#ifndef THING_HARDWARE__XL330_CONTROL_TABLE_HPP_
#define THING_HARDWARE__XL330_CONTROL_TABLE_HPP_

#include <cstdint>

namespace thing_hardware::xl330
{

inline constexpr uint16_t EXPECTED_MODEL_NUMBER = 1200;

inline constexpr uint16_t TORQUE_ENABLE_ADDRESS = 64;
inline constexpr uint16_t HARDWARE_ERROR_STATUS_ADDRESS = 70;
inline constexpr uint16_t PRESENT_CURRENT_ADDRESS = 126;
inline constexpr uint16_t PRESENT_VELOCITY_ADDRESS = 128;
inline constexpr uint16_t PRESENT_POSITION_ADDRESS = 132;
inline constexpr uint16_t PRESENT_INPUT_VOLTAGE_ADDRESS = 144;
inline constexpr uint16_t PRESENT_TEMPERATURE_ADDRESS = 146;

inline constexpr double INPUT_VOLTAGE_UNIT = 0.1;
inline constexpr double VELOCITY_RPM_UNIT = 0.229;
inline constexpr double POSITION_DEGREE_UNIT = 0.088;

}  // namespace thing_hardware::xl330

#endif  // THING_HARDWARE__XL330_CONTROL_TABLE_HPP_
