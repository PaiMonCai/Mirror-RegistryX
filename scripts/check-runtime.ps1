$ErrorActionPreference = "Stop"

python scripts\verify.py
python -m py_compile scripts\verify.py scripts\reset-admin-password.py sync\sync.py ops_agent\agent.py mirror_registry_core\ops_agent.py panel\main.py panel\password_reset.py tests\test_panel.py tests\test_sync.py tests\test_ops_agent.py

if (Get-Command npm.cmd -ErrorAction SilentlyContinue) {
    npm.cmd --prefix panel run typecheck
    npm.cmd run build
} else {
    Write-Warning "npm.cmd is not available. Skipping frontend build."
}

if (Get-Command pytest -ErrorAction SilentlyContinue) {
    python -m pytest
} else {
    Write-Warning "pytest is not installed. Install development dependencies with: python -m pip install -r requirements-dev.txt"
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker compose config
    docker compose -f docker-compose.dev.yml config
} else {
    Write-Warning "Docker CLI is not available. Skipping docker compose config."
}
