# Scripted model pull for the office machine (plan section 15: the local LLM
# must be a scripted step, not folklore). Run once per machine.
#
# Usage: scripts\pull_models.ps1 [-ChatModel qwen3:8b] [-EmbedModel nomic-embed-text]
[CmdletBinding()]
param(
    [string]$ChatModel = 'qwen3:8b',
    [string]$EmbedModel = 'nomic-embed-text'
)

$ErrorActionPreference = 'Stop'

foreach ($model in @($ChatModel, $EmbedModel)) {
    Write-Host "pulling $model ..."
    ollama pull $model
    if ($LASTEXITCODE -ne 0) { throw "ollama pull $model failed - is Ollama running?" }
}

Write-Host ''
Write-Host 'installed models:'
ollama list
Write-Host ''
Write-Host "now set in .env:"
Write-Host "  PPM_CHAT_MODEL=$ChatModel"
Write-Host "  PPM_EMBED_MODEL=$EmbedModel"
Write-Host "  PPM_EMBED_DIM=768   (nomic-embed-text is 768-dimensional)"
Write-Host 'and verify with: .venv\Scripts\ppm.exe models check'
