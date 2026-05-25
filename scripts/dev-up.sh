#!/usr/bin/env bash
# Brings up the local dev stack (NATS + Ollama) and pulls the LLM model the
# agent expects. Idempotent.
#
# Defaults to qwen2.5:1.5b to match the Pi. Override with LAFUFU_DEV_MODEL.

set -euo pipefail

MODEL="${LAFUFU_DEV_MODEL:-qwen2.5:1.5b}"
COMPOSE="$(dirname "$0")/../docker-compose.dev.yml"

echo "Bringing up dev stack (NATS + Ollama)..."
docker compose -f "$COMPOSE" up -d

echo "Waiting for Ollama to become healthy..."
for _ in $(seq 1 60); do
    if curl -fsS http://localhost:11434/ >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
[ "${ready:-0}" = "1" ] || { echo "Ollama didn't come up after 60s"; exit 1; }

echo "Pulling LLM: $MODEL"
docker compose -f "$COMPOSE" exec -T ollama ollama pull "$MODEL"

echo ""
echo "Dev stack ready."
echo "  NATS:    nats://localhost:4222"
echo "  Ollama:  http://localhost:11434"
echo ""
echo "Next: see docs/local-dev.md for the run order"
