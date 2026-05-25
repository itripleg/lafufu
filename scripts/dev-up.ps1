#!/usr/bin/env pwsh
# Brings up the local dev stack (NATS + Ollama) and pulls the LLM model the
# agent expects. Idempotent — safe to re-run.
#
# Defaults to qwen2.5:1.5b to match what the Pi runs. Pass -Model to override.
#
# Usage:
#     ./scripts/dev-up.ps1
#     ./scripts/dev-up.ps1 -Model qwen2.5:7b

param(
    [string]$Model = "qwen2.5:1.5b"
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot ".." "docker-compose.dev.yml"

Write-Host "Bringing up dev stack (NATS + Ollama)..." -ForegroundColor Cyan
docker compose -f $composeFile up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

Write-Host "Waiting for Ollama to become healthy..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:11434/" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw "Ollama didn't come up after 60s" }

Write-Host "Pulling LLM: $Model" -ForegroundColor Cyan
docker compose -f $composeFile exec -T ollama ollama pull $Model
if ($LASTEXITCODE -ne 0) { throw "ollama pull failed" }

Write-Host ""
Write-Host "Dev stack ready." -ForegroundColor Green
Write-Host "  NATS:    nats://localhost:4222"
Write-Host "  Ollama:  http://localhost:11434"
Write-Host ""
Write-Host "Next: see docs/local-dev.md for the run order"
