#include "thing_hardware/dynamixel_bus.hpp"

#include <utility>

namespace thing_hardware
{

DynamixelBus::DynamixelBus(std::string device_name, int baud_rate, float protocol_version)
: device_name_(std::move(device_name)), baud_rate_(baud_rate), protocol_version_(protocol_version)
{
}

DriverResult DynamixelBus::initialize()
{
  port_handler_.reset(dynamixel::PortHandler::getPortHandler(device_name_.c_str()));

  if (!port_handler_) {
    return {false, "Failed to create PortHandler"};
  }

  if (!port_handler_->openPort()) {
    return {false, "Failed to open port: " + device_name_};
  }

  if (!port_handler_->setBaudRate(baud_rate_)) {
    return {false, "Failed to set baud rate"};
  }

  packet_handler_ = dynamixel::PacketHandler::getPacketHandler(protocol_version_);

  if (!packet_handler_) {
    return {false, "Failed to create PacketHandler"};
  }

  return {true, ""};
}

DriverResult DynamixelBus::check_result(int communication_result, uint8_t dynamixel_error) const
{
  if (communication_result != COMM_SUCCESS) {
    return {false, packet_handler_->getTxRxResult(communication_result)};
  }

  if (dynamixel_error != 0) {
    return {false, packet_handler_->getRxPacketError(dynamixel_error)};
  }

  return {true, ""};
}

DriverResult DynamixelBus::ping(uint8_t motor_id, uint16_t & model_number)
{
  uint8_t dynamixel_error = 0;

  const int communication_result =
    packet_handler_->ping(port_handler_.get(), motor_id, &model_number, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::read_one_byte(uint8_t motor_id, uint16_t address, uint8_t & value)
{
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->read1ByteTxRx(
    port_handler_.get(), motor_id, address, &value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::read_two_bytes(uint8_t motor_id, uint16_t address, uint16_t & value)
{
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->read2ByteTxRx(
    port_handler_.get(), motor_id, address, &value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::read_four_bytes(uint8_t motor_id, uint16_t address, uint32_t & value)
{
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->read4ByteTxRx(
    port_handler_.get(), motor_id, address, &value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

}  // namespace thing_hardware
