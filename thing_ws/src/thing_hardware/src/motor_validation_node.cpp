#include <chrono>
#include <cstdint>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "thing_hardware/dynamixel_bus.hpp"
#include "thing_hardware/xl330_control_table.hpp"

class MotorValidatorNode : public rclcpp::Node
{
public:
  MotorValidatorNode() : Node("motor_validator")
  {
    RCLCPP_INFO(
      this->get_logger(), "Device: %s, baud rate: %d, protocol: %.1f, motor ID: %u",
      device_name_.c_str(), baud_rate_, protocol_version_, static_cast<unsigned int>(motor_id_));

    // ==== bus initialize ====
    bus_ =
      std::make_unique<thing_hardware::DynamixelBus>(device_name_, baud_rate_, protocol_version_);

    const auto initialize_result = bus_->initialize();

    if (!initialize_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to initialize DYNAMIXEL bus: %s",
        initialize_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(this->get_logger(), "DYNAMIXEL bus initialized: %s", device_name_.c_str());
    // ========================

    // ==== ping check ====
    uint16_t model_number = 0;

    const auto ping_result = bus_->ping(motor_id_, model_number);

    if (!ping_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Ping failed for ID %u: %s", static_cast<unsigned int>(motor_id_),
        ping_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Ping succeeded: ID=%u, model number=%u",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(model_number));

    if (model_number != thing_hardware::xl330::EXPECTED_MODEL_NUMBER) {
      RCLCPP_WARN(
        this->get_logger(), "Unexpected model: expected=%u, received=%u",
        static_cast<unsigned int>(thing_hardware::xl330::EXPECTED_MODEL_NUMBER),
        static_cast<unsigned int>(model_number));
    }
    // ====================

    // ==== drive mode read ====
    uint8_t drive_mode = 0;

    const auto drive_mode_result =
      bus_->read_one_byte(motor_id_, thing_hardware::xl330::DRIVE_MODE_ADDRESS, drive_mode);

    if (!drive_mode_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Drive Mode: %s",
        drive_mode_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Drive Mode: ID=%u, raw=0x%02X, torque_on_by_goal_update=%s",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(drive_mode),
      (drive_mode & 0x08U) == 0U ? "disabled" : "enabled");
    // =========================

    // ==== operating mode read ====
    uint8_t operating_mode = 0;

    const auto operating_mode_result =
      bus_->read_one_byte(motor_id_, thing_hardware::xl330::OPERATING_MODE_ADDRESS, operating_mode);

    if (!operating_mode_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Operating Mode: %s",
        operating_mode_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Operating Mode: ID=%u, raw=%u, value=%s",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(operating_mode),
      operating_mode_name(operating_mode));
    // =============================

    // ==== current limit read ====
    uint16_t raw_current_limit = 0;

    const auto current_limit_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::CURRENT_LIMIT_ADDRESS, raw_current_limit);

    if (!current_limit_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Current Limit: %s",
        current_limit_result.error_message.c_str());
      return;
    }

    const double current_limit_ma =
      static_cast<double>(raw_current_limit) * thing_hardware::xl330::CURRENT_MILLIAMPERE_UNIT;

    RCLCPP_INFO(
      this->get_logger(), "Current Limit: ID=%u, raw=%u, value=%.1f mA",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_current_limit),
      current_limit_ma);
    // ============================

    // ==== velocity limit read ====
    uint32_t raw_velocity_limit = 0;

    const auto velocity_limit_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::VELOCITY_LIMIT_ADDRESS, raw_velocity_limit);

    if (!velocity_limit_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Velocity Limit: %s",
        velocity_limit_result.error_message.c_str());
      return;
    }

    const double velocity_limit_rpm =
      static_cast<double>(raw_velocity_limit) * thing_hardware::xl330::VELOCITY_RPM_UNIT;

    RCLCPP_INFO(
      this->get_logger(), "Velocity Limit: ID=%u, raw=%u, value=%.2f rpm",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_velocity_limit),
      velocity_limit_rpm);
    // =============================

    // ==== maximum position limit read ====
    uint32_t raw_max_position_limit = 0;

    const auto max_position_limit_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::MAX_POSITION_LIMIT_ADDRESS, raw_max_position_limit);

    if (!max_position_limit_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Max Position Limit: %s",
        max_position_limit_result.error_message.c_str());
      return;
    }

    const double max_position_limit_degrees =
      static_cast<double>(raw_max_position_limit) * thing_hardware::xl330::POSITION_DEGREE_UNIT;

    RCLCPP_INFO(
      this->get_logger(), "Max Position Limit: ID=%u, raw=%u, value=%u pulse (%.2f deg)",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_max_position_limit),
      static_cast<unsigned int>(raw_max_position_limit), max_position_limit_degrees);
    // =====================================

    // ==== minimum position limit read ====
    uint32_t raw_min_position_limit = 0;

    const auto min_position_limit_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::MIN_POSITION_LIMIT_ADDRESS, raw_min_position_limit);

    if (!min_position_limit_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Min Position Limit: %s",
        min_position_limit_result.error_message.c_str());
      return;
    }

    const double min_position_limit_degrees =
      static_cast<double>(raw_min_position_limit) * thing_hardware::xl330::POSITION_DEGREE_UNIT;

    RCLCPP_INFO(
      this->get_logger(), "Min Position Limit: ID=%u, raw=%u, value=%u pulse (%.2f deg)",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_min_position_limit),
      static_cast<unsigned int>(raw_min_position_limit), min_position_limit_degrees);
    // =====================================

    // ==== torque enable read ====
    uint8_t torque_enable = 0;

    const auto torque_enable_result =
      bus_->read_one_byte(motor_id_, thing_hardware::xl330::TORQUE_ENABLE_ADDRESS, torque_enable);

    if (!torque_enable_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Torque Enable: %s",
        torque_enable_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Torque Enable: ID=%u, raw=%u, value=%s",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(torque_enable),
      torque_enable_name(torque_enable));
    // ============================

    // ==== hardware error status read ====
    uint8_t hardware_error_status = 0;

    const auto hardware_error_result = bus_->read_one_byte(
      motor_id_, thing_hardware::xl330::HARDWARE_ERROR_STATUS_ADDRESS, hardware_error_status);

    if (!hardware_error_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Hardware Error Status: %s",
        hardware_error_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Hardware Error Status: ID=%u, raw=0x%02X, value=%s",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(hardware_error_status),
      hardware_error_status == 0U ? "no_error" : "error_detected");
    // ====================================

    // ==== present position read ====
    uint32_t raw_present_position = 0;

    const auto position_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_POSITION_ADDRESS, raw_present_position);

    if (!position_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Present Position: %s",
        position_result.error_message.c_str());
      return;
    }

    const int32_t present_position = static_cast<int32_t>(raw_present_position);

    const double present_position_degrees =
      static_cast<double>(present_position) * thing_hardware::xl330::POSITION_DEGREE_UNIT;

    RCLCPP_INFO(
      this->get_logger(), "Present Position: ID=%u, raw=%u, value=%d pulse (%.2f deg)",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_present_position),
      present_position, present_position_degrees);
    // ===============================

    // ==== present input voltage read ====
    uint16_t raw_input_voltage = 0;

    const auto voltage_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_INPUT_VOLTAGE_ADDRESS, raw_input_voltage);

    if (!voltage_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Present Input Voltage: %s",
        voltage_result.error_message.c_str());
      return;
    }

    const double input_voltage =
      static_cast<double>(raw_input_voltage) * thing_hardware::xl330::INPUT_VOLTAGE_UNIT;

    RCLCPP_INFO(
      this->get_logger(), "Present Input Voltage: ID=%u, raw=%u, value=%.1f V",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_input_voltage),
      input_voltage);
    // ================================

    // ==== present temperature read ====
    uint8_t present_temperature = 0;

    const auto temperature_result = bus_->read_one_byte(
      motor_id_, thing_hardware::xl330::PRESENT_TEMPERATURE_ADDRESS, present_temperature);

    if (!temperature_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Present Temperature: %s",
        temperature_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Present Temperature: ID=%u, raw=%u, value=%u degC",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(present_temperature),
      static_cast<unsigned int>(present_temperature));
    // ==================================

    // ==== write test ====
    if (operating_mode != 5U) {
      RCLCPP_ERROR(this->get_logger(), "Expected Current-based Position Control Mode");
      return;
    }

    if (torque_enable != 0U) {
      RCLCPP_ERROR(this->get_logger(), "Torque must be disabled before test setup");
      return;
    }

    if (hardware_error_status != 0U) {
      RCLCPP_ERROR(this->get_logger(), "Hardware error detected");
      return;
    }

    if ((drive_mode & 0x08U) != 0U) {
      RCLCPP_ERROR(this->get_logger(), "Torque On by Goal Update must be disabled for this test");
      return;
    }

    static constexpr uint16_t TEST_GOAL_CURRENT = 100;  // 100 mA
    static constexpr uint32_t TEST_PROFILE_ACCELERATION = 5;
    static constexpr uint32_t TEST_PROFILE_VELOCITY = 20;  // 약 4.58 rpm
    static constexpr int32_t TEST_POSITION_DELTA = 30;

    if (TEST_GOAL_CURRENT > raw_current_limit) {
      RCLCPP_ERROR(this->get_logger(), "Test Goal Current exceeds Current Limit");
      return;
    }

    if (TEST_PROFILE_VELOCITY > raw_velocity_limit) {
      RCLCPP_ERROR(this->get_logger(), "Test Profile Velocity exceeds Velocity Limit");
      return;
    }

    const int32_t test_goal_position = present_position + TEST_POSITION_DELTA;

    if (
      test_goal_position < static_cast<int32_t>(raw_min_position_limit) ||
      test_goal_position > static_cast<int32_t>(raw_max_position_limit)) {
      RCLCPP_ERROR(this->get_logger(), "Test goal position is outside configured limits");
      return;
    }

    const uint32_t raw_test_goal_position = static_cast<uint32_t>(test_goal_position);

    const auto write_goal_current_result = bus_->write_two_bytes(
      motor_id_, thing_hardware::xl330::GOAL_CURRENT_ADDRESS, TEST_GOAL_CURRENT);

    if (!write_goal_current_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Goal Current: %s",
        write_goal_current_result.error_message.c_str());
      return;
    }

    const auto write_profile_acceleration_result = bus_->write_four_bytes(
      motor_id_, thing_hardware::xl330::PROFILE_ACCELERATION_ADDRESS, TEST_PROFILE_ACCELERATION);

    if (!write_profile_acceleration_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Profile Acceleration: %s",
        write_profile_acceleration_result.error_message.c_str());
      return;
    }

    const auto write_profile_velocity_result = bus_->write_four_bytes(
      motor_id_, thing_hardware::xl330::PROFILE_VELOCITY_ADDRESS, TEST_PROFILE_VELOCITY);

    if (!write_profile_velocity_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Profile Velocity: %s",
        write_profile_velocity_result.error_message.c_str());
      return;
    }

    uint16_t readback_goal_current = 0;
    uint32_t readback_profile_acceleration = 0;
    uint32_t readback_profile_velocity = 0;

    const auto readback_goal_current_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::GOAL_CURRENT_ADDRESS, readback_goal_current);

    if (!readback_goal_current_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read back Goal Current: %s",
        readback_goal_current_result.error_message.c_str());
      return;
    }

    const auto readback_profile_acceleration_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PROFILE_ACCELERATION_ADDRESS,
      readback_profile_acceleration);

    if (!readback_profile_acceleration_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read back Profile Acceleration: %s",
        readback_profile_acceleration_result.error_message.c_str());
      return;
    }

    const auto readback_profile_velocity_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PROFILE_VELOCITY_ADDRESS, readback_profile_velocity);

    if (!readback_profile_velocity_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read back Profile Velocity: %s",
        readback_profile_velocity_result.error_message.c_str());
      return;
    }

    if (
      readback_goal_current != TEST_GOAL_CURRENT ||
      readback_profile_acceleration != TEST_PROFILE_ACCELERATION ||
      readback_profile_velocity != TEST_PROFILE_VELOCITY) {
      RCLCPP_ERROR(
        this->get_logger(),
        "Test command read-back mismatch: "
        "goal_current=%u/%u, profile_acceleration=%u/%u, "
        "profile_velocity=%u/%u",
        static_cast<unsigned int>(readback_goal_current),
        static_cast<unsigned int>(TEST_GOAL_CURRENT),
        static_cast<unsigned int>(readback_profile_acceleration),
        static_cast<unsigned int>(TEST_PROFILE_ACCELERATION),
        static_cast<unsigned int>(readback_profile_velocity),
        static_cast<unsigned int>(TEST_PROFILE_VELOCITY));
      return;
    }

    RCLCPP_INFO(
      this->get_logger(),
      "Test profile verified: ID=%u, goal_current=%u mA, "
      "profile_acceleration=%u, profile_velocity=%u, "
      "planned_goal_position=%d pulse; torque remains disabled",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(readback_goal_current),
      static_cast<unsigned int>(readback_profile_acceleration),
      static_cast<unsigned int>(readback_profile_velocity), test_goal_position);

    const auto enable_torque_result =
      bus_->write_one_byte(motor_id_, thing_hardware::xl330::TORQUE_ENABLE_ADDRESS, 1U);

    if (!enable_torque_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to enable Torque: %s",
        enable_torque_result.error_message.c_str());
      return;
    }

    torque_enabled_ = true;

    uint8_t readback_torque_enable = 0;
    const auto readback_torque_result = bus_->read_one_byte(
      motor_id_, thing_hardware::xl330::TORQUE_ENABLE_ADDRESS, readback_torque_enable);

    if (!readback_torque_result.success || readback_torque_enable != 1U) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to verify Torque Enable: %s",
        readback_torque_result.success ? "unexpected read-back value"
                                       : readback_torque_result.error_message.c_str());
      disable_torque();
      return;
    }

    const auto write_goal_position_result = bus_->write_four_bytes(
      motor_id_, thing_hardware::xl330::GOAL_POSITION_ADDRESS, raw_test_goal_position);

    if (!write_goal_position_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Goal Position: %s",
        write_goal_position_result.error_message.c_str());
      disable_torque();
      return;
    }

    uint32_t readback_goal_position = 0;
    const auto readback_goal_position_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::GOAL_POSITION_ADDRESS, readback_goal_position);

    if (
      !readback_goal_position_result.success || readback_goal_position != raw_test_goal_position) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to verify Goal Position: received=%u, expected=%u, error=%s",
        static_cast<unsigned int>(readback_goal_position),
        static_cast<unsigned int>(raw_test_goal_position),
        readback_goal_position_result.success
          ? "none"
          : readback_goal_position_result.error_message.c_str());
      disable_torque();
      return;
    }

    test_goal_position_ = test_goal_position;

    RCLCPP_WARN(
      this->get_logger(),
      "Motion test started: ID=%u, start=%d pulse, goal=%d pulse; "
      "torque will be disabled automatically after 2 seconds",
      static_cast<unsigned int>(motor_id_), present_position, test_goal_position_);

    motion_timer_ =
      this->create_wall_timer(std::chrono::seconds(2), [this]() { finish_motion_test(); });
    // ====================
  }

  ~MotorValidatorNode() override { disable_torque(); }

private:
  void finish_motion_test()
  {
    uint16_t raw_present_current = 0;
    uint32_t raw_present_velocity = 0;
    uint32_t raw_present_position = 0;

    const auto current_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_CURRENT_ADDRESS, raw_present_current);
    const auto velocity_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_VELOCITY_ADDRESS, raw_present_velocity);
    const auto position_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_POSITION_ADDRESS, raw_present_position);

    if (current_result.success && velocity_result.success && position_result.success) {
      const int16_t present_current = static_cast<int16_t>(raw_present_current);
      const int32_t present_velocity = static_cast<int32_t>(raw_present_velocity);
      const int32_t present_position = static_cast<int32_t>(raw_present_position);
      const double present_velocity_rpm =
        static_cast<double>(present_velocity) * thing_hardware::xl330::VELOCITY_RPM_UNIT;

      RCLCPP_INFO(
        this->get_logger(),
        "Motion test result: ID=%u, goal=%d pulse, position=%d pulse, "
        "current=%d mA, velocity=%.2f rpm",
        static_cast<unsigned int>(motor_id_), test_goal_position_, present_position,
        static_cast<int>(present_current), present_velocity_rpm);
    } else {
      RCLCPP_ERROR(
        this->get_logger(),
        "Failed to read motion test result: current=%s, velocity=%s, position=%s",
        current_result.success ? "ok" : current_result.error_message.c_str(),
        velocity_result.success ? "ok" : velocity_result.error_message.c_str(),
        position_result.success ? "ok" : position_result.error_message.c_str());
    }

    disable_torque();

    if (motion_timer_) {
      motion_timer_->cancel();
    }
  }

  void disable_torque()
  {
    if (!torque_enabled_ || !bus_) {
      return;
    }

    const auto result =
      bus_->write_one_byte(motor_id_, thing_hardware::xl330::TORQUE_ENABLE_ADDRESS, 0U);

    if (!result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to disable Torque: %s", result.error_message.c_str());
      return;
    }

    torque_enabled_ = false;
    RCLCPP_INFO(
      this->get_logger(), "Torque disabled after motion test: ID=%u",
      static_cast<unsigned int>(motor_id_));
  }

  static const char * torque_enable_name(uint8_t torque_enable)
  {
    if (torque_enable == 0U) {
      return "disabled";
    }

    if (torque_enable == 1U) {
      return "enabled";
    }

    return "unknown";
  }

  static const char * operating_mode_name(uint8_t operating_mode)
  {
    switch (operating_mode) {
      case 0:
        return "current";
      case 1:
        return "velocity";
      case 3:
        return "position";
      case 4:
        return "extended_position";
      case 5:
        return "current_based_position";
      case 16:
        return "pwm";
      default:
        return "unknown";
    }
  }

  std::string device_name_{
    "/dev/serial/by-id/"
    "usb-FTDI_USB__-__Serial_Converter_FTBIN51S-if00-port0"};
  int baud_rate_{57600};
  float protocol_version_{2.0F};
  uint8_t motor_id_{3};

  std::unique_ptr<thing_hardware::DynamixelBus> bus_;
  rclcpp::TimerBase::SharedPtr motion_timer_;
  int32_t test_goal_position_{0};
  bool torque_enabled_{false};
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);

  auto node = std::make_shared<MotorValidatorNode>();
  rclcpp::spin(node);

  rclcpp::shutdown();
  return 0;
}
