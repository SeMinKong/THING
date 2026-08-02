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
      this->get_logger(), "Hardware Error Status: ID=%u, status=0x%02X",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(hardware_error_status));
    // ====================================

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
      this->get_logger(), "Present Temperature: ID=%u, temperature=%u degC",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(present_temperature));
    // ==================================

    // ==== raw input voltage read ====
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
      this->get_logger(), "Present Input Voltage: ID=%u, voltage=%.1f V",
      static_cast<unsigned int>(motor_id_), input_voltage);
    // ================================

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
      this->get_logger(), "Present Position: ID=%u, position=%d pulse (%.2f deg)",
      static_cast<unsigned int>(motor_id_), present_position, present_position_degrees);
    // ===============================

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
      this->get_logger(), "Torque Enable: ID=%u, enabled=%s, raw=%u",
      static_cast<unsigned int>(motor_id_), torque_enable == 1U ? "true" : "false",
      static_cast<unsigned int>(torque_enable));
    // ============================

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
      this->get_logger(), "Operating Mode: ID=%u, mode=%u", static_cast<unsigned int>(motor_id_),
      static_cast<unsigned int>(operating_mode));
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
      this->get_logger(), "Current Limit: ID=%u, raw=%u, limit=%.1f mA",
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
      this->get_logger(), "Velocity Limit: ID=%u, raw=%u, limit=%.2f rpm",
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
      this->get_logger(), "Max Position Limit: ID=%u, limit=%u pulse (%.2f deg)",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_max_position_limit),
      max_position_limit_degrees);
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
      this->get_logger(), "Min Position Limit: ID=%u, limit=%u pulse (%.2f deg)",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(raw_min_position_limit),
      min_position_limit_degrees);
    // =====================================
  }

private:
  std::string device_name_{
    "/dev/serial/by-id/"
    "usb-FTDI_USB__-__Serial_Converter_FTBIN51S-if00-port0"};
  int baud_rate_{57600};
  float protocol_version_{2.0F};
  uint8_t motor_id_{3};

  std::unique_ptr<thing_hardware::DynamixelBus> bus_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);

  auto node = std::make_shared<MotorValidatorNode>();
  rclcpp::spin(node);

  rclcpp::shutdown();
  return 0;
}
