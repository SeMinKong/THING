#include "thing_hardware/dynamixel_bus.hpp"

#include <algorithm>
#include <utility>

#include "dynamixel_sdk/group_sync_read.h"
#include "dynamixel_sdk/group_sync_write.h"
#include "thing_hardware/xl330_control_table.hpp"

namespace thing_hardware
{

namespace
{
constexpr uint16_t PRESENT_BLOCK_START_ADDRESS = xl330::PRESENT_CURRENT_ADDRESS;
constexpr uint16_t PRESENT_BLOCK_LENGTH =
  xl330::PRESENT_TEMPERATURE_ADDRESS - PRESENT_BLOCK_START_ADDRESS + 1;
constexpr uint16_t STATUS_BLOCK_START_ADDRESS = xl330::TORQUE_ENABLE_ADDRESS;
constexpr uint16_t STATUS_BLOCK_LENGTH =
  xl330::HARDWARE_ERROR_STATUS_ADDRESS - STATUS_BLOCK_START_ADDRESS + 1;

void append_error(MotorStatusReadResult & result, const std::string & error)
{
  result.result.success = false;
  if (!result.result.error_message.empty()) {
    result.result.error_message += "; ";
  }
  result.result.error_message += error;
}
}  // namespace

DynamixelBus::DynamixelBus(std::string device_name, int baud_rate, float protocol_version)
: device_name_(std::move(device_name)), baud_rate_(baud_rate), protocol_version_(protocol_version)
{
}

DriverResult DynamixelBus::initialize()
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
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
  const std::lock_guard<std::mutex> lock(port_mutex_);
  uint8_t dynamixel_error = 0;

  const int communication_result =
    packet_handler_->ping(port_handler_.get(), motor_id, &model_number, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::read_one_byte(uint8_t motor_id, uint16_t address, uint8_t & value)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->read1ByteTxRx(
    port_handler_.get(), motor_id, address, &value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::read_two_bytes(uint8_t motor_id, uint16_t address, uint16_t & value)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->read2ByteTxRx(
    port_handler_.get(), motor_id, address, &value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::read_four_bytes(uint8_t motor_id, uint16_t address, uint32_t & value)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->read4ByteTxRx(
    port_handler_.get(), motor_id, address, &value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

std::vector<MotorStatusReadResult> DynamixelBus::sync_read_motor_status(
  const std::vector<uint8_t> & motor_ids)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  std::vector<MotorStatusReadResult> results;
  results.reserve(motor_ids.size());
  for (const uint8_t motor_id : motor_ids) {
    results.push_back({motor_id, {}, {true, ""}});
  }

  const auto run_sync_read = [this, &results](
                               dynamixel::GroupSyncRead & group, uint16_t address, uint16_t length,
                               const std::string & label, const auto & extract_data) {
    for (auto & result : results) {
      if (!group.addParam(result.motor_id)) {
        append_error(result, label + " addParam failed");
      }
    }

    const int communication_result = group.txRxPacket();
    if (communication_result != COMM_SUCCESS) {
      const std::string error = packet_handler_->getTxRxResult(communication_result);
      for (auto & result : results) {
        append_error(result, label + ": " + error);
      }
      return;
    }

    for (auto & result : results) {
      if (!group.isAvailable(result.motor_id, address, length)) {
        append_error(result, label + " data unavailable");
        continue;
      }
      uint8_t dynamixel_error = 0;
      if (group.getError(result.motor_id, &dynamixel_error) && dynamixel_error != 0) {
        append_error(result, label + ": " + packet_handler_->getRxPacketError(dynamixel_error));
        continue;
      }
      extract_data(group, result);
    }
  };

  dynamixel::GroupSyncRead status_group(
    port_handler_.get(), packet_handler_, STATUS_BLOCK_START_ADDRESS, STATUS_BLOCK_LENGTH);
  run_sync_read(
    status_group, STATUS_BLOCK_START_ADDRESS, STATUS_BLOCK_LENGTH, "status",
    [](dynamixel::GroupSyncRead & group, MotorStatusReadResult & result) {
      result.value.torque_enabled =
        static_cast<uint8_t>(group.getData(result.motor_id, xl330::TORQUE_ENABLE_ADDRESS, 1));
      result.value.hardware_error = static_cast<uint8_t>(
        group.getData(result.motor_id, xl330::HARDWARE_ERROR_STATUS_ADDRESS, 1));
    });

  dynamixel::GroupSyncRead goal_position_group(
    port_handler_.get(), packet_handler_, xl330::GOAL_POSITION_ADDRESS, 4);
  run_sync_read(
    goal_position_group, xl330::GOAL_POSITION_ADDRESS, 4, "goal_position",
    [](dynamixel::GroupSyncRead & group, MotorStatusReadResult & result) {
      result.value.goal_position = group.getData(result.motor_id, xl330::GOAL_POSITION_ADDRESS, 4);
    });

  dynamixel::GroupSyncRead present_group(
    port_handler_.get(), packet_handler_, PRESENT_BLOCK_START_ADDRESS, PRESENT_BLOCK_LENGTH);
  run_sync_read(
    present_group, PRESENT_BLOCK_START_ADDRESS, PRESENT_BLOCK_LENGTH, "present_state",
    [](dynamixel::GroupSyncRead & group, MotorStatusReadResult & result) {
      result.value.present_current =
        static_cast<uint16_t>(group.getData(result.motor_id, xl330::PRESENT_CURRENT_ADDRESS, 2));
      result.value.present_velocity =
        group.getData(result.motor_id, xl330::PRESENT_VELOCITY_ADDRESS, 4);
      result.value.present_position =
        group.getData(result.motor_id, xl330::PRESENT_POSITION_ADDRESS, 4);
      result.value.present_voltage = static_cast<uint16_t>(
        group.getData(result.motor_id, xl330::PRESENT_INPUT_VOLTAGE_ADDRESS, 2));
      result.value.present_temperature =
        static_cast<uint8_t>(group.getData(result.motor_id, xl330::PRESENT_TEMPERATURE_ADDRESS, 1));
    });

  const bool bus_read_failed = std::any_of(
    results.begin(), results.end(),
    [](const MotorStatusReadResult & result) { return !result.result.success; });
  if (bus_read_failed) {
    for (auto & result : results) {
      if (result.result.success) {
        append_error(result, "bus sync read invalidated by another motor failure");
      }
    }
  }

  return results;
}

DriverResult DynamixelBus::sync_write_goal_positions(
  const std::vector<uint8_t> & motor_ids, const std::vector<uint32_t> & goal_positions)
{
  if (motor_ids.empty()) {
    return {false, "motor_ids must not be empty"};
  }
  if (motor_ids.size() != goal_positions.size()) {
    return {false, "motor_ids and goal_positions must have the same size"};
  }

  const std::lock_guard<std::mutex> lock(port_mutex_);
  dynamixel::GroupSyncWrite group(
    port_handler_.get(), packet_handler_, xl330::GOAL_POSITION_ADDRESS, 4);

  for (std::size_t index = 0; index < motor_ids.size(); ++index) {
    const uint32_t goal_position = goal_positions[index];
    uint8_t data[4] = {
      static_cast<uint8_t>(goal_position & 0xFFU),
      static_cast<uint8_t>((goal_position >> 8U) & 0xFFU),
      static_cast<uint8_t>((goal_position >> 16U) & 0xFFU),
      static_cast<uint8_t>((goal_position >> 24U) & 0xFFU)};

    if (!group.addParam(motor_ids[index], data)) {
      return {
        false,
        "Failed to add ID=" + std::to_string(motor_ids[index]) + " to goal position Sync Write"};
    }
  }

  const int communication_result = group.txPacket();
  if (communication_result != COMM_SUCCESS) {
    return {false, packet_handler_->getTxRxResult(communication_result)};
  }

  return {true, ""};
}

DriverResult DynamixelBus::write_one_byte(uint8_t motor_id, uint16_t address, uint8_t value)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->write1ByteTxRx(
    port_handler_.get(), motor_id, address, value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::write_one_byte_tx_only(uint8_t motor_id, uint16_t address, uint8_t value)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  return check_result(
    packet_handler_->write1ByteTxOnly(port_handler_.get(), motor_id, address, value), 0);
}

DriverResult DynamixelBus::write_two_bytes(uint8_t motor_id, uint16_t address, uint16_t value)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->write2ByteTxRx(
    port_handler_.get(), motor_id, address, value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

DriverResult DynamixelBus::write_four_bytes(uint8_t motor_id, uint16_t address, uint32_t value)
{
  const std::lock_guard<std::mutex> lock(port_mutex_);
  uint8_t dynamixel_error = 0;

  const int communication_result = packet_handler_->write4ByteTxRx(
    port_handler_.get(), motor_id, address, value, &dynamixel_error);

  return check_result(communication_result, dynamixel_error);
}

}  // namespace thing_hardware
