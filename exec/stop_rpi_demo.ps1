param(
    [string]$RpiHost = "192.168.100.249",
    [string]$RpiUser = "rpi103",
    [string]$ComposeDirectory = "/home/rpi103/rpi-ros2-humble",
    [string]$ComposeService = "ros2"
)

$ErrorActionPreference = "Stop"
$sshTarget = "${RpiUser}@${RpiHost}"

$remoteScript = @'
set -eu

compose_directory="$1"
compose_service="$2"

cd "$compose_directory"

is_running() {
  launch_file="$1"
  docker compose exec -T "$compose_service" bash -lc \
    "pgrep -f '[r]os2 launch thing_bringup ${launch_file}' >/dev/null" \
    </dev/null
}

stop_launch() {
  launch_file="$1"

  if ! is_running "$launch_file"; then
    echo "      Already stopped: ${launch_file}"
    return
  fi

  docker compose exec -T "$compose_service" bash -lc \
    "pkill -INT -f '[r]os2 launch thing_bringup ${launch_file}' || true" \
    </dev/null

  attempts=0
  while is_running "$launch_file" && [ "$attempts" -lt 10 ]; do
    sleep 1
    attempts=$((attempts + 1))
  done

  if is_running "$launch_file"; then
    echo "      Graceful stop timed out; forcing: ${launch_file}"
    docker compose exec -T "$compose_service" bash -lc \
      "pkill -KILL -f '[r]os2 launch thing_bringup ${launch_file}' || true" \
      </dev/null
  else
    echo "      Stopped: ${launch_file}"
  fi
}

echo "[1/3] Stopping the motor driver..."
stop_launch "thing_bringup.launch.py"

echo "[2/3] Stopping the control stack..."
stop_launch "control.launch.py"

echo "[3/3] Checking remaining ROS 2 launch processes..."
docker compose exec -T "$compose_service" bash -lc \
  "pgrep -af '[r]os2 launch thing_bringup' || true" \
  </dev/null

echo
echo "Demo launch processes stopped. The ROS 2 container is still running."
'@

Write-Host "Connecting to $sshTarget..."
$linuxRemoteScript = $remoteScript -replace "`r", ""
$remoteScriptBytes = [System.Text.Encoding]::UTF8.GetBytes($linuxRemoteScript)
$encodedRemoteScript = [System.Convert]::ToBase64String($remoteScriptBytes)
$encodedRemoteScript | ssh $sshTarget "tr -d '\r\n' | base64 -d | bash -s -- '$ComposeDirectory' '$ComposeService'"

if ($LASTEXITCODE -ne 0) {
    throw "Remote demo shutdown failed with exit code $LASTEXITCODE."
}
