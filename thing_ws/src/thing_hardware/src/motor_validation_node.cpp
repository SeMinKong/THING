#include <chrono>
#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "thing_hardware/dynamixel_bus.hpp"
#include "thing_hardware/xl330_control_table.hpp"

class MotorValidatorNode : public rclcpp::Node
{
public:
  MotorValidatorNode() : Node("motor_validator")
  {
    declare_parameters();
    load_and_validate_parameters();

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

    // ==== position gain read ====
    uint16_t position_d_gain = 0;
    uint16_t position_i_gain = 0;
    uint16_t position_p_gain = 0;

    const auto position_d_gain_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_D_GAIN_ADDRESS, position_d_gain);
    const auto position_i_gain_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_I_GAIN_ADDRESS, position_i_gain);
    const auto position_p_gain_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_P_GAIN_ADDRESS, position_p_gain);

    if (
      !position_d_gain_result.success || !position_i_gain_result.success ||
      !position_p_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Position Gain: d=%s, i=%s, p=%s",
        position_d_gain_result.success ? "ok" : position_d_gain_result.error_message.c_str(),
        position_i_gain_result.success ? "ok" : position_i_gain_result.error_message.c_str(),
        position_p_gain_result.success ? "ok" : position_p_gain_result.error_message.c_str());
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Position Gain: ID=%u, d=%u, i=%u, p=%u",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(position_d_gain),
      static_cast<unsigned int>(position_i_gain), static_cast<unsigned int>(position_p_gain));
    // ============================

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

    if (!motion_test_enabled_) {
      RCLCPP_INFO(
        this->get_logger(),
        "Status-only inspection complete: home_position and closed_position are uncalibrated; "
        "no control values were written and the existing torque state was not changed");
      rclcpp::shutdown();
      return;
    }

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

    if (goal_current_ > raw_current_limit) {
      RCLCPP_ERROR(this->get_logger(), "Test Goal Current exceeds Current Limit");
      return;
    }

    if (profile_velocity_ > raw_velocity_limit) {
      RCLCPP_ERROR(this->get_logger(), "Test Profile Velocity exceeds Velocity Limit");
      return;
    }

    const int32_t test_goal_position = static_cast<int32_t>(closed_position_);

    const uint32_t raw_test_goal_position = static_cast<uint32_t>(test_goal_position);

    const auto write_position_p_gain_result = bus_->write_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_P_GAIN_ADDRESS, position_p_gain_);

    if (!write_position_p_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Position P Gain: %s",
        write_position_p_gain_result.error_message.c_str());
      return;
    }

    const auto write_position_i_gain_result = bus_->write_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_I_GAIN_ADDRESS, position_i_gain_);

    if (!write_position_i_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Position I Gain: %s",
        write_position_i_gain_result.error_message.c_str());
      return;
    }

    const auto write_position_d_gain_result = bus_->write_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_D_GAIN_ADDRESS, position_d_gain_);

    if (!write_position_d_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Position D Gain: %s",
        write_position_d_gain_result.error_message.c_str());
      return;
    }

    const auto write_goal_current_result =
      bus_->write_two_bytes(motor_id_, thing_hardware::xl330::GOAL_CURRENT_ADDRESS, goal_current_);

    if (!write_goal_current_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Goal Current: %s",
        write_goal_current_result.error_message.c_str());
      return;
    }

    const auto write_profile_acceleration_result = bus_->write_four_bytes(
      motor_id_, thing_hardware::xl330::PROFILE_ACCELERATION_ADDRESS, profile_acceleration_);

    if (!write_profile_acceleration_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Profile Acceleration: %s",
        write_profile_acceleration_result.error_message.c_str());
      return;
    }

    const auto write_profile_velocity_result = bus_->write_four_bytes(
      motor_id_, thing_hardware::xl330::PROFILE_VELOCITY_ADDRESS, profile_velocity_);

    if (!write_profile_velocity_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write Profile Velocity: %s",
        write_profile_velocity_result.error_message.c_str());
      return;
    }

    uint16_t readback_position_p_gain = 0;
    uint16_t readback_position_i_gain = 0;
    uint16_t readback_position_d_gain = 0;
    uint16_t readback_goal_current = 0;
    uint32_t readback_profile_acceleration = 0;
    uint32_t readback_profile_velocity = 0;

    const auto readback_position_p_gain_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_P_GAIN_ADDRESS, readback_position_p_gain);

    if (!readback_position_p_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read back Position P Gain: %s",
        readback_position_p_gain_result.error_message.c_str());
      return;
    }

    const auto readback_position_i_gain_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_I_GAIN_ADDRESS, readback_position_i_gain);

    if (!readback_position_i_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read back Position I Gain: %s",
        readback_position_i_gain_result.error_message.c_str());
      return;
    }

    const auto readback_position_d_gain_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_D_GAIN_ADDRESS, readback_position_d_gain);

    if (!readback_position_d_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read back Position D Gain: %s",
        readback_position_d_gain_result.error_message.c_str());
      return;
    }

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
      readback_position_p_gain != position_p_gain_ ||
      readback_position_i_gain != position_i_gain_ ||
      readback_position_d_gain != position_d_gain_ || readback_goal_current != goal_current_ ||
      readback_profile_acceleration != profile_acceleration_ ||
      readback_profile_velocity != profile_velocity_) {
      RCLCPP_ERROR(
        this->get_logger(),
        "Test command read-back mismatch: "
        "position_p_gain=%u/%u, position_i_gain=%u/%u, position_d_gain=%u/%u, "
        "goal_current=%u/%u, "
        "profile_acceleration=%u/%u, profile_velocity=%u/%u",
        static_cast<unsigned int>(readback_position_p_gain),
        static_cast<unsigned int>(position_p_gain_),
        static_cast<unsigned int>(readback_position_i_gain),
        static_cast<unsigned int>(position_i_gain_),
        static_cast<unsigned int>(readback_position_d_gain),
        static_cast<unsigned int>(position_d_gain_),
        static_cast<unsigned int>(readback_goal_current), static_cast<unsigned int>(goal_current_),
        static_cast<unsigned int>(readback_profile_acceleration),
        static_cast<unsigned int>(profile_acceleration_),
        static_cast<unsigned int>(readback_profile_velocity),
        static_cast<unsigned int>(profile_velocity_));
      return;
    }

    RCLCPP_INFO(
      this->get_logger(),
      "Test profile verified: ID=%u, position_p_gain=%u, position_i_gain=%u, "
      "position_d_gain=%u, "
      "goal_current=%u mA, profile_acceleration=%u, profile_velocity=%u, "
      "planned_goal_position=%d pulse; torque remains disabled",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(readback_position_p_gain),
      static_cast<unsigned int>(readback_position_i_gain),
      static_cast<unsigned int>(readback_position_d_gain),
      static_cast<unsigned int>(readback_goal_current),
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

    start_position_ = static_cast<int32_t>(home_position_);
    test_goal_position_ = test_goal_position;

    RCLCPP_WARN(
      this->get_logger(),
      "Forward motion test started: ID=%u, current=%d pulse, home=%d pulse, goal=%d pulse; "
      "monitoring every 100 ms with a %ld second timeout",
      static_cast<unsigned int>(motor_id_), present_position, start_position_, test_goal_position_,
      static_cast<long>(motion_timeout_seconds_.count()));

    motion_start_time_ = std::chrono::steady_clock::now();
    motion_timer_ =
      this->create_wall_timer(std::chrono::milliseconds(100), [this]() { monitor_motion_test(); });
    // ====================
  }

  ~MotorValidatorNode() override { disable_torque(); }

private:
  // Bus parameter
  std::string device_name_{
    "/dev/serial/by-id/"
    "usb-FTDI_USB__-__Serial_Converter_FTBIN51S-if00-port0"};
  int baud_rate_{57600};
  float protocol_version_{2.0F};

  // Axis parameter
  uint8_t motor_id_{3};
  int64_t home_position_{750};
  int64_t closed_position_{1000};

  // Controller parameter
  uint16_t position_p_gain_{500};
  uint16_t position_i_gain_{30};
  uint16_t position_d_gain_{600};
  uint16_t goal_current_{500};  // mA
  uint32_t profile_acceleration_{50};
  uint32_t profile_velocity_{200};  // 약 45.80 rpm

  // Completion and safety parameter
  int64_t position_tolerance_{5};
  int64_t settled_velocity_raw_{1};
  uint8_t required_settled_samples_{3};
  std::chrono::seconds motion_timeout_seconds_{6};
  bool motion_test_enabled_{true};

  void declare_parameters()
  {
    this->declare_parameter<std::string>("device_name", "");
    this->declare_parameter<int64_t>("baud_rate", 57600);
    this->declare_parameter<double>("protocol_version", 2.0);
    this->declare_parameter<int64_t>("motor_id", 3);
    this->declare_parameter<int64_t>("home_position", 750);
    this->declare_parameter<int64_t>("closed_position", 1000);
    this->declare_parameter<int64_t>("position_p_gain", 250);
    this->declare_parameter<int64_t>("position_i_gain", 0);
    this->declare_parameter<int64_t>("position_d_gain", 600);
    this->declare_parameter<int64_t>("goal_current", 500);
    this->declare_parameter<int64_t>("profile_acceleration", 5);
    this->declare_parameter<int64_t>("profile_velocity", 50);
    this->declare_parameter<int64_t>("position_tolerance", 5);
    this->declare_parameter<int64_t>("settled_velocity_raw", 1);
    this->declare_parameter<int64_t>("required_settled_samples", 3);
    this->declare_parameter<int64_t>("motion_timeout_seconds", 6);
  }

  void load_and_validate_parameters()
  {
    device_name_ = this->get_parameter("device_name").as_string();
    const int64_t baud_rate = this->get_parameter("baud_rate").as_int();
    const double protocol_version = this->get_parameter("protocol_version").as_double();
    const int64_t motor_id = this->get_parameter("motor_id").as_int();
    home_position_ = this->get_parameter("home_position").as_int();
    closed_position_ = this->get_parameter("closed_position").as_int();
    const int64_t position_p_gain = this->get_parameter("position_p_gain").as_int();
    const int64_t position_i_gain = this->get_parameter("position_i_gain").as_int();
    const int64_t position_d_gain = this->get_parameter("position_d_gain").as_int();
    const int64_t goal_current = this->get_parameter("goal_current").as_int();
    const int64_t profile_acceleration = this->get_parameter("profile_acceleration").as_int();
    const int64_t profile_velocity = this->get_parameter("profile_velocity").as_int();
    position_tolerance_ = this->get_parameter("position_tolerance").as_int();
    settled_velocity_raw_ = this->get_parameter("settled_velocity_raw").as_int();
    const int64_t required_settled_samples =
      this->get_parameter("required_settled_samples").as_int();
    const int64_t motion_timeout_seconds = this->get_parameter("motion_timeout_seconds").as_int();

    if (device_name_.empty()) {
      throw std::runtime_error("device_name must not be empty");
    }

    if (baud_rate <= 0 || baud_rate > std::numeric_limits<int>::max()) {
      throw std::runtime_error("baud_rate must be between 1 and INT_MAX");
    }

    if (protocol_version != 2.0) {
      throw std::runtime_error("protocol_version must be 2.0 for XL330");
    }

    if (motor_id < 0 || motor_id > 252) {
      throw std::runtime_error("motor_id must be between 0 and 252");
    }

    const bool home_position_uncalibrated = home_position_ == -1;
    const bool closed_position_uncalibrated = closed_position_ == -1;

    if (home_position_uncalibrated != closed_position_uncalibrated) {
      throw std::runtime_error(
        "home_position and closed_position must either both be -1 or both be calibrated");
    }

    motion_test_enabled_ = !home_position_uncalibrated;

    if (motion_test_enabled_) {
      constexpr int64_t MIN_EXTENDED_POSITION = -1048575;
      constexpr int64_t MAX_EXTENDED_POSITION = 1048575;

      if (home_position_ < MIN_EXTENDED_POSITION || home_position_ > MAX_EXTENDED_POSITION) {
        throw std::runtime_error("home_position must be between -1048575 and 1048575 in mode 5");
      }

      if (closed_position_ < MIN_EXTENDED_POSITION || closed_position_ > MAX_EXTENDED_POSITION) {
        throw std::runtime_error("closed_position must be between -1048575 and 1048575 in mode 5");
      }

      if (closed_position_ == home_position_) {
        throw std::runtime_error("closed_position must differ from home_position");
      }
    }

    if (position_p_gain < 0 || position_p_gain > 16383) {
      throw std::runtime_error("position_p_gain must be between 0 and 16383");
    }

    if (position_i_gain < 0 || position_i_gain > 16383) {
      throw std::runtime_error("position_i_gain must be between 0 and 16383");
    }

    if (position_d_gain < 0 || position_d_gain > 16383) {
      throw std::runtime_error("position_d_gain must be between 0 and 16383");
    }

    if (goal_current < 0 || goal_current > std::numeric_limits<uint16_t>::max()) {
      throw std::runtime_error("goal_current must fit in an unsigned 16-bit value");
    }

    if (
      profile_acceleration < 0 ||
      static_cast<uint64_t>(profile_acceleration) > std::numeric_limits<uint32_t>::max()) {
      throw std::runtime_error("profile_acceleration must fit in an unsigned 32-bit value");
    }

    if (
      profile_velocity < 0 ||
      static_cast<uint64_t>(profile_velocity) > std::numeric_limits<uint32_t>::max()) {
      throw std::runtime_error("profile_velocity must fit in an unsigned 32-bit value");
    }

    if (position_tolerance_ < 0 || position_tolerance_ > 4095) {
      throw std::runtime_error("position_tolerance must be between 0 and 4095");
    }

    if (settled_velocity_raw_ < 0 || settled_velocity_raw_ > std::numeric_limits<int32_t>::max()) {
      throw std::runtime_error("settled_velocity_raw must be between 0 and INT32_MAX");
    }

    if (
      required_settled_samples < 1 ||
      required_settled_samples > std::numeric_limits<uint8_t>::max()) {
      throw std::runtime_error("required_settled_samples must be between 1 and 255");
    }

    if (motion_timeout_seconds < 1 || motion_timeout_seconds > 3600) {
      throw std::runtime_error("motion_timeout_seconds must be between 1 and 3600");
    }

    baud_rate_ = static_cast<int>(baud_rate);
    protocol_version_ = static_cast<float>(protocol_version);
    motor_id_ = static_cast<uint8_t>(motor_id);
    position_p_gain_ = static_cast<uint16_t>(position_p_gain);
    position_i_gain_ = static_cast<uint16_t>(position_i_gain);
    position_d_gain_ = static_cast<uint16_t>(position_d_gain);
    goal_current_ = static_cast<uint16_t>(goal_current);
    profile_acceleration_ = static_cast<uint32_t>(profile_acceleration);
    profile_velocity_ = static_cast<uint32_t>(profile_velocity);
    required_settled_samples_ = static_cast<uint8_t>(required_settled_samples);
    motion_timeout_seconds_ = std::chrono::seconds(motion_timeout_seconds);
  }

  void monitor_motion_test()
  {
    uint8_t hardware_error_status = 0;
    uint8_t moving_status = 0;
    uint16_t raw_present_pwm = 0;
    uint16_t raw_present_current = 0;
    uint32_t raw_present_velocity = 0;
    uint32_t raw_present_position = 0;
    uint32_t raw_position_trajectory = 0;

    const auto hardware_error_result = bus_->read_one_byte(
      motor_id_, thing_hardware::xl330::HARDWARE_ERROR_STATUS_ADDRESS, hardware_error_status);
    const auto moving_status_result =
      bus_->read_one_byte(motor_id_, thing_hardware::xl330::MOVING_STATUS_ADDRESS, moving_status);
    const auto pwm_result =
      bus_->read_two_bytes(motor_id_, thing_hardware::xl330::PRESENT_PWM_ADDRESS, raw_present_pwm);
    const auto current_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_CURRENT_ADDRESS, raw_present_current);
    const auto velocity_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_VELOCITY_ADDRESS, raw_present_velocity);
    const auto position_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_POSITION_ADDRESS, raw_present_position);
    const auto trajectory_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::POSITION_TRAJECTORY_ADDRESS, raw_position_trajectory);

    if (
      !hardware_error_result.success || !moving_status_result.success || !pwm_result.success ||
      !current_result.success || !velocity_result.success || !position_result.success ||
      !trajectory_result.success) {
      RCLCPP_ERROR(
        this->get_logger(),
        "Failed to monitor motion test: hardware_error=%s, moving_status=%s, pwm=%s, "
        "current=%s, velocity=%s, position=%s, trajectory=%s",
        hardware_error_result.success ? "ok" : hardware_error_result.error_message.c_str(),
        moving_status_result.success ? "ok" : moving_status_result.error_message.c_str(),
        pwm_result.success ? "ok" : pwm_result.error_message.c_str(),
        current_result.success ? "ok" : current_result.error_message.c_str(),
        velocity_result.success ? "ok" : velocity_result.error_message.c_str(),
        position_result.success ? "ok" : position_result.error_message.c_str(),
        trajectory_result.success ? "ok" : trajectory_result.error_message.c_str());
      stop_motion_test();
      return;
    }

    const int16_t present_pwm = static_cast<int16_t>(raw_present_pwm);
    const int16_t present_current = static_cast<int16_t>(raw_present_current);
    const int32_t present_velocity = static_cast<int32_t>(raw_present_velocity);
    const int32_t present_position = static_cast<int32_t>(raw_present_position);
    const int32_t position_trajectory = static_cast<int32_t>(raw_position_trajectory);
    const int64_t position_error =
      static_cast<int64_t>(test_goal_position_) - static_cast<int64_t>(present_position);
    const int64_t absolute_position_error = position_error >= 0 ? position_error : -position_error;
    const int64_t velocity = static_cast<int64_t>(present_velocity);
    const int64_t absolute_velocity = velocity >= 0 ? velocity : -velocity;
    const bool profile_ongoing = (moving_status & 0x02U) != 0U;

    if (!profile_ongoing && absolute_velocity <= settled_velocity_raw_) {
      if (settled_sample_count_ < required_settled_samples_) {
        ++settled_sample_count_;
      }
    } else {
      settled_sample_count_ = 0;
    }

    const double present_velocity_rpm =
      static_cast<double>(present_velocity) * thing_hardware::xl330::VELOCITY_RPM_UNIT;
    const double present_pwm_percent =
      static_cast<double>(present_pwm) * thing_hardware::xl330::PWM_PERCENT_UNIT;
    const auto elapsed = std::chrono::steady_clock::now() - motion_start_time_;
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();

    RCLCPP_INFO(
      this->get_logger(),
      "Motion monitoring: phase=%s, ID=%u, elapsed=%ld ms, goal=%d, trajectory=%d, position=%d, "
      "error=%ld pulse, moving_status=0x%02X, profile_ongoing=%s, in_position=%s, "
      "settled=%u/%u, pwm=%d (%.2f%%), current=%d mA, velocity=%.2f rpm",
      return_motion_started_ ? "return" : "forward", static_cast<unsigned int>(motor_id_),
      static_cast<long>(elapsed_ms), test_goal_position_, position_trajectory, present_position,
      static_cast<long>(absolute_position_error), static_cast<unsigned int>(moving_status),
      profile_ongoing ? "true" : "false", (moving_status & 0x01U) != 0U ? "true" : "false",
      static_cast<unsigned int>(settled_sample_count_),
      static_cast<unsigned int>(required_settled_samples_), static_cast<int>(present_pwm),
      present_pwm_percent, static_cast<int>(present_current), present_velocity_rpm);

    if (hardware_error_status != 0U) {
      RCLCPP_ERROR(
        this->get_logger(), "Hardware error during motion test: status=0x%02X",
        static_cast<unsigned int>(hardware_error_status));
      stop_motion_test();
      return;
    }

    if (settled_sample_count_ >= required_settled_samples_) {
      RCLCPP_INFO(
        this->get_logger(),
        "%s motion settled: goal=%d, position=%d, error=%ld pulse, precision=%s",
        return_motion_started_ ? "Return" : "Forward", test_goal_position_, present_position,
        static_cast<long>(absolute_position_error),
        absolute_position_error <= position_tolerance_ ? "passed" : "not_reached");

      if (!return_motion_started_) {
        if (!start_return_motion()) {
          stop_motion_test();
        }
        return;
      }

      stop_motion_test();
      return;
    }

    if (elapsed >= motion_timeout_seconds_) {
      if (!return_motion_started_) {
        RCLCPP_WARN(
          this->get_logger(),
          "Forward motion settled with residual error: goal=%d, position=%d, error=%ld pulse; "
          "starting return motion",
          test_goal_position_, present_position, static_cast<long>(absolute_position_error));

        if (!start_return_motion()) {
          stop_motion_test();
        }
        return;
      }

      RCLCPP_WARN(
        this->get_logger(), "Return motion test timed out: goal=%d, position=%d, error=%ld pulse",
        test_goal_position_, present_position, static_cast<long>(absolute_position_error));
      stop_motion_test();
    }
  }

  bool start_return_motion()
  {
    const auto write_i_gain_result = bus_->write_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_I_GAIN_ADDRESS, position_i_gain_);

    if (!write_i_gain_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write return Position I Gain: %s",
        write_i_gain_result.error_message.c_str());
      return false;
    }

    uint16_t readback_i_gain = 0;
    const auto readback_i_gain_result = bus_->read_two_bytes(
      motor_id_, thing_hardware::xl330::POSITION_I_GAIN_ADDRESS, readback_i_gain);

    if (!readback_i_gain_result.success || readback_i_gain != position_i_gain_) {
      RCLCPP_ERROR(
        this->get_logger(),
        "Failed to verify return Position I Gain: received=%u, expected=%u, error=%s",
        static_cast<unsigned int>(readback_i_gain), static_cast<unsigned int>(position_i_gain_),
        readback_i_gain_result.success ? "none" : readback_i_gain_result.error_message.c_str());
      return false;
    }

    const auto write_result = bus_->write_four_bytes(
      motor_id_, thing_hardware::xl330::GOAL_POSITION_ADDRESS,
      static_cast<uint32_t>(start_position_));

    if (!write_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to write return Goal Position: %s",
        write_result.error_message.c_str());
      return false;
    }

    uint32_t readback_goal_position = 0;
    const auto readback_result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::GOAL_POSITION_ADDRESS, readback_goal_position);

    if (
      !readback_result.success ||
      readback_goal_position != static_cast<uint32_t>(start_position_)) {
      RCLCPP_ERROR(
        this->get_logger(),
        "Failed to verify return Goal Position: received=%u, expected=%d, error=%s",
        static_cast<unsigned int>(readback_goal_position), start_position_,
        readback_result.success ? "none" : readback_result.error_message.c_str());
      return false;
    }

    return_motion_started_ = true;
    test_goal_position_ = start_position_;
    settled_sample_count_ = 0;
    motion_start_time_ = std::chrono::steady_clock::now();

    RCLCPP_WARN(
      this->get_logger(),
      "Return motion test started: ID=%u, position_i_gain=%u, goal=%d pulse; "
      "monitoring with a %ld second timeout",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(readback_i_gain),
      test_goal_position_, static_cast<long>(motion_timeout_seconds_.count()));
    return true;
  }

  void stop_motion_test()
  {
    if (!disable_torque()) {
      return;
    }

    if (motion_timer_) {
      motion_timer_->cancel();
    }
  }

  bool disable_torque()
  {
    if (!torque_enabled_ || !bus_) {
      return true;
    }

    const auto result =
      bus_->write_one_byte(motor_id_, thing_hardware::xl330::TORQUE_ENABLE_ADDRESS, 0U);

    if (!result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to disable Torque: %s", result.error_message.c_str());
      return false;
    }

    torque_enabled_ = false;
    RCLCPP_INFO(
      this->get_logger(), "Torque disabled after motion test: ID=%u",
      static_cast<unsigned int>(motor_id_));
    return true;
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

  std::unique_ptr<thing_hardware::DynamixelBus> bus_;
  rclcpp::TimerBase::SharedPtr motion_timer_;
  std::chrono::steady_clock::time_point motion_start_time_;
  int32_t start_position_{0};
  int32_t test_goal_position_{0};
  uint8_t settled_sample_count_{0};
  bool return_motion_started_{false};
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
