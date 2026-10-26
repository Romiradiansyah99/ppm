param(
    [string]$ChatModel = "qwen2.5:3b",
    [string]$EmbedModel = "nomic-embed-text"
)

$ErrorActionPreference = "Stop"

Write-Host "pulling $EmbedModel"
ollama pull $EmbedModel
if ($LASTEXITCODE -ne 0) { throw "ollama pull $EmbedModel failed" }

Write-Host "pulling $ChatModel"
ollama pull $ChatModel
if ($LASTEXITCODE -ne 0) { throw "ollama pull $ChatModel failed" }

Write-Host "models ready"
