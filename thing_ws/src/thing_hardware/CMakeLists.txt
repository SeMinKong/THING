cmake_minimum_required(VERSION 3.8)
project(thing_hardware)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclcpp REQUIRED)
find_package(dynamixel_sdk REQUIRED)
find_package(thing_interfaces REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)

ament_python_install_package(${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  find_package(ament_cmake_pytest REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  set(ament_cmake_uncrustify_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
  ament_add_pytest_test(test_estop_input test/test_estop_input.py)
  ament_add_pytest_test(test_estop_gpio_node test/test_estop_gpio_node.py)
endif()

add_executable(
  motor_validation_node
  src/motor_validation_node.cpp
  src/dynamixel_bus.cpp
)

add_executable(
  motor_home_calibration_node
  src/motor_home_calibration_node.cpp
  src/dynamixel_bus.cpp
)

add_executable(
  dynamixel_baud_config_node
  src/dynamixel_baud_config_node.cpp
  src/dynamixel_bus.cpp
)

add_executable(
  thumb_calibration_node
  src/thumb_calibration_node.cpp
  src/dynamixel_bus.cpp
)

add_executable(
  thumb_motion_test_node
  src/thumb_motion_test_node.cpp
  src/thumb_motion_controller.cpp
  src/dynamixel_bus.cpp
)

add_executable(
  motor_status_node
  src/motor_status_node.cpp
  src/dynamixel_bus.cpp
)

add_executable(
  motor_driver_node
  src/motor_driver_node.cpp
  src/thumb_motion_controller.cpp
  src/dynamixel_bus.cpp
)

target_include_directories(
  motor_validation_node
  PRIVATE
    include
)

target_include_directories(
  motor_home_calibration_node
  PRIVATE
    include
)

target_include_directories(
  dynamixel_baud_config_node
  PRIVATE
    include
)

target_include_directories(
  thumb_calibration_node
  PRIVATE
    include
)

target_include_directories(
  thumb_motion_test_node
  PRIVATE
    include
)

target_include_directories(
  motor_status_node
  PRIVATE
    include
)

target_include_directories(
  motor_driver_node
  PRIVATE
    include
)

ament_target_dependencies(
  motor_validation_node
  rclcpp
  dynamixel_sdk
)

ament_target_dependencies(
  motor_home_calibration_node
  rclcpp
  dynamixel_sdk
)

ament_target_dependencies(
  dynamixel_baud_config_node
  rclcpp
  dynamixel_sdk
)

ament_target_dependencies(
  thumb_calibration_node
  rclcpp
  dynamixel_sdk
)

ament_target_dependencies(
  thumb_motion_test_node
  rclcpp
  dynamixel_sdk
)

ament_target_dependencies(
  motor_status_node
  rclcpp
  dynamixel_sdk
  thing_interfaces
  diagnostic_msgs
)

ament_target_dependencies(
  motor_driver_node
  rclcpp
  dynamixel_sdk
  thing_interfaces
  diagnostic_msgs
)

install(
  TARGETS
    dynamixel_baud_config_node
    motor_driver_node
    motor_status_node
    motor_home_calibration_node
    motor_validation_node
    thumb_calibration_node
    thumb_motion_test_node
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY config
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS scripts/estop_gpio_node
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
