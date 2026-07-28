from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


COMM_SUCCESS = 0


class FakePortHandler:
    opened_count = 0

    def __init__(self, device: str) -> None:
        self.device = device
        self.opened = False

    def openPort(self) -> bool:
        self.opened = True
        FakePortHandler.opened_count += 1
        return True

    def setBaudRate(self, _baudrate: int) -> bool:
        return True

    def closePort(self) -> None:
        self.opened = False


class FakePacketHandler:
    def __init__(self, _protocol: float) -> None:
        self.registers: dict[tuple[str, int, int], int] = {}
        self.writes: list[tuple[int, int, int]] = []

    @staticmethod
    def _key(port: FakePortHandler, motor_id: int, address: int):
        return (port.device, motor_id, address)

    def default_value(self, address: int) -> int:
        return {
            38: 1750,
            70: 0,
            126: 0,
            128: 0,
            132: 2048,
            144: 50,
            146: 25,
        }.get(address, 0)

    def value(self, port, motor_id, address):
        return self.registers.get(
            self._key(port, motor_id, address),
            self.default_value(address),
        )

    def ping(self, _port, _motor_id):
        return 1200, COMM_SUCCESS, 0

    def write1ByteTxRx(self, port, motor_id, address, value):
        self.registers[self._key(port, motor_id, address)] = value
        self.writes.append((motor_id, address, value))
        return COMM_SUCCESS, 0

    def write2ByteTxRx(self, port, motor_id, address, value):
        self.registers[self._key(port, motor_id, address)] = value
        self.writes.append((motor_id, address, value))
        return COMM_SUCCESS, 0

    def write4ByteTxRx(self, port, motor_id, address, value):
        self.registers[self._key(port, motor_id, address)] = value
        self.writes.append((motor_id, address, value))
        return COMM_SUCCESS, 0

    def read1ByteTxRx(self, port, motor_id, address):
        return self.value(port, motor_id, address), COMM_SUCCESS, 0

    def read2ByteTxRx(self, port, motor_id, address):
        return self.value(port, motor_id, address), COMM_SUCCESS, 0

    def read4ByteTxRx(self, port, motor_id, address):
        return self.value(port, motor_id, address), COMM_SUCCESS, 0

    def getTxRxResult(self, value):
        return f"comm={value}"

    def getRxPacketError(self, value):
        return f"error={value}"


class FakeGroupSyncRead:
    def __init__(self, port, packet, _start, _length) -> None:
        self.port = port
        self.packet = packet
        self.ids = set()

    def addParam(self, motor_id):
        self.ids.add(motor_id)
        return True

    def txRxPacket(self):
        return COMM_SUCCESS

    def isAvailable(self, motor_id, _address, _length):
        return motor_id in self.ids

    def getData(self, motor_id, address, _length):
        return self.packet.value(self.port, motor_id, address)


class FakeGroupSyncWrite:
    def __init__(self, port, packet, address, _length) -> None:
        self.port = port
        self.packet = packet
        self.address = address
        self.params = {}

    def addParam(self, motor_id, data):
        self.params[motor_id] = data
        return True

    def txPacket(self):
        for motor_id, data in self.params.items():
            value = sum(byte << (8 * index) for index, byte in enumerate(data))
            self.packet.registers[
                self.packet._key(self.port, motor_id, self.address)
            ] = value
        return COMM_SUCCESS

    def clearParam(self):
        self.params.clear()


fake_sdk = types.ModuleType("dynamixel_sdk")
fake_sdk.COMM_SUCCESS = COMM_SUCCESS
fake_sdk.GroupSyncRead = FakeGroupSyncRead
fake_sdk.GroupSyncWrite = FakeGroupSyncWrite
fake_sdk.PacketHandler = FakePacketHandler
fake_sdk.PortHandler = FakePortHandler
sys.modules["dynamixel_sdk"] = fake_sdk

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOL_ROOT = REPOSITORY_ROOT / "tools" / "dynamixel"
SOURCE = TOOL_ROOT / "hand_motion_7_motors.py"
CONFIG = TOOL_ROOT / "hand_motion_config.example.json"
spec = importlib.util.spec_from_file_location("hand_motion_under_test", SOURCE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


class HandMotionOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        FakePortHandler.opened_count = 0

    def test_config_and_plan_are_hardware_free(self) -> None:
        config = module.load_config(CONFIG)
        self.assertEqual(set(config.axes), set(range(1, 8)))
        self.assertFalse(config.mapping_is_confirmed)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.print_plan(config)
        self.assertIn("No serial port will be opened", output.getvalue())
        self.assertEqual(FakePortHandler.opened_count, 0)

    def test_dry_run_motion_does_not_open_ports(self) -> None:
        config = module.load_config(CONFIG)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = module.run_motion(
                config,
                ["open"],
                armed=False,
                confirmation="",
                duration_s=1.0,
                dwell_s=0.0,
                command_name="offline",
            )
        self.assertEqual(result, 0)
        self.assertEqual(FakePortHandler.opened_count, 0)

    def test_prepare_holds_position_and_applies_current_before_torque(self):
        config = module.load_config(CONFIG)
        hardware = module.Hardware(config, [1])
        try:
            hardware.prepare(
                {1: config.settings.calibration_goal_current_raw},
                require_start_within_endpoints=False,
            )
            runtime = hardware.runtimes[1]
            packet = hardware.packet
            port = runtime.port
            self.assertEqual(packet.value(port, 1, 38), 150)
            self.assertEqual(packet.value(port, 1, 102), 80)
            self.assertEqual(packet.value(port, 1, 116), 2048)
            self.assertEqual(packet.value(port, 1, 64), 1)

            write_addresses = [address for _, address, _ in packet.writes]
            torque_on_index = max(
                index
                for index, (_, address, value) in enumerate(packet.writes)
                if address == 64 and value == 1
            )
            self.assertLess(write_addresses.index(38), torque_on_index)
            self.assertLess(write_addresses.index(102), torque_on_index)
            self.assertLess(write_addresses.index(116), torque_on_index)
        finally:
            hardware.shutdown()
        self.assertEqual(packet.value(port, 1, 64), 0)
        self.assertFalse(port.opened)

    def test_mapping_confirmation_is_invalidated_by_role_change(self):
        raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            config = module.load_config(path)
            with contextlib.redirect_stdout(io.StringIO()):
                module.confirm_map(config, "MAP")
            confirmed = module.load_config(path)
            self.assertTrue(confirmed.mapping_is_confirmed)

            changed = json.loads(path.read_text(encoding="utf-8"))
            changed["axes"][0]["role"], changed["axes"][1]["role"] = (
                changed["axes"][1]["role"],
                changed["axes"][0]["role"],
            )
            path.write_text(
                json.dumps(changed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.assertFalse(module.load_config(path).mapping_is_confirmed)

    def test_group_status_and_goal_paths_cover_all_seven_axes(self):
        raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        for axis in raw["axes"]:
            axis["open_raw"] = 1900
            axis["closed_raw"] = 2200
            axis["calibrated"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            config = module.load_config(path)
            hardware = module.Hardware(config, range(1, 8))

            class MemoryLogger:
                def write(self, *_args):
                    pass

            try:
                hardware.prepare(
                    {
                        motor_id: config.axes[motor_id].goal_current_raw
                        for motor_id in config.axes
                    },
                    require_start_within_endpoints=True,
                )
                monitor = module.SafetyMonitor(hardware, MemoryLogger())
                statuses, blocked = monitor.sample("offline")
                self.assertEqual(set(statuses), set(range(1, 8)))
                self.assertEqual(blocked, set())
                self.assertTrue(
                    all(
                        status["position"] == 2048
                        for status in statuses.values()
                    )
                )

                goals = {motor_id: 2100 + motor_id for motor_id in config.axes}
                hardware.command_positions(goals)
                self.assertTrue(
                    all(
                        hardware.runtimes[motor_id].goal_raw
                        == goals[motor_id]
                        for motor_id in goals
                    )
                )
            finally:
                hardware.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
