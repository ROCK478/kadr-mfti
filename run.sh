#!/bin/sh
set -eu
cd "$(dirname "$0")"
docker compose build app
if [ ! -f models/Qwen3-4B-Q4_K_M.gguf ]; then
  mkdir -p models
  docker compose --profile setup run --rm setup-model
fi
if [ ! -f models/speech/vosk-model-small-ru-0.22/conf/model.conf ] || [ ! -f models/speech/ru_RU-denis-medium.onnx ] || [ ! -f models/speech/ru_RU-denis-medium.onnx.json ]; then
  docker compose --profile setup run --rm setup-speech
fi
docker compose up -d --wait app
echo "Открыть: http://localhost:${APP_PORT:-8080}"
echo "Остановить: docker compose down"
