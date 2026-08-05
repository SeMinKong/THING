#!/usr/bin/env python3
"""Publish the physical E-Stop auxiliary-contact heartbeat fail-closed."""

import importlib
import time
from typing import Any, Callable, Optional

import rclpy
from rclpy.clock import Clock, ClockType
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import Bool

from thing_hardware.estop_input import (
    EstopInputCore,
    GpiodV1LineReader,
    validate_configuration_types,
    validate_timing,
)


_HEARTBEAT_QOS = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.VOLATILE,
)


def _load_gpiod() -> Any:
    return importlib.import_module('gpiod')


class EstopGpioNode(Node):
    """
    Read an active-low auxiliary contact and publish E-Stop heartbeats.

    The main E-Stop contact must remove motor drive power without this process.
    This node only reports the isolated auxiliary contact to Safety Manager.
    Unknown, unreadable, or unsupported GPIO input is always reported active.
    """

    def __init__(
        self,
        *,
        gpiod_loader: Optional[Callable[[], Any]] = None,
    ) -> None:
        super().__init__(
            'estop_gpio_node',
            start_parameter_services=False,
        )
        for name, default in (
            ('gpio_chip', 'gpiochip4'),
            ('gpio_line', 17),
            ('active_low', True),
            ('poll_interval_ms', 5),
            ('debounce_ms', 50),
            ('heartbeat_period_ms', 100),
            ('reopen_interval_ms', 500),
            ('safety_timeout_ms', 300),
        ):
            self.declare_parameter(
                name,
                default,
                ParameterDescriptor(read_only=True),
            )

        gpio_chip = self.get_parameter('gpio_chip').value
        gpio_line = self.get_parameter('gpio_line').value
        active_low = self.get_parameter('active_low').value
        poll_interval_ms = self.get_parameter('poll_interval_ms').value
        debounce_ms = self.get_parameter('debounce_ms').value
        heartbeat_period_ms = self.get_parameter(
            'heartbeat_period_ms'
        ).value
        reopen_interval_ms = self.get_parameter('reopen_interval_ms').value
        safety_timeout_ms = self.get_parameter('safety_timeout_ms').value
        validate_configuration_types(
            gpio_chip=gpio_chip,
            gpio_line=gpio_line,
            active_low=active_low,
            poll_interval_ms=poll_interval_ms,
            debounce_ms=debounce_ms,
            heartbeat_period_ms=heartbeat_period_ms,
            reopen_interval_ms=reopen_interval_ms,
            safety_timeout_ms=safety_timeout_ms,
        )
        validate_timing(
            poll_interval_ms=poll_interval_ms,
            debounce_ms=debounce_ms,
            heartbeat_period_ms=heartbeat_period_ms,
            reopen_interval_ms=reopen_interval_ms,
            safety_timeout_ms=safety_timeout_ms,
        )
        if not gpio_chip:
            raise ValueError('gpio_chip must not be empty')
        if gpio_line < 0:
            raise ValueError('gpio_line must be non-negative')

        self._gpio_chip = gpio_chip
        self._gpio_line = gpio_line
        self._gpiod_loader = gpiod_loader or _load_gpiod
        self._reader: Optional[GpiodV1LineReader] = None
        self._next_reopen_ns = 0
        self._reopen_interval_ns = reopen_interval_ms * 1_000_000
        self._last_error: Optional[str] = None
        self._core = EstopInputCore(
            debounce_ns=debounce_ms * 1_000_000,
            active_low=active_low,
        )
        self._publisher = self.create_publisher(
            Bool,
            '/thing/estop',
            _HEARTBEAT_QOS,
        )
        self._steady_clock = Clock(clock_type=ClockType.STEADY_TIME)
        self._poll_timer = self.create_timer(
            poll_interval_ms / 1000.0,
            self._poll_gpio,
            clock=self._steady_clock,
        )
        self._heartbeat_timer = self.create_timer(
            heartbeat_period_ms / 1000.0,
            self._publish_state,
            clock=self._steady_clock,
        )

        # Start fail-closed instead of waiting for the first GPIO read or the
        # Safety Manager's missing-heartbeat timeout.
        self._publish_state()
        self.get_logger().warn(
            'E-Stop input starts active until GPIO is read as stable inactive'
        )

    def _ensure_reader(self) -> GpiodV1LineReader:
        if self._reader is None:
            self._reader = GpiodV1LineReader(
                gpiod_module=self._gpiod_loader(),
                chip_name=self._gpio_chip,
                line_offset=self._gpio_line,
                consumer='thing-estop',
            )
        return self._reader

    def _record_read_error(self, error: Exception, now_ns: int) -> None:
        if self._reader is not None:
            self._reader.close()
        self._reader = None
        self._next_reopen_ns = now_ns + self._reopen_interval_ns
        changed = self._core.fail_closed()
        message = f'{type(error).__name__}: {error}'
        if message != self._last_error:
            self.get_logger().error(
                'E-Stop GPIO unreadable; forcing active: ' + message
            )
            self._last_error = message
        if changed:
            self._publish_state()

    def _poll_gpio(self) -> None:
        now_ns = time.monotonic_ns()
        if now_ns < self._next_reopen_ns:
            return
        try:
            raw_level = self._ensure_reader().read()
        except Exception as error:
            self._record_read_error(error, now_ns)
            return

        if self._last_error is not None:
            self.get_logger().info('E-Stop GPIO input recovered')
            self._last_error = None
        if self._core.observe(raw_level, now_ns):
            self._publish_state()
            if self._core.active:
                self.get_logger().error('Physical E-Stop auxiliary contact active')
            else:
                self.get_logger().info(
                    'E-Stop auxiliary contact stable inactive; reset still required'
                )

    def _publish_state(self) -> None:
        message = Bool()
        message.data = self._core.active
        self._publisher.publish(message)

    def destroy_node(self) -> bool:
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = EstopGpioNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
