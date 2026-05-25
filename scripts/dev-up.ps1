#!/usr/bin/env pwsh
# Brings up the local dev stack (NATS + Ollama) and pulls the LLM model the
# agent expects. Idempotent — safe to re-run.
#
# Defaults to qwen2.5:1.5b — light enough for laptop CPU and matches what the
# Pi runs at runtime (the systemd default is 7b; admin UI swaps to 1.5b via
# the agent.llm_model setting). Pass -Model to override.
#
# Usage:
#     ./scripts/dev-up.ps1
#     ./scripts/dev-up.ps1 -Model qwen2.5:7b

param(
    [string]$Model = "qwen2.5:1.5b"
)

# NOTE: deliberately NOT setting $ErrorActionPreference = "Stop". On Windows
# PowerShell 5.1, native commands' stderr writes (e.g. docker pull progress)
# are wrapped in NativeCommandError records and would terminate the script
# under Stop. We check $LASTEXITCODE explicitly after each native call instead.
#
# Single-arg Join-Path so it works on Windows PowerShell 5.1 too (5.1 doesn't
# support multiple ChildPath args; that's PS 6+).
$composeFile = Join-Path $PSScriptRoot (Join-Path ".." "docker-compose.dev.yml")

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
