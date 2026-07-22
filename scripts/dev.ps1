param(
    [ValidateSet("up", "down", "status", "logs")]
    [string]$Action = "up"
)

$ErrorActionPreference = "Stop"
$ComposeFile = Join-Path $PSScriptRoot "..\infra\compose\docker-compose.yml"
$VerifyPhase0 = Join-Path $PSScriptRoot "verify_phase0.py"
$VerifyPhase1 = Join-Path $PSScriptRoot "verify_phase1.py"

switch ($Action) {
    "up" {
        docker compose -f $ComposeFile up -d --build --wait
        uv run python $VerifyPhase0
        uv run python $VerifyPhase1
    }
    "down" {
        docker compose -f $ComposeFile down
    }
    "status" {
        docker compose -f $ComposeFile ps
    }
    "logs" {
        docker compose -f $ComposeFile logs --follow --tail 200
    }
}
