cmake_minimum_required(VERSION 3.8)
project(thing_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
# 의존성 추가
find_package(rclcpp REQUIRED)

set(msg_files
  "msg/HandCommand.msg"
  "msg/HandLandmarks.msg"
  "msg/MotorState.msg"
  "msg/MotorStatus.msg"
  "msg/ControlState.msg"
  "msg/SafetyState.msg"
  "msg/RecordingState.msg"
)

set(srv_files
  "srv/SetControlMode.srv"
  "srv/ExecuteGesture.srv"
  "srv/StartRecording.srv"
  "srv/StopRecording.srv"
  "srv/SetMimicResult.srv"
)

set(action_files
  "action/ExecuteSequence.action"
)

rosidl_generate_interfaces(${PROJECT_NAME}
  ${msg_files}
  ${srv_files}
  ${action_files}
  DEPENDENCIES builtin_interfaces geometry_msgs std_msgs
)


# 같은 패키지에서 생성된 인터페이스를
# C++ 예제 노드와 테스트에서 사용할 수 있도록 타입 지원 연결
rosidl_get_typesupport_target(
  cpp_typesupport_target
  ${PROJECT_NAME}
  rosidl_typesupport_cpp
)

add_executable(
  hand_command_publisher
  examples/hand_command_publisher.cpp
)

ament_target_dependencies(
  hand_command_publisher
  rclcpp
)

target_link_libraries(
  hand_command_publisher
  "${cpp_typesupport_target}"
)

add_executable(
  hand_command_subscriber
  examples/hand_command_subscriber.cpp
)

ament_target_dependencies(
  hand_command_subscriber
  rclcpp
)

target_link_libraries(
  hand_command_subscriber
  "${cpp_typesupport_target}"
)

install(
  TARGETS
    hand_command_publisher
    hand_command_subscriber
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)

  ament_add_gtest(
    test_serialization
    test/test_serialization.cpp
  )

  if(TARGET test_serialization)
    ament_target_dependencies(
      test_serialization
      rclcpp
    )

    target_link_libraries(
      test_serialization
      "${cpp_typesupport_target}"
    )
  endif()
endif()

ament_export_dependencies(rosidl_default_runtime)
ament_package()
