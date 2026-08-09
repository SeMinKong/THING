param(
    [Parameter(Mandatory = $true)][string]$RpiHost,
    [Parameter(Mandatory = $true)][string]$RpiUser,
    [Parameter(Mandatory = $true)][string]$ComposeDirectory,
    [Parameter(Mandatory = $true)][string]$ComposeService,
    [Parameter(Mandatory = $true)][string]$RemoteSessionPath
)

$ErrorActionPreference = "Stop"
$sshTarget = "${RpiUser}@${RpiHost}"

Write-Host "Connecting to $sshTarget..."
Write-Host "Keep this window open. Press Ctrl+C here to stop the demo." -ForegroundColor Yellow
Write-Host ""

ssh -tt $sshTarget "bash '$RemoteSessionPath' '$ComposeDirectory' '$ComposeService'"
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host "Demo stopped normally." -ForegroundColor Green
} elseif ($exitCode -eq 130) {
    Write-Host "Demo stopped by Ctrl+C." -ForegroundColor Yellow
} else {
    Write-Host "Demo session ended with exit code $exitCode." -ForegroundColor Red
}
