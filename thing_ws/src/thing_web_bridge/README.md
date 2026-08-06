# thing_web_bridge

`thing_web_bridge` exposes the ROS 2 robot state and the permitted control
interfaces to the browser. It does not publish directly to actuator command
topics; all browser commands pass through the existing ROS service/action
validation path.

## Endpoint

- WebSocket: `ws://<jetson-host>:8000/ws/robot-state`
- State snapshot period: 200 ms (5 Hz)
- MJPEG: `http://<jetson-host>:8080/stream.mjpg`

Every snapshot carries the eight top-level fields fixed by requirement 6.4:
`timestamp`, `mode`, `recording_state`, `landmarks`, `motor_state`,
`safety_state`, `control_state`, and `recording`. It also adds
`last_hand_command` and `connection_status` for the current web UI.

`control_state` and `recording` carry the frozen `.msg` schema verbatim, so
their enums stay integers and no derived field is attached. Only `landmarks`,
`motor_state`, and `safety_state` are display objects with symbolic enums and
derived values. Top-level `mode` and `recording_state` are symbolic mirrors of
the verbatim sections and always agree with them.

ROS `uint64` session IDs are encoded as decimal strings so JavaScript does not
lose precision; no active session is `"0"`.

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

### Requirements

`websockets` **10.0 or newer** is required. Version 9.1 passes a removed `loop`
argument to `asyncio.Lock()` and `asyncio.sleep()`, which raises `TypeError` on
Python 3.10. The node still binds its port, so the failure only shows up when a
browser connects and the connection is reset immediately.

```bash
python3 -c "import websockets; print(websockets.__version__)"   # must be >= 10
```

Ubuntu 22.04 (Jetson JetPack 6.2) ships 10.1 via `apt install python3-websockets`,
which satisfies this. Older hosts need `pip3 install --user 'websockets>=10.4'`.
Pin the version in the Jetson container image as well.

### Start

```bash
cd thing_ws
colcon build --packages-select thing_web_bridge
source install/setup.bash
ros2 launch thing_bringup web_bridge.launch.py
```

This starts `mjpeg_streamer` and `web_bridge_node` together. The vision
pipeline runs separately via `vision.launch.py`.

To run only the bridge:

```bash
ros2 run thing_web_bridge web_bridge_node
```

The bind address, port, snapshot period, and ROS request timeout are configured
under `web_bridge_node` in `thing_bringup/config/vision.yaml`:

| Parameter | Default |
| --- | --- |
| `bind_address` | `0.0.0.0` |
| `port` | `8000` |
| `snapshot_period_ms` | `200` |
| `service_timeout_ms` | `2000` |

Startup is confirmed by this log line:

```
[INFO] [web_bridge_node]: Web Bridge listening on ws://0.0.0.0:8000/ws/robot-state
```

### Test

```bash
cd thing_ws
colcon test --packages-select thing_web_bridge
colcon test-result --verbose
```

`test_interface_contract.py` compares the JSON symbol tables against the real
`thing_interfaces` constants and is skipped when the workspace is not sourced.

## Contract

The frozen browser-facing contract is
[web/docs/interfaces-bridge.md](../../../web/docs/interfaces-bridge.md). The ROS 2
side is `docs/interfaces.md`.
