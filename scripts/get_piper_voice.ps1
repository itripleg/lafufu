#!/usr/bin/env pwsh
# Downloads a Piper TTS voice from rhasspy/piper-voices into ./models/.
# Both the .onnx and the .onnx.json config are required by piper-tts.
#
# Default voice (en_US-amy-medium) is a small, fast, well-known test voice.
# Override with -Voice for something more oracle-y like
# en_GB-northern_english_male-medium.
#
# After running, set:  $env:LAFUFU_PIPER_MODEL = (Resolve-Path ./models/<voice>.onnx)
#
# Usage:
#     ./scripts/get_piper_voice.ps1
#     ./scripts/get_piper_voice.ps1 -Voice en_US-lessac-medium

param(
    [string]$Voice = "en_US-amy-medium",
    [string]$OutDir = "models"
)

$ErrorActionPreference = "Stop"

# voice names follow "<lang>_<region>-<speaker>-<quality>" — split into HF path
$lang, $speaker, $quality = $Voice -split "-"
$langGroup = $lang.Substring(0, 2)
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/$langGroup/$lang/$speaker/$quality"

$outDirPath = Join-Path (Get-Location) $OutDir
New-Item -ItemType Directory -Force -Path $outDirPath | Out-Null

$onnx = Join-Path $outDirPath "$Voice.onnx"
$json = Join-Path $outDirPath "$Voice.onnx.json"

if (-not (Test-Path $onnx)) {
    Write-Host "Downloading $Voice.onnx ..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri "$base/$Voice.onnx" -OutFile $onnx -UseBasicParsing
} else {
    Write-Host "Already present: $onnx"
}

if (-not (Test-Path $json)) {
    Write-Host "Downloading $Voice.onnx.json ..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri "$base/$Voice.onnx.json" -OutFile $json -UseBasicParsing
}

Write-Host ""
Write-Host "Voice ready at $onnx" -ForegroundColor Green
Write-Host 'Set in your agent shell:'
Write-Host "  `$env:LAFUFU_PIPER_MODEL = `"$onnx`""
