import lib_env


class TestIsPlaceholder:
    def test_empty_string_is_placeholder(self):
        assert lib_env.is_placeholder("") is True

    def test_angle_bracket_value_is_placeholder(self):
        assert lib_env.is_placeholder("<your_api_key>") is True

    def test_real_value_is_not_placeholder(self):
        assert lib_env.is_placeholder("sk-abc123") is False

    def test_value_containing_angle_brackets_but_not_wrapping_is_not_placeholder(self):
        assert lib_env.is_placeholder("a<b>c") is False


class TestReadWriteEnvVar:
    def test_read_missing_file_returns_empty_string(self, tmp_path):
        assert lib_env.read_env_var(tmp_path / "nope.env", "FOO") == ""

    def test_read_missing_key_returns_empty_string(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('OTHER="x"\n')
        assert lib_env.read_env_var(env_file, "FOO") == ""

    def test_write_then_read_round_trip(self, tmp_path):
        env_file = tmp_path / ".env"
        lib_env.update_env_var(env_file, "GOOGLE_API_KEY", "abc123XYZ")
        assert lib_env.read_env_var(env_file, "GOOGLE_API_KEY") == "abc123XYZ"

    def test_write_preserves_special_characters(self, tmp_path):
        env_file = tmp_path / ".env"
        value = "newvalue/with+special=chars"
        lib_env.update_env_var(env_file, "GOOGLE_API_KEY", value)
        assert lib_env.read_env_var(env_file, "GOOGLE_API_KEY") == value

    def test_overwriting_existing_key_replaces_in_place(self, tmp_path):
        env_file = tmp_path / ".env"
        lib_env.update_env_var(env_file, "KEY", "first")
        lib_env.update_env_var(env_file, "KEY", "second")
        text = env_file.read_text()
        assert text.count("KEY=") == 1
        assert lib_env.read_env_var(env_file, "KEY") == "second"

    def test_writing_new_key_preserves_other_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('# a comment\nEXISTING="keep-me"\n')
        lib_env.update_env_var(env_file, "NEW_KEY", "value")
        text = env_file.read_text()
        assert "# a comment" in text
        assert lib_env.read_env_var(env_file, "EXISTING") == "keep-me"
        assert lib_env.read_env_var(env_file, "NEW_KEY") == "value"

    def test_appended_line_does_not_run_into_previous_line(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('FIRST="no-trailing-newline"')  # deliberately no \n
        lib_env.update_env_var(env_file, "SECOND", "value")
        lines = env_file.read_text().splitlines()
        assert lines == ['FIRST="no-trailing-newline"', "SECOND='value'"]

    def test_overwriting_existing_key_with_windows_path_does_not_crash(self, tmp_path):
        """
        Regression test (GH issue #31): a Windows path like
        "C:\\Users\\..." contains "\\U", which re.sub() misparses as an
        invalid backreference escape when the replacement is given as a
        string, raising `re.PatternError: bad escape \\U` - this only
        showed up when the key already existed (the replace path, not the
        append path), which is why setup_llm.py crashed on a second
        _setup_state_repo() write, not the first.
        """
        env_file = tmp_path / ".env"
        lib_env.update_env_var(env_file, "STATE_REPO_PATH", "old-value")
        windows_path = r"C:\Users\till\Documents\GitHub\heinzelmann"
        lib_env.update_env_var(env_file, "STATE_REPO_PATH", windows_path)
        assert lib_env.read_env_var(env_file, "STATE_REPO_PATH") == windows_path

    def test_windows_path_is_written_single_quoted_not_double_quoted(self, tmp_path):
        """
        Regression test (GH issue #34): Docker Compose's own .env parser
        (used to interpolate ${STATE_REPO_PATH} into docker-compose.yaml's
        volume mount) applies C-style backslash-escape processing to
        DOUBLE-quoted values only - "C:\\Users\\till\\..." silently
        corrupted (\\t became an actual tab character, breaking the volume
        mount with "The filename, directory name, or volume label syntax
        is incorrect") even though our own read_env_var() round-tripped it
        fine. Single-quoted values are untouched by Compose's escaping, so
        update_env_var() must write single quotes, not double.
        """
        env_file = tmp_path / ".env"
        windows_path = r"C:\Users\till\Documents\GitHub\heinzelmann"
        lib_env.update_env_var(env_file, "STATE_REPO_PATH", windows_path)
        text = env_file.read_text()
        assert f"STATE_REPO_PATH='{windows_path}'" in text
        assert '"' not in text

    def test_embedded_single_quote_round_trips(self, tmp_path):
        """
        Regression test (GH issue #122): an ordinary Git user name like
        "O'Brien" written unescaped into a single-quoted value
        (GIT_USER_NAME='O'Brien') isn't just mis-parsed by Docker Compose's
        own .env parser (which also reads this file directly, e.g.
        GIT_USER_NAME=${GIT_USER_NAME} in docker-compose.yaml) - confirmed
        via `docker compose config` that it fails outright with
        "unexpected character '\\'' in variable name". Compose does
        recognize a backslash-escaped quote (`\\'`) inside single quotes as
        a literal quote, so update_env_var must write that escape and
        read_env_var/load_env_file must reverse it.
        """
        env_file = tmp_path / ".env"
        value = "O'Brien"
        lib_env.update_env_var(env_file, "GIT_USER_NAME", value)
        text = env_file.read_text()
        assert "GIT_USER_NAME='O\\'Brien'" in text
        assert lib_env.read_env_var(env_file, "GIT_USER_NAME") == value

    def test_multiple_embedded_quotes_round_trip(self, tmp_path):
        env_file = tmp_path / ".env"
        value = "'quoted' 'again'"
        lib_env.update_env_var(env_file, "KEY", value)
        assert lib_env.read_env_var(env_file, "KEY") == value

    def test_windows_path_still_round_trips_after_quote_escaping_change(self, tmp_path):
        """A literal backslash not adjacent to a quote must stay untouched -
        only embedded `'` characters get the `\\'` escape."""
        env_file = tmp_path / ".env"
        windows_path = r"C:\Users\till\Documents\GitHub\heinzelmann"
        lib_env.update_env_var(env_file, "STATE_REPO_PATH", windows_path)
        assert lib_env.read_env_var(env_file, "STATE_REPO_PATH") == windows_path


class TestGenSecret:
    def test_returns_a_reasonably_long_hex_string(self):
        secret = lib_env.gen_secret()
        assert len(secret) == 48  # secrets.token_hex(24) -> 48 hex chars
        int(secret, 16)  # raises ValueError if not valid hex

    def test_two_calls_differ(self):
        assert lib_env.gen_secret() != lib_env.gen_secret()


class TestEnsureMasterKey:
    def test_generates_key_when_missing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        lib_env.ensure_master_key(env_file)
        master = lib_env.read_env_var(env_file, "LITELLM_MASTER_KEY")
        proxy = lib_env.read_env_var(env_file, "LITELLM_PROXY_API_KEY")
        assert master and not lib_env.is_placeholder(master)
        assert proxy == master

    def test_generates_key_when_placeholder(self, tmp_path):
        env_file = tmp_path / ".env"
        lib_env.update_env_var(env_file, "LITELLM_MASTER_KEY", "<your_master_key>")
        lib_env.ensure_master_key(env_file)
        master = lib_env.read_env_var(env_file, "LITELLM_MASTER_KEY")
        assert not lib_env.is_placeholder(master)

    def test_leaves_existing_real_key_untouched(self, tmp_path):
        env_file = tmp_path / ".env"
        lib_env.update_env_var(env_file, "LITELLM_MASTER_KEY", "already-real-value")
        lib_env.ensure_master_key(env_file)
        assert lib_env.read_env_var(env_file, "LITELLM_MASTER_KEY") == "already-real-value"


class TestLoadEnvFile:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert lib_env.load_env_file(tmp_path / "nope.env") == {}

    def test_parses_quoted_and_unquoted_values(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            '# a comment\n'
            '\n'
            'QUOTED="hello world"\n'
            "SINGLE='single quoted'\n"
            "UNQUOTED=plainvalue\n"
        )
        result = lib_env.load_env_file(env_file)
        assert result == {
            "QUOTED": "hello world",
            "SINGLE": "single quoted",
            "UNQUOTED": "plainvalue",
        }

    def test_skips_comments_and_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment=notavalue\n\nKEY=value\n")
        result = lib_env.load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_unescapes_embedded_single_quote(self, tmp_path):
        """GH issue #122: must mirror update_env_var's `'` -> `\\'` escaping."""
        env_file = tmp_path / ".env"
        env_file.write_text("GIT_USER_NAME='O\\'Brien'\n")
        result = lib_env.load_env_file(env_file)
        assert result == {"GIT_USER_NAME": "O'Brien"}
