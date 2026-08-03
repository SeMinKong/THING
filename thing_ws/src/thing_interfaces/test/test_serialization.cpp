#include <gtest/gtest.h>

// ROS 2 메시지를 직렬화·역직렬화하기 위한 헤더
#include "rclcpp/serialization.hpp"
#include "rclcpp/serialized_message.hpp"

// 테스트 대상인 HandCommand 메시지 헤더
#include "thing_interfaces/msg/hand_command.hpp"

// HandCommand 메시지를 직렬화한 뒤 다시 복원했을 때
// 모든 필드가 원본과 동일하게 유지되는지 확인하는 테스트
TEST(HandCommandSerialization, round_trip)
{
  // 원본 HandCommand 메시지 생성
  thing_interfaces::msg::HandCommand original;

  // 메시지 생성 시각 설정
  original.stamp.sec = 123;
  original.stamp.nanosec = 456000000;

  // 명령 순서 번호 설정
  original.sequence = 42;

  // 명령 생성 주체를 손동작 모방 모드로 설정
  original.source =
    thing_interfaces::msg::HandCommand::SOURCE_MIMIC;

  // 7개 논리축의 목표값 설정
  // 각 값은 정규화된 손가락 굽힘·움직임 정도를 의미
  original.thumb_flex = 0.1F;   // 엄지 굽힘
  original.thumb_opp = 0.2F;    // 엄지 대립
  original.thumb_abd = 0.3F;    // 엄지 벌림
  original.index_flex = 0.4F;   // 검지 굽힘
  original.middle_flex = 0.5F;  // 중지 굽힘
  original.ring_flex = 0.6F;    // 약지 굽힘
  original.little_flex = 0.7F;  // 소지 굽힘

  // 최대 동작 속도 제한 설정
  original.speed_limit = 0.25F;

  // 손동작 인식 결과의 신뢰도 설정
  original.confidence = 0.95F;

  // HandCommand 메시지를 직렬화·역직렬화할 객체 생성
  rclcpp::Serialization<
    thing_interfaces::msg::HandCommand> serializer;

  // 직렬화된 바이너리 데이터를 저장할 객체
  rclcpp::SerializedMessage serialized_message;

  // 원본 메시지를 전송 가능한 바이너리 형태로 직렬화
  serializer.serialize_message(
    &original,
    &serialized_message);

  // 역직렬화 결과를 저장할 메시지 객체 생성
  thing_interfaces::msg::HandCommand restored;

  // 직렬화된 데이터를 다시 HandCommand 메시지로 복원
  serializer.deserialize_message(
    &serialized_message,
    &restored);

  // 시간 정보가 원본과 동일한지 확인
  EXPECT_EQ(restored.stamp.sec, 123);
  EXPECT_EQ(restored.stamp.nanosec, 456000000U);

  // 명령 순서 번호와 명령 생성 주체 확인
  EXPECT_EQ(restored.sequence, 42U);
  EXPECT_EQ(
    restored.source,
    thing_interfaces::msg::HandCommand::SOURCE_MIMIC);

  // 7개 논리축 값이 직렬화 전과 동일한지 확인
  EXPECT_FLOAT_EQ(restored.thumb_flex, 0.1F);
  EXPECT_FLOAT_EQ(restored.thumb_opp, 0.2F);
  EXPECT_FLOAT_EQ(restored.thumb_abd, 0.3F);
  EXPECT_FLOAT_EQ(restored.index_flex, 0.4F);
  EXPECT_FLOAT_EQ(restored.middle_flex, 0.5F);
  EXPECT_FLOAT_EQ(restored.ring_flex, 0.6F);
  EXPECT_FLOAT_EQ(restored.little_flex, 0.7F);

  // 속도 제한과 인식 신뢰도가 동일하게 복원됐는지 확인
  EXPECT_FLOAT_EQ(restored.speed_limit, 0.25F);
  EXPECT_FLOAT_EQ(restored.confidence, 0.95F);
}