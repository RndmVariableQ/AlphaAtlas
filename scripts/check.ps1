$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'ruff check failed' }
    uv run ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'ruff format failed' }
    uv run python scripts/operator_catalog.py --check
    if ($LASTEXITCODE -ne 0) { throw 'operator catalog check failed' }
    uv run pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'pytest failed' }
} finally { Pop-Location }
