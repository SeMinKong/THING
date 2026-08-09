#ifndef THING_HARDWARE__XL330_CONTROL_TABLE_HPP_
#define THING_HARDWARE__XL330_CONTROL_TABLE_HPP_

#include <cstdint>

namespace thing_hardware::xl330
{

inline constexpr uint16_t EXPECTED_MODEL_NUMBER = 1200;

inline constexpr uint16_t BAUD_RATE_ADDRESS = 8;
inline constexpr uint16_t DRIVE_MODE_ADDRESS = 10;
inline constexpr uint16_t OPERATING_MODE_ADDRESS = 11;
inline constexpr uint16_t CURRENT_LIMIT_ADDRESS = 38;
inline constexpr uint16_t VELOCITY_LIMIT_ADDRESS = 44;
inline constexpr uint16_t MAX_POSITION_LIMIT_ADDRESS = 48;
inline constexpr uint16_t MIN_POSITION_LIMIT_ADDRESS = 52;
inline constexpr uint16_t TORQUE_ENABLE_ADDRESS = 64;
inline constexpr uint16_t HARDWARE_ERROR_STATUS_ADDRESS = 70;
inline constexpr uint16_t POSITION_D_GAIN_ADDRESS = 80;
inline constexpr uint16_t POSITION_I_GAIN_ADDRESS = 82;
inline constexpr uint16_t POSITION_P_GAIN_ADDRESS = 84;
inline constexpr uint16_t GOAL_CURRENT_ADDRESS = 102;
inline constexpr uint16_t PROFILE_ACCELERATION_ADDRESS = 108;
inline constexpr uint16_t PROFILE_VELOCITY_ADDRESS = 112;
inline constexpr uint16_t GOAL_POSITION_ADDRESS = 116;
inline constexpr uint16_t MOVING_STATUS_ADDRESS = 123;
inline constexpr uint16_t PRESENT_PWM_ADDRESS = 124;
inline constexpr uint16_t PRESENT_CURRENT_ADDRESS = 126;
inline constexpr uint16_t PRESENT_VELOCITY_ADDRESS = 128;
inline constexpr uint16_t PRESENT_POSITION_ADDRESS = 132;
inline constexpr uint16_t POSITION_TRAJECTORY_ADDRESS = 140;
inline constexpr uint16_t PRESENT_INPUT_VOLTAGE_ADDRESS = 144;
inline constexpr uint16_t PRESENT_TEMPERATURE_ADDRESS = 146;

inline constexpr double INPUT_VOLTAGE_UNIT = 0.1;
inline constexpr double PWM_PERCENT_UNIT = 0.113;
inline constexpr double CURRENT_MILLIAMPERE_UNIT = 1.0;
inline constexpr double VELOCITY_RPM_UNIT = 0.229;
inline constexpr double POSITION_DEGREE_UNIT = 0.088;

}  // namespace thing_hardware::xl330

#endif  // THING_HARDWARE__XL330_CONTROL_TABLE_HPP_
