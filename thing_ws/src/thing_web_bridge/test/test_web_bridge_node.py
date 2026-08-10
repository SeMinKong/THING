"""
Pin the subscription-callback crash guard without spinning rclpy.

구독 콜백에서 ProtocolError가 밖으로 나가면 rclpy spin이 죽어 브리지
전체가 내려간다(NaN 크래시와 같은 계열). _guarded가 예외를 흡수하고
로그만 남기는지 확인한다.

web_bridge_node는 rclpy·thing_interfaces를 import하므로 ROS 환경이 없으면
전체를 skip한다. colcon test는 환경이 있으므로 항상 실행된다.
"""

from thing_web_bridge.protocol import ProtocolError

import pytest

web_bridge_node = pytest.importorskip(
    'thing_web_bridge.web_bridge_node',
    reason='web_bridge_node requires a sourced ROS 2 workspace',
)


class RecordingLogger:
    """Capture warning calls the way the rclpy logger receives them."""

    def __init__(self):
        self.messages = []

    def warning(self, message, **kwargs):
        self.messages.append(message)


def test_guarded_callback_drops_bad_message_and_logs():
    seen = []
    logger = RecordingLogger()

    def update(message):
        seen.append(message)
        raise ProtocolError('invalid_safety_state')

    callback = web_bridge_node._guarded(update, logger)
    callback('bad-enum')  # 예외가 새어 나오면 여기서 테스트가 실패한다

    assert seen == ['bad-enum']
    assert logger.messages
    assert 'invalid_safety_state' in logger.messages[0]


def test_guarded_callback_passes_good_messages_through():
    seen = []
    logger = RecordingLogger()
    callback = web_bridge_node._guarded(seen.append, logger)

    callback('fine')

    assert seen == ['fine']
    assert logger.messages == []
