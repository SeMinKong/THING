#!/usr/bin/env bash
set -eu

compose_directory="$1"
compose_service="$2"
stopping=0

cd "$compose_directory"

is_running() {
  local launch_file="$1"
  docker compose exec -T "$compose_service" bash -lc \
    "pgrep -f '[r]os2 launch thing_bringup ${launch_file}' >/dev/null" \
    </dev/null
}

signal_launch() {
  local launch_file="$1"
  docker compose exec -T "$compose_service" bash -lc \
    "pkill -INT -f '[r]os2 launch thing_bringup ${launch_file}' || true" \
    </dev/null
}

stop_demo() {
  local exit_code="${1:-0}"
  if [ "$stopping" -eq 1 ]; then
    return
  fi
  stopping=1
  trap - INT TERM HUP EXIT

  echo
  echo "Stopping the motor driver..."
  signal_launch "thing_bringup.launch.py"
  echo "Stopping the control stack..."
  signal_launch "control.launch.py"

  local attempts=0
  while [ "$attempts" -lt 10 ]; do
    if ! is_running "thing_bringup.launch.py" && ! is_running "control.launch.py"; then
      echo "All ROS 2 launch processes stopped."
      exit "$exit_code"
    fi
    sleep 1
    attempts=$((attempts + 1))
  done

  echo "Some launch processes did not stop within 10 seconds." >&2
  echo "No SIGKILL was sent; inspect the Raspberry Pi before forcing shutdown." >&2
  exit 1
}

echo "[1/3] Starting the ROS 2 container..."
docker compose up -d "$compose_service" </dev/null

if is_running "control.launch.py" || is_running "thing_bringup.launch.py"; then
  echo "A demo launch process is already running." >&2
  echo "Stop the existing session before starting a foreground session." >&2
  exit 1
fi

trap 'stop_demo 130' INT TERM HUP
trap 'stop_demo $?' EXIT

echo "[2/3] Starting the control stack..."
docker compose exec -T "$compose_service" bash -lc \
  "source /opt/ros/humble/setup.bash && source /thing_ws/install/setup.bash && exec ros2 launch thing_bringup control.launch.py" \
  </dev/null &
control_client_pid=$!

sleep 2
if ! kill -0 "$control_client_pid" 2>/dev/null || ! is_running "control.launch.py"; then
  echo "The control stack failed to start." >&2
  exit 1
fi

echo "[3/3] Starting the motor driver..."
docker compose exec -T "$compose_service" bash -lc \
  "source /opt/ros/humble/setup.bash && source /thing_ws/install/setup.bash && exec ros2 launch thing_bringup thing_bringup.launch.py" \
  </dev/null &
driver_client_pid=$!

sleep 2
if ! kill -0 "$driver_client_pid" 2>/dev/null || ! is_running "thing_bringup.launch.py"; then
  echo "The motor driver failed to start." >&2
  exit 1
fi

echo
echo "Demo is running in the foreground. Press Ctrl+C to stop it."

while kill -0 "$control_client_pid" 2>/dev/null && kill -0 "$driver_client_pid" 2>/dev/null; do
  sleep 1
done

echo "A ROS 2 launch process exited unexpectedly." >&2
exit 1
