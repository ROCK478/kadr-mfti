$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
docker compose build app
if ($LASTEXITCODE -ne 0) { throw "Docker build failed" }
if (!(Test-Path "models/Qwen3-4B-Q4_K_M.gguf")) {
    New-Item -ItemType Directory -Force models | Out-Null
    docker compose --profile setup run --rm setup-model
    if ($LASTEXITCODE -ne 0) { throw "Model download failed" }
}
if (!(Test-Path "models/speech/vosk-model-small-ru-0.22/conf/model.conf") -or !(Test-Path "models/speech/ru_RU-denis-medium.onnx") -or !(Test-Path "models/speech/ru_RU-denis-medium.onnx.json")) {
    docker compose --profile setup run --rm setup-speech
    if ($LASTEXITCODE -ne 0) { throw "Speech model download failed" }
}
docker compose up -d --wait app
if ($LASTEXITCODE -ne 0) { throw "Application did not start" }
Write-Host "Open http://localhost:8080 ; Stop: docker compose down"
