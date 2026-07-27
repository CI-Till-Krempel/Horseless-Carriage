#!/bin/sh
set -e

# Model tag for the "local-ollama" template
# (config/model-templates/litellm.local-ollama.yaml). Keep this in sync with
# the `model:` lines in that file (this var holds the bare Ollama tag,
# without the "ollama/" provider prefix litellm uses).
MODEL="${OLLAMA_MODEL:-llama3.1:8b}"

ollama serve &
SERVE_PID=$!

echo "Waiting for Ollama to start..."
until ollama list >/dev/null 2>&1; do
  sleep 1
done

if ollama list | grep -q "^${MODEL}"; then
  echo "Model ${MODEL} already present."
else
  echo "Pulling model ${MODEL} (first run only, cached in the ollama_data volume afterwards)..."
  ollama pull "${MODEL}"
fi

wait "$SERVE_PID"
