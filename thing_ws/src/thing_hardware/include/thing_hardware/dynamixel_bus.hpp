#ifndef THING_HARDWARE__DYNAMIXEL_BUS_HPP_
#define THING_HARDWARE__DYNAMIXEL_BUS_HPP_

#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "dynamixel_sdk/packet_handler.h"
#include "dynamixel_sdk/port_handler.h"

namespace thing_hardware
{

struct DriverResult
{
  bool success;
  std::string error_message;
};

struct MotorStatusRaw
{
  uint32_t goal_position{0};
  uint32_t present_position{0};
  uint32_t present_velocity{0};
  uint16_t present_current{0};
  uint16_t present_voltage{0};
  uint8_t present_temperature{0};
  uint8_t torque_enabled{0};
  uint8_t hardware_error{0};
};

struct MotorStatusReadResult
{
  uint8_t motor_id{0};
  MotorStatusRaw value;
  DriverResult result{true, ""};
};

class DynamixelBus
{
public:
  DynamixelBus(std::string device_name, int baud_rate, float protocol_version);

  DriverResult initialize();

  DriverResult ping(uint8_t motor_id, uint16_t & model_number);

  DriverResult read_one_byte(uint8_t motor_id, uint16_t address, uint8_t & value);
  DriverResult read_two_bytes(uint8_t motor_id, uint16_t address, uint16_t & value);
  DriverResult read_four_bytes(uint8_t motor_id, uint16_t address, uint32_t & value);

  std::vector<MotorStatusReadResult> sync_read_motor_status(const std::vector<uint8_t> & motor_ids);
  DriverResult sync_write_goal_positions(
    const std::vector<uint8_t> & motor_ids, const std::vector<uint32_t> & goal_positions);

  DriverResult write_one_byte(uint8_t motor_id, uint16_t address, uint8_t value);
  DriverResult write_one_byte_tx_only(uint8_t motor_id, uint16_t address, uint8_t value);
  DriverResult write_two_bytes(uint8_t motor_id, uint16_t address, uint16_t value);
  DriverResult write_four_bytes(uint8_t motor_id, uint16_t address, uint32_t value);

private:
  DriverResult check_result(int communication_result, uint8_t dynamixel_error) const;

  std::string device_name_;
  int baud_rate_;
  float protocol_version_;

  std::unique_ptr<dynamixel::PortHandler> port_handler_;
  dynamixel::PacketHandler * packet_handler_{nullptr};
  std::mutex port_mutex_;
};

}  // namespace thing_hardware

#endif  // THING_HARDWARE__DYNAMIXEL_BUS_HPP_
