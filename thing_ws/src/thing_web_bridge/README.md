# thing_web_bridge

`thing_web_bridge` exposes the ROS 2 robot state and the permitted control
interfaces to the browser. It does not publish directly to actuator command
topics; all browser commands pass through the existing ROS service/action
validation path.

## Endpoint

- WebSocket: `ws://<jetson-host>:8000/ws/robot-state`
- State snapshot period: 200 ms (5 Hz)
- MJPEG: `http://<jetson-host>:8080/stream.mjpg`

Each snapshot always contains the requirement fields `timestamp`, `mode`,
`recording_state`, `landmarks`, `motor_state`, and `safety_state`. The bridge
also emits `control_state`, `recording`, and `last_hand_command` for the current
web UI. ROS `uint64` session IDs are encoded as decimal strings so JavaScript
does not lose precision.

## Browser request contract

Requests use this envelope:

```json
{
  "request_id": "unique-id",
  "type": "set_control_mode",
  "timestamp": "2026-08-04T12:00:00Z",
  "payload": {
    "requested_mode": "MIMIC",
    "requested_owner": "WEB"
  }
}
```

Supported types are `set_control_mode`, `stop`, `execute_gesture`,
`execute_sequence`, `start_recording`, `stop_recording`, `set_mimic_result`,
and `reset_safety`. Invalid enums, unknown fields, unsafe numeric values, and
malformed session IDs are rejected before a ROS call is made.

Every request receives one acknowledgement with the same `request_id`:

```json
{
  "request_id": "unique-id",
  "type": "ack",
  "timestamp": "2026-08-04T12:00:00Z",
  "accepted": true,
  "reason": "accepted"
}
```

The server keeps no command queue, so reconnecting a browser does not replay
old requests.

## Run

Install the Ubuntu package `python3-websockets`, build the workspace, then run:

```bash
ros2 launch thing_bringup vision.launch.py
```

The bind address, port, snapshot period, and ROS request timeout are configured
under `web_bridge_node` in `thing_bringup/config/vision.yaml`.
