#include <chrono>
#include <functional>
#include <memory>

// ROS 2 C++ 노드 기능을 사용하기 위한 헤더
#include "rclcpp/rclcpp.hpp"

// 발행할 사용자 정의 HandCommand 메시지 헤더
#include "thing_interfaces/msg/hand_command.hpp"

// 1s와 같은 시간 단위 표현을 사용하기 위한 설정
using namespace std::chrono_literals;

// HandCommand 메시지를 주기적으로 발행하는 ROS 2 노드
class HandCommandPublisher : public rclcpp::Node
{
public:
  HandCommandPublisher()
  : Node("hand_command_publisher"),  // 노드 이름 설정
    sequence_(0)                     // 명령 순서 번호를 0부터 시작
  {
    // /thing/command/test 토픽으로 HandCommand 메시지를 발행하는
    // 퍼블리셔 생성
    // 두 번째 인자인 10은 QoS 메시지 큐의 크기
    publisher_ = create_publisher<thing_interfaces::msg::HandCommand>(
      "/thing/command/test", 10);

    // 1초마다 publish_command() 함수를 실행하는 타이머 생성
    timer_ = create_wall_timer(
      1s,
      std::bind(&HandCommandPublisher::publish_command, this));
  }

private:
  // HandCommand 메시지를 생성하고 발행하는 함수
  void publish_command()
  {
    // 발행할 HandCommand 메시지 객체 생성
    thing_interfaces::msg::HandCommand message;

    // 현재 ROS 시간을 메시지 생성 시각으로 저장
    message.stamp = now();

    // 메시지의 순서 번호 저장
    message.sequence = sequence_;

    // 명령 생성 주체를 수동 조작 모드로 설정
    message.source =
      thing_interfaces::msg::HandCommand::SOURCE_TELEOP;

    // 7개 논리축의 테스트 명령값 설정
    message.thumb_flex = 0.1F;   // 엄지 굽힘
    message.thumb_opp = 0.2F;    // 엄지 대립
    message.thumb_abd = 0.3F;    // 엄지 벌림
    message.index_flex = 0.4F;   // 검지 굽힘
    message.middle_flex = 0.5F;  // 중지 굽힘
    message.ring_flex = 0.6F;    // 약지 굽힘
    message.little_flex = 0.7F;  // 소지 굽힘

    // 로봇 손의 최대 동작 속도 제한값 설정
    message.speed_limit = 0.25F;

    // 수동 조작 명령이므로 신뢰도를 최대값으로 설정
    message.confidence = 1.0F;

    // 완성된 HandCommand 메시지를 토픽으로 발행
    publisher_->publish(message);

    // 현재 발행한 메시지의 순서 번호를 터미널에 출력
    RCLCPP_INFO(
      get_logger(),
      "Published HandCommand sequence=%u",
      message.sequence);

    // 다음 메시지 발행을 위해 순서 번호를 1 증가
    ++sequence_;
  }

  // 발행되는 명령의 순서를 구분하기 위한 번호
  uint32_t sequence_;

  // HandCommand 메시지 퍼블리셔
  rclcpp::Publisher<
    thing_interfaces::msg::HandCommand>::SharedPtr publisher_;

  // 1초 주기로 메시지를 발행하기 위한 타이머
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char * argv[])
{
  // ROS 2 통신 시스템 초기화
  rclcpp::init(argc, argv);

  // HandCommandPublisher 노드를 생성하고
  // 종료 요청이 들어올 때까지 콜백을 반복 실행
  rclcpp::spin(std::make_shared<HandCommandPublisher>());

  // ROS 2 종료 처리
  rclcpp::shutdown();

  return 0;
}