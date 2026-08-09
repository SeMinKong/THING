param(
    [string]$RpiHost = "192.168.100.249",
    [string]$RpiUser = "rpi103",
    [string]$ComposeDirectory = "/home/rpi103/rpi-ros2-humble",
    [string]$ComposeService = "ros2"
)

$ErrorActionPreference = "Stop"
$sshTarget = "${RpiUser}@${RpiHost}"
$remoteSessionPath = "/tmp/thing-rpi-demo-session.sh"
$sessionScriptPath = Join-Path $PSScriptRoot "rpi_demo_session.sh"
$terminalScriptPath = Join-Path $PSScriptRoot "rpi_demo_terminal.ps1"

Write-Host "Uploading the foreground session script to $sshTarget..."
scp $sessionScriptPath "${sshTarget}:${remoteSessionPath}"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upload the remote demo session script (exit code $LASTEXITCODE)."
}

$terminalArguments = @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$terminalScriptPath`"",
    "-RpiHost", "`"$RpiHost`"",
    "-RpiUser", "`"$RpiUser`"",
    "-ComposeDirectory", "`"$ComposeDirectory`"",
    "-ComposeService", "`"$ComposeService`"",
    "-RemoteSessionPath", "`"$remoteSessionPath`""
)

Start-Process -FilePath "powershell.exe" -ArgumentList $terminalArguments
Write-Host "Opened the Raspberry Pi demo in a separate PowerShell window."
Write-Host "Press Ctrl+C in that window to stop both ROS 2 launch processes."
