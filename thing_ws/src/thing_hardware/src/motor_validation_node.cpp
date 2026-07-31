#include <cstdint>
#include <memory>
#include <string>

#include "dynamixel_sdk/packet_handler.h"
#include "dynamixel_sdk/port_handler.h"
#include "rclcpp/rclcpp.hpp"

class MotorValidatorNode : public rclcpp::Node
{
public:
  MotorValidatorNode() : Node("motor_validator")
  {
    RCLCPP_INFO(
      this->get_logger(), "Device: %s, baud rate: %d, protocol: %.1f, motor ID: %u",
      device_name_.c_str(), baud_rate_, protocol_version_, static_cast<unsigned int>(motor_id_));

    // ==== port_handler_ init ====
    port_handler_.reset(dynamixel::PortHandler::getPortHandler(device_name_.c_str()));

    if (!port_handler_) {
      RCLCPP_ERROR(this->get_logger(), "Failed to create PortHandler");
      return;
    }

    if (!port_handler_->openPort()) {
      RCLCPP_ERROR(this->get_logger(), "Failed to open port: %s", device_name_.c_str());
      return;
    }

    RCLCPP_INFO(this->get_logger(), "Port opened: %s", device_name_.c_str());

    if (!port_handler_->setBaudRate(baud_rate_)) {
      RCLCPP_ERROR(this->get_logger(), "Failed to set baud rate: %d", baud_rate_);
      return;
    }

    RCLCPP_INFO(this->get_logger(), "Baud rate configured: %d", baud_rate_);
    // ============================

    // ==== packet_handler_ init ====
    packet_handler_ = dynamixel::PacketHandler::getPacketHandler(protocol_version_);

    if (!packet_handler_) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to get PacketHandler for protocol %.1f", protocol_version_);
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "PacketHandler initialized for protocol %.1f",
      packet_handler_->getProtocolVersion());
    // ==============================

    // ==== ping check ====
    uint16_t model_number = 0;
    uint8_t dynamixel_error = 0;

    const int communication_result =
      packet_handler_->ping(port_handler_.get(), motor_id_, &model_number, &dynamixel_error);

    if (communication_result != COMM_SUCCESS) {
      RCLCPP_ERROR(
        this->get_logger(), "Ping failed for ID %u: %s", static_cast<unsigned int>(motor_id_),
        packet_handler_->getTxRxResult(communication_result));
      return;
    }

    if (dynamixel_error != 0) {
      RCLCPP_ERROR(
        this->get_logger(), "DYNAMIXEL ID %u returned an error: %s",
        static_cast<unsigned int>(motor_id_), packet_handler_->getRxPacketError(dynamixel_error));
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Ping succeeded: ID=%u, model number=%u",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(model_number));

    if (model_number != EXPECTED_MODEL_NUMBER) {
      RCLCPP_WARN(
        this->get_logger(), "Unexpected model: expected=%u, received=%u",
        static_cast<unsigned int>(EXPECTED_MODEL_NUMBER), static_cast<unsigned int>(model_number));
    }
    // ====================

    // ==== hardware error status read ====
    uint8_t hardware_error_status = 0;
    if (!read_one_byte(
          HARDWARE_ERROR_STATUS_ADDRESS, hardware_error_status, "Hardware Error Status")) {
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Hardware Error Status: ID=%u, status=0x%02X",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(hardware_error_status));
    // ====================================

    // ==== present temperature read ====
    uint8_t present_temperature = 0;

    if (!read_one_byte(PRESENT_TEMPERATURE_ADDRESS, present_temperature, "Present Temperature")) {
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Present Temperature: ID=%u, temperature=%u degC",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(present_temperature));
    // ==================================
  }

private:
  static constexpr uint16_t EXPECTED_MODEL_NUMBER = 1200;
  static constexpr uint16_t HARDWARE_ERROR_STATUS_ADDRESS = 70;
  static constexpr uint16_t PRESENT_TEMPERATURE_ADDRESS = 146;

  bool read_one_byte(uint16_t address, uint8_t & value, const std::string & item_name)
  {
    uint8_t dynamixel_error = 0;

    const int communication_result = packet_handler_->read1ByteTxRx(
      port_handler_.get(), motor_id_, address, &value, &dynamixel_error);

    if (communication_result != COMM_SUCCESS) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read %s: %s", item_name.c_str(),
        packet_handler_->getTxRxResult(communication_result));
      return false;
    }

    if (dynamixel_error != 0) {
      RCLCPP_ERROR(
        this->get_logger(), "DYNAMIXEL error while reading %s: %s", item_name.c_str(),
        packet_handler_->getRxPacketError(dynamixel_error));
      return false;
    }

    return true;
  }

  std::string device_name_{
    "/dev/serial/by-id/"
    "usb-FTDI_USB__-__Serial_Converter_FTBIN51S-if00-port0"};
  int baud_rate_{57600};
  float protocol_version_{2.0F};
  uint8_t motor_id_{3};

  std::unique_ptr<dynamixel::PortHandler> port_handler_;
  dynamixel::PacketHandler * packet_handler_{nullptr};
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);

  auto node = std::make_shared<MotorValidatorNode>();
  rclcpp::spin(node);

  rclcpp::shutdown();
  return 0;
}
