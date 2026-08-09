"""Static wiring tests for the keyboard teleop ROS executor."""

from pathlib import Path


SOURCE = (
    Path(__file__).parents[1]
    / 'thing_teleop'
    / 'keyboard_teleop_node.py'
).read_text(encoding='utf-8')


def test_ros_executor_runs_outside_curses_ui_loop():
    """Ensure terminal rendering cannot starve ROS timer callbacks."""
    run_ui_source = SOURCE.split('def run_ui(', 1)[1].split(
        '\ndef main(', 1
    )[0]

    assert 'rclpy.spin_once' not in run_ui_source
    assert 'node.ui_snapshot()' in run_ui_source
    assert 'SingleThreadedExecutor()' in SOURCE
    assert 'Thread(target=executor.spin' in SOURCE


def test_control_lifecycle_is_driven_by_ros_timer():
    """Ensure lease and STOP futures progress independently of the UI."""
    assert 'self._lifecycle_timer = self.create_timer(' in SOURCE
    assert 'self.update_control_lifecycle,' in SOURCE
