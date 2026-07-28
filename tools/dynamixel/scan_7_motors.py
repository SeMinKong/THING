#!/usr/bin/env python3
"""Read-only connectivity scan for the configured DYNAMIXEL buses."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from dynamixel_sdk import COMM_SUCCESS, PacketHandler, PortHandler


@dataclass(frozen=True)
class BusConfig:
    name: str
    device: str
    expected_ids: tuple[int, ...]


@dataclass(frozen=True)
class ScanConfig:
    baudrate: int
    protocol_version: float
    buses: tuple[BusConfig, ...]


def load_config(path: Path) -> ScanConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    buses: list[BusConfig] = []
    seen_ids: set[int] = set()

    for item in raw["buses"]:
        ids = tuple(int(value) for value in item["ids"])
        if not ids:
            raise ValueError(f"{item['name']} has no configured IDs")
        duplicates = seen_ids.intersection(ids)
        if duplicates:
            raise ValueError(f"Duplicate motor IDs: {sorted(duplicates)}")
        seen_ids.update(ids)
        buses.append(
            BusConfig(
                name=str(item["name"]),
                device=str(item["device"]),
                expected_ids=ids,
            )
        )

    if not buses:
        raise ValueError("No buses in configuration")
    return ScanConfig(
        baudrate=int(raw["baudrate"]),
        protocol_version=float(raw["protocol_version"]),
        buses=tuple(buses),
    )


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=script_dir / "hand_motion_config.json",
        help="Runtime hand configuration JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Configuration not found: {config_path}. Copy "
            "hand_motion_config.example.json to hand_motion_config.json first."
        )

    config = load_config(config_path)
    packet = PacketHandler(config.protocol_version)
    total_detected = 0
    total_expected = sum(len(bus.expected_ids) for bus in config.buses)

    for bus in config.buses:
        port = PortHandler(bus.device)
        print(f"\nScanning {bus.name}")
        print(f"Port: {bus.device}")

        if not port.openPort():
            print("Could not open port")
            continue

        try:
            if not port.setBaudRate(config.baudrate):
                print("Could not set baud rate")
                continue

            for motor_id in bus.expected_ids:
                model, comm_result, dxl_error = packet.ping(port, motor_id)
                if comm_result != COMM_SUCCESS:
                    print(
                        f"ID {motor_id}: "
                        f"{packet.getTxRxResult(comm_result)}"
                    )
                    continue
                if dxl_error:
                    print(
                        f"ID {motor_id}: "
                        f"{packet.getRxPacketError(dxl_error)}"
                    )
                    continue

                print(f"ID {motor_id}: detected, model={model}")
                total_detected += 1
        finally:
            port.closePort()

    print(f"\nDetected motors: {total_detected}/{total_expected}")
    if total_detected == total_expected:
        print("All configured motors detected successfully.")
        return 0

    print("One or more configured motors were not detected.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
