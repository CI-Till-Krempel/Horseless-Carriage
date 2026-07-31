"""Retro startup banner shown by run.py before it hands off to Docker Compose."""

from pathlib import Path

BANNER = r"""
========================================================
 #   #  ###  ####   #### ##### #     #####  ####  ####
 #   # #   # #   # #     #     #     #     #     #
 ##### #   # ####   ###  ###   #     ###    ###   ###
 #   # #   # #  #      # #     #     #         #     #
 #   #  ###  #   # ####  ##### ##### ##### ####  ####

  ###   ###  ####  ####  #####  ###   ###  #####
 #   # #   # #   # #   #   #   #   # #     #
 #     ##### ####  ####    #   ##### #  ## ###
 #   # #   # #  #  #  #    #   #   # #   # #
  ###  #   # #   # #   # ##### #   #  ###  #####

                     ______
                    /|_||_\`.__          a multi-agent Scrum team,
                   (   _    _ _\          steaming since 2026
                    `--(_)--(_)-'
========================================================
"""


def version() -> str:
    try:
        return Path(__file__).resolve().parent.joinpath("VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def print_banner() -> None:
    """No-op if stdout isn't a real terminal (e.g. piped into a log file or
    CI) - the banner is decorative, not information a script should have to
    parse."""
    import sys
    if not sys.stdout.isatty():
        return
    print(BANNER)
    print(f"                              v{version()}")
    print()
