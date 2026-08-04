#ifndef THING_HARDWARE__DYNAMIXEL_BUS_HPP_
#define THING_HARDWARE__DYNAMIXEL_BUS_HPP_

#include <cstdint>
#include <memory>
#include <string>

#include "dynamixel_sdk/packet_handler.h"
#include "dynamixel_sdk/port_handler.h"

namespace thing_hardware
{

struct DriverResult
{
  bool success;
  std::string error_message;
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

  DriverResult write_one_byte(uint8_t motor_id, uint16_t address, uint8_t value);
  DriverResult write_two_bytes(uint8_t motor_id, uint16_t address, uint16_t value);
  DriverResult write_four_bytes(uint8_t motor_id, uint16_t address, uint32_t value);

private:
  DriverResult check_result(int communication_result, uint8_t dynamixel_error) const;

  std::string device_name_;
  int baud_rate_;
  float protocol_version_;

  std::unique_ptr<dynamixel::PortHandler> port_handler_;
  dynamixel::PacketHandler * packet_handler_{nullptr};
};

}  // namespace thing_hardware

#endif  // THING_HARDWARE__DYNAMIXEL_BUS_HPP_
