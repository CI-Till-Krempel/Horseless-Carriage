# Extends the official Ollama image so the configured model is pulled
# automatically on first start, instead of requiring a manual
# `docker exec ... ollama pull <model>` step. Used by docker-compose.local.yaml
# for the fully-local (no commercial LLM) stack.
FROM ollama/ollama:latest

COPY ollama-entrypoint.sh /ollama-entrypoint.sh
# Strip any CRLF line endings before chmod: on Windows, git's default
# core.autocrlf=true checks this file out with CRLF, which corrupts the
# "#!/bin/sh" shebang into "#!/bin/sh\r" - the kernel then fails to find
# that (nonexistent) interpreter and refuses to exec the script at all
# ("exec /ollama-entrypoint.sh: no such file or directory"), restarting
# forever under restart: unless-stopped. Normalizing here fixes it
# unconditionally, regardless of the checkout's line endings (see also
# .gitattributes, which prevents CRLF from being checked out here at all
# on a fresh clone - this still covers an already-cloned working tree).
RUN sed -i 's/\r$//' /ollama-entrypoint.sh && chmod +x /ollama-entrypoint.sh

ENTRYPOINT ["/ollama-entrypoint.sh"]
