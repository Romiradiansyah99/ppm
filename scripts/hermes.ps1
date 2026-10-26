param(
    [Parameter(Position = 0)]
    [ValidateSet("init", "run", "test", "up", "down", "logs")]
    [string]$Action = "run",
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv-hermes\Scripts\python.exe"
$Streamlit = Join-Path $Root ".venv-hermes\Scripts\streamlit.exe"

function Ensure-Env {
    if (-not (Test-Path $Python)) {
        python -m venv (Join-Path $Root ".venv-hermes")
    }
    & $Python -m pip install -r (Join-Path $Root "requirements.txt")
    & $Python -m pip install -e $Root
}

switch ($Action) {
    "init" { Ensure-Env }
    "run" {
        if (-not (Test-Path $Streamlit)) { Ensure-Env }
        & $Streamlit run (Join-Path $Root "app\main.py") --server.port $Port
    }
    "test" {
        if (-not (Test-Path $Python)) { Ensure-Env }
        Push-Location $Root
        try { & $Python -m pytest -q } finally { Pop-Location }
    }
    "up" { docker compose up -d --build }
    "down" { docker compose down }
    "logs" { docker compose logs -f --tail=100 }
}
