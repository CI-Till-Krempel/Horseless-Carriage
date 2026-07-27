# Extends the official Ollama image so the configured model is pulled
# automatically on first start, instead of requiring a manual
# `docker exec ... ollama pull <model>` step. Used by docker-compose.local.yaml
# for the fully-local (no commercial LLM) stack.
FROM ollama/ollama:latest

COPY ollama-entrypoint.sh /ollama-entrypoint.sh
RUN chmod +x /ollama-entrypoint.sh

ENTRYPOINT ["/ollama-entrypoint.sh"]
