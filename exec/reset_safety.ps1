param(
    [string]$RpiHost = "192.168.100.249",
    [string]$RpiUser = "rpi103",
    [string]$ComposeDirectory = "/home/rpi103/rpi-ros2-humble",
    [string]$ComposeService = "ros2"
)

$ErrorActionPreference = "Stop"
$sshTarget = "${RpiUser}@${RpiHost}"

$remoteScript = @'
set -e

compose_directory="$1"
compose_service="$2"

cd "$compose_directory"
docker compose exec -T "$compose_service" bash -lc '
  source /opt/ros/humble/setup.bash
  source /thing_ws/install/setup.bash
  ros2 service call /thing/reset_safety std_srvs/srv/Trigger
'
'@

Write-Host "Requesting safety reset through $sshTarget..."
# Base64 preserves the LF-normalized Bash source across the Windows native pipeline.
$linuxRemoteScript = $remoteScript -replace "`r", ""
$remoteScriptBytes = [System.Text.Encoding]::UTF8.GetBytes($linuxRemoteScript)
$encodedRemoteScript = [System.Convert]::ToBase64String($remoteScriptBytes)
$encodedRemoteScript | ssh $sshTarget "tr -d '\r\n' | base64 -d | bash -s -- '$ComposeDirectory' '$ComposeService'"

if ($LASTEXITCODE -ne 0) {
    throw "Safety reset request failed with exit code $LASTEXITCODE."
}
