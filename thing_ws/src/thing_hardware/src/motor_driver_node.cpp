#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "diagnostic_msgs/msg/diagnostic_array.hpp"
#include "diagnostic_msgs/msg/diagnostic_status.hpp"
#include "diagnostic_msgs/msg/key_value.hpp"
#include "rclcpp/rclcpp.hpp"
#include "thing_hardware/dynamixel_bus.hpp"
#include "thing_hardware/thumb_motion_controller.hpp"
#include "thing_hardware/xl330_control_table.hpp"
#include "thing_interfaces/msg/hand_command.hpp"
#include "thing_interfaces/msg/motor_state.hpp"
#include "thing_interfaces/msg/motor_status.hpp"
#include "thing_interfaces/msg/safety_state.hpp"

namespace thing_hardware
{

namespace
{
constexpr double TWO_PI = 6.28318530717958647692;
constexpr double POSITION_RADIAN_UNIT = TWO_PI / 4096.0;
constexpr double RPM_TO_RADIAN_PER_SECOND = TWO_PI / 60.0;
constexpr int64_t SLOW_OPERATION_THRESHOLD_MS = 300;

template <typename ValueT>
ValueT clamp(ValueT value, ValueT minimum, ValueT maximum)
{
  return std::max(minimum, std::min(value, maximum));
}
}  // namespace

struct AxisConfig
{
  uint8_t motor_id;
  std::string actuator_name;
  std::size_t bus_index;
  bool control_enabled;
  int32_t home_position;
  int32_t closed_position;
  int32_t safe_position;
  int32_t position_tolerance;
  uint16_t position_p_gain;
  uint16_t position_i_gain;
  uint16_t position_d_gain;
  uint16_t goal_current;
  uint32_t profile_acceleration;
  uint32_t profile_velocity;
};

struct BusRuntime
{
  std::string name;
  std::string device;
  std::unique_ptr<DynamixelBus> driver;
  bool initialized{false};
  uint32_t failed_read_count{0};
  std::string last_error;
};

class MotorDriverNode : public rclcpp::Node
{
public:
  MotorDriverNode()
  : Node("motor_driver_node"), last_command_time_(std::chrono::steady_clock::now())
  {
    load_parameters();
    initialize_hardware();

    status_publisher_ = create_publisher<thing_interfaces::msg::MotorStatus>(
      "/thing/motor_status", rclcpp::QoS(rclcpp::KeepLast(5)).reliable());
    diagnostic_publisher_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
      "/thing/diagnostics", rclcpp::QoS(rclcpp::KeepLast(10)).reliable());
    command_subscription_ = create_subscription<thing_interfaces::msg::HandCommand>(
      "/thing/command", rclcpp::QoS(rclcpp::KeepLast(1)).reliable(),
      [this](thing_interfaces::msg::HandCommand::ConstSharedPtr message) {
        receive_command(*message);
      });
    safety_subscription_ = create_subscription<thing_interfaces::msg::SafetyState>(
      "/thing/safety_state", rclcpp::QoS(rclcpp::KeepLast(1)).reliable().transient_local(),
      [this](thing_interfaces::msg::SafetyState::ConstSharedPtr message) {
        receive_safety_state(*message);
      });

    read_timer_ = create_wall_timer(read_period_, [this]() { publish_motor_status(); });
    write_timer_ = create_wall_timer(write_period_, [this]() { run_control_cycle(); });
    diagnostic_timer_ = create_wall_timer(diagnostic_period_, [this]() { publish_diagnostics(); });

    RCLCPP_WARN(
      get_logger(),
      "Motor driver started in %s mode: motors=%zu, buses=%zu, thumb_control=%s, read=%.1f Hz, "
      "write=%.1f Hz",
      integration_test_mode_ ? "four-finger integration test" : "production", axes_.size(),
      buses_.size(), thumb_control_enabled_ ? "enabled" : "disabled", read_rate_hz_,
      write_rate_hz_);
  }

  ~MotorDriverNode() override { disable_torque(); }

private:
  template <typename ValueT>
  std::vector<ValueT> declare_integer_array(const std::string & name)
  {
    const auto values = declare_parameter<std::vector<int64_t>>(name, std::vector<int64_t>{});
    std::vector<ValueT> converted;
    converted.reserve(values.size());
    for (const int64_t value : values) {
      converted.push_back(static_cast<ValueT>(value));
    }
    return converted;
  }

  std::vector<double> declare_double_array(const std::string & name)
  {
    return declare_parameter<std::vector<double>>(name, std::vector<double>{});
  }

  void load_parameters()
  {
    integration_test_mode_ = declare_parameter<bool>("integration_test_mode", false);
    const int64_t baud_rate = declare_parameter<int64_t>("baud_rate", 57600);
    const double protocol_version = declare_parameter<double>("protocol_version", 2.0);
    operating_mode_ = declare_parameter<int>("operating_mode", 5);
    read_rate_hz_ = declare_parameter<double>("read_rate_hz", 20.0);
    write_rate_hz_ = declare_parameter<double>("write_rate_hz", 50.0);
    safe_velocity_limit_ = declare_parameter<double>("safe_velocity_limit", 0.5);
    safe_motion_timeout_seconds_ = declare_parameter<double>("safe_motion_timeout_seconds", 2.5);
    const double diagnostic_rate_hz = declare_parameter<double>("diagnostic_rate_hz", 1.0);
    hardware_command_watchdog_ms_ = declare_parameter<int>("hardware_command_watchdog_ms", 500);

    const auto bus_names =
      declare_parameter<std::vector<std::string>>("bus_names", std::vector<std::string>{});
    const auto bus_devices =
      declare_parameter<std::vector<std::string>>("bus_devices", std::vector<std::string>{});
    const auto motor_ids = declare_integer_array<int64_t>("motor_ids");
    const auto controlled_motor_ids = declare_integer_array<int64_t>("controlled_motor_ids");
    const auto actuator_names =
      declare_parameter<std::vector<std::string>>("actuator_names", std::vector<std::string>{});
    const auto bus_indices = declare_integer_array<int64_t>("bus_indices");
    const auto home_positions = declare_integer_array<int32_t>("home_positions_raw");
    const auto closed_positions = declare_integer_array<int32_t>("closed_positions_raw");
    const auto safe_positions = declare_integer_array<int32_t>("safe_positions_raw");
    const auto position_tolerances = declare_integer_array<int32_t>("position_tolerances_raw");
    const auto position_p_gains = declare_integer_array<uint16_t>("position_p_gains");
    const auto position_i_gains = declare_integer_array<uint16_t>("position_i_gains");
    const auto position_d_gains = declare_integer_array<uint16_t>("position_d_gains");
    const auto goal_currents = declare_integer_array<uint16_t>("goal_currents_ma");
    const auto profile_accelerations = declare_integer_array<uint32_t>("profile_accelerations_raw");
    const auto profile_velocities = declare_integer_array<uint32_t>("profile_velocities_raw");

    const auto thumb_pose_names = declare_parameter<std::vector<std::string>>(
      "thumb_functional_pose_names", std::vector<std::string>{});
    const auto thumb_opposition = declare_double_array("thumb_functional_opposition");
    const auto thumb_abduction = declare_double_array("thumb_functional_abduction");
    const auto thumb_opposition_positions =
      declare_integer_array<int32_t>("thumb_opposition_positions_raw");
    const auto thumb_opposition_directions =
      declare_integer_array<int8_t>("thumb_opposition_approach_directions");
    const auto thumb_opposition_starts =
      declare_integer_array<int32_t>("thumb_opposition_approach_start_positions_raw");
    const auto thumb_abduction_positions =
      declare_integer_array<int32_t>("thumb_abduction_positions_raw");
    const auto thumb_abduction_directions =
      declare_integer_array<int8_t>("thumb_abduction_approach_directions");
    const auto thumb_abduction_starts =
      declare_integer_array<int32_t>("thumb_abduction_approach_start_positions_raw");
    const auto thumb_flex_homes = declare_integer_array<int32_t>("thumb_flex_home_positions_raw");
    const auto thumb_flex_closed =
      declare_integer_array<int32_t>("thumb_flex_closed_positions_raw");
    const auto thumb_opposition_reversals =
      declare_integer_array<int32_t>("thumb_opposition_reversal_positions_raw");
    const auto thumb_abduction_reversals =
      declare_integer_array<int32_t>("thumb_abduction_reversal_positions_raw");
    const int32_t thumb_flex_clearance =
      declare_parameter<int>("thumb_flex_clearance_position_raw", 3200);
    const int32_t thumb_position_tolerance =
      declare_parameter<int>("thumb_position_tolerance_raw", 50);
    const int32_t thumb_approach_tolerance =
      declare_parameter<int>("thumb_approach_tolerance_raw", 75);
    const double thumb_motion_timeout =
      declare_parameter<double>("thumb_motion_timeout_seconds", 6.0);
    thumb_pose_switch_margin_ = declare_parameter<double>("thumb_pose_switch_margin", 0.1);
    thumb_pose_stable_samples_ = declare_parameter<int>("thumb_pose_stable_samples", 3);

    if (baud_rate <= 0 || protocol_version <= 0.0 || operating_mode_ < 0 || operating_mode_ > 16) {
      throw std::invalid_argument("invalid DYNAMIXEL communication or operating mode parameter");
    }
    if (
      read_rate_hz_ <= 0.0 || write_rate_hz_ <= 0.0 || diagnostic_rate_hz <= 0.0 ||
      hardware_command_watchdog_ms_ <= 0) {
      throw std::invalid_argument("rates and hardware watchdog must be positive");
    }
    if (
      safe_velocity_limit_ <= 0.0 || safe_velocity_limit_ > 1.0 ||
      safe_motion_timeout_seconds_ <= 0.0 || safe_motion_timeout_seconds_ > 3.0) {
      throw std::invalid_argument(
        "safe_velocity_limit must be in (0.0, 1.0] and "
        "safe_motion_timeout_seconds must be in (0.0, 3.0]");
    }
    if (bus_names.empty() || bus_names.size() != bus_devices.size()) {
      throw std::invalid_argument("bus_names and bus_devices must have the same non-zero size");
    }

    const std::size_t axis_count = motor_ids.size();
    const std::vector<std::size_t> sizes = {
      actuator_names.size(),   bus_indices.size(),           home_positions.size(),
      closed_positions.size(), safe_positions.size(),        position_tolerances.size(),
      position_p_gains.size(), position_i_gains.size(),      position_d_gains.size(),
      goal_currents.size(),    profile_accelerations.size(), profile_velocities.size()};
    if (axis_count == 0 || std::any_of(sizes.begin(), sizes.end(), [axis_count](std::size_t size) {
          return size != axis_count;
        })) {
      throw std::invalid_argument("all axis parameter arrays must have the same non-zero size");
    }
    if (axis_count != 7) {
      throw std::invalid_argument("motor status requires exactly seven configured motors");
    }

    std::set<int64_t> controlled_ids;
    for (const int64_t motor_id : controlled_motor_ids) {
      if (motor_id < 0 || motor_id > 252 || !controlled_ids.insert(motor_id).second) {
        throw std::invalid_argument(
          "controlled_motor_ids must contain unique values from 0 through 252");
      }
    }
    if (controlled_ids.empty()) {
      throw std::invalid_argument("controlled_motor_ids must not be empty");
    }

    buses_.reserve(bus_names.size());
    for (std::size_t index = 0; index < bus_names.size(); ++index) {
      if (bus_names[index].empty() || bus_devices[index].empty()) {
        throw std::invalid_argument("bus name and device must not be empty");
      }
      buses_.push_back(
        {bus_names[index], bus_devices[index],
         std::make_unique<DynamixelBus>(
           bus_devices[index], static_cast<int>(baud_rate), static_cast<float>(protocol_version)),
         false, 0, ""});
    }

    std::set<int64_t> unique_ids;
    std::set<std::string> unique_names;
    axes_.reserve(axis_count);
    for (std::size_t index = 0; index < axis_count; ++index) {
      if (
        motor_ids[index] < 0 || motor_ids[index] > 252 ||
        !unique_ids.insert(motor_ids[index]).second) {
        throw std::invalid_argument("motor IDs must be unique values from 0 through 252");
      }
      if (actuator_names[index].empty() || !unique_names.insert(actuator_names[index]).second) {
        throw std::invalid_argument("actuator names must be non-empty and unique");
      }
      if (bus_indices[index] < 0 || static_cast<std::size_t>(bus_indices[index]) >= buses_.size()) {
        throw std::invalid_argument("bus index is outside the configured bus list");
      }
      const bool control_enabled = controlled_ids.count(motor_ids[index]) != 0;
      if (
        control_enabled &&
        (home_positions[index] == closed_positions[index] || position_tolerances[index] < 0)) {
        throw std::invalid_argument(
          "controlled axis endpoints must differ and tolerance must be non-negative");
      }

      axes_.push_back(
        {static_cast<uint8_t>(motor_ids[index]), actuator_names[index],
         static_cast<std::size_t>(bus_indices[index]), control_enabled, home_positions[index],
         closed_positions[index], safe_positions[index], position_tolerances[index],
         position_p_gains[index], position_i_gains[index], position_d_gains[index],
         goal_currents[index], profile_accelerations[index], profile_velocities[index]});
    }
    std::sort(axes_.begin(), axes_.end(), [](const AxisConfig & left, const AxisConfig & right) {
      return left.motor_id < right.motor_id;
    });
    for (const int64_t controlled_id : controlled_ids) {
      if (unique_ids.count(controlled_id) == 0) {
        throw std::invalid_argument("controlled_motor_ids contains an unconfigured motor ID");
      }
    }

    thumb_flex_index_ = axis_index("thumb_flex");
    thumb_abduction_index_ = axis_index("thumb_abduction");
    thumb_opposition_index_ = axis_index("thumb_opposition");
    thumb_control_enabled_ = axes_[thumb_flex_index_].control_enabled &&
                             axes_[thumb_abduction_index_].control_enabled &&
                             axes_[thumb_opposition_index_].control_enabled;

    const std::size_t thumb_pose_count = thumb_pose_names.size();
    const std::vector<std::size_t> thumb_pose_sizes = {
      thumb_opposition.size(),           thumb_abduction.size(),
      thumb_opposition_positions.size(), thumb_opposition_directions.size(),
      thumb_opposition_starts.size(),    thumb_abduction_positions.size(),
      thumb_abduction_directions.size(), thumb_abduction_starts.size(),
      thumb_flex_homes.size(),           thumb_flex_closed.size()};
    if (
      thumb_control_enabled_ &&
      (thumb_pose_count == 0 ||
       std::any_of(
         thumb_pose_sizes.begin(), thumb_pose_sizes.end(),
         [thumb_pose_count](std::size_t size) { return size != thumb_pose_count; }) ||
       thumb_opposition_reversals.size() != thumb_pose_count * thumb_pose_count ||
       thumb_abduction_reversals.size() != thumb_pose_count * thumb_pose_count)) {
      throw std::invalid_argument("thumb functional-pose parameter arrays have invalid sizes");
    }
    if (
      thumb_pose_switch_margin_ < 0.0 || thumb_pose_stable_samples_ <= 0 ||
      thumb_motion_timeout <= 0.0) {
      throw std::invalid_argument("thumb motion parameters are invalid");
    }

    thumb_poses_.reserve(thumb_pose_count);
    for (std::size_t index = 0; index < thumb_pose_count; ++index) {
      if (
        thumb_pose_names[index].empty() || thumb_opposition[index] < 0.0 ||
        thumb_opposition[index] > 1.0 || thumb_abduction[index] < 0.0 ||
        thumb_abduction[index] > 1.0 ||
        std::abs(static_cast<int>(thumb_opposition_directions[index])) != 1 ||
        std::abs(static_cast<int>(thumb_abduction_directions[index])) != 1) {
        throw std::invalid_argument("thumb functional pose contains an invalid value");
      }
      thumb_poses_.push_back(
        {thumb_pose_names[index], thumb_opposition[index], thumb_abduction[index],
         thumb_opposition_positions[index], thumb_opposition_directions[index],
         thumb_opposition_starts[index], thumb_abduction_positions[index],
         thumb_abduction_directions[index], thumb_abduction_starts[index], thumb_flex_homes[index],
         thumb_flex_closed[index]});
    }
    if (thumb_control_enabled_) {
      thumb_controller_ = std::make_unique<ThumbMotionController>(
        thumb_poses_, thumb_position_tolerance, thumb_approach_tolerance,
        std::chrono::duration<double>(thumb_motion_timeout), thumb_flex_clearance,
        thumb_opposition_reversals, thumb_abduction_reversals);
    }

    read_period_ = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::duration<double>(1.0 / read_rate_hz_));
    write_period_ = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::duration<double>(1.0 / write_rate_hz_));
    diagnostic_period_ = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::duration<double>(1.0 / diagnostic_rate_hz));
    commanded_positions_.resize(axes_.size());
    desired_positions_.resize(axes_.size());
    latest_present_positions_.resize(axes_.size());
    latest_goal_positions_.resize(axes_.size());
    latest_present_currents_ampere_.resize(axes_.size());
    latest_torque_enabled_.resize(axes_.size());
    latest_position_valid_.resize(axes_.size(), false);
  }

  std::size_t axis_index(const std::string & name) const
  {
    const auto iterator = std::find_if(axes_.begin(), axes_.end(), [&name](const auto & axis) {
      return axis.actuator_name == name;
    });
    if (iterator == axes_.end()) {
      throw std::invalid_argument("required actuator is missing: " + name);
    }
    return static_cast<std::size_t>(std::distance(axes_.begin(), iterator));
  }

  void require_write(const DriverResult & result, const std::string & operation)
  {
    if (!result.success) {
      throw std::runtime_error(operation + ": " + result.error_message);
    }
  }

  void initialize_hardware()
  {
    for (auto & bus : buses_) {
      const DriverResult result = bus.driver->initialize();
      bus.initialized = result.success;
      bus.last_error = result.error_message;
      if (!result.success) {
        throw std::runtime_error("failed to initialize " + bus.name + ": " + result.error_message);
      }
      RCLCPP_INFO(
        get_logger(), "DYNAMIXEL bus initialized: %s (%s)", bus.name.c_str(), bus.device.c_str());
    }

    for (const auto & axis : axes_) {
      auto & driver = *buses_[axis.bus_index].driver;
      uint16_t model_number = 0;
      require_write(
        driver.ping(axis.motor_id, model_number),
        "failed to ping ID=" + std::to_string(axis.motor_id));
      if (model_number != xl330::EXPECTED_MODEL_NUMBER) {
        throw std::runtime_error(
          "unexpected model number for ID=" + std::to_string(axis.motor_id) +
          ": expected=" + std::to_string(xl330::EXPECTED_MODEL_NUMBER) +
          ", actual=" + std::to_string(model_number));
      }
      require_write(
        driver.write_one_byte(axis.motor_id, xl330::TORQUE_ENABLE_ADDRESS, 0),
        "failed to disable torque for ID=" + std::to_string(axis.motor_id));
      if (!axis.control_enabled) {
        RCLCPP_INFO(
          get_logger(), "Motor monitored with torque disabled: ID=%u, axis=%s, model=%u",
          static_cast<unsigned int>(axis.motor_id), axis.actuator_name.c_str(),
          static_cast<unsigned int>(model_number));
        continue;
      }
      require_write(
        driver.write_one_byte(
          axis.motor_id, xl330::OPERATING_MODE_ADDRESS, static_cast<uint8_t>(operating_mode_)),
        "failed to set operating mode for ID=" + std::to_string(axis.motor_id));
      require_write(
        driver.write_two_bytes(axis.motor_id, xl330::POSITION_P_GAIN_ADDRESS, axis.position_p_gain),
        "failed to set P gain for ID=" + std::to_string(axis.motor_id));
      require_write(
        driver.write_two_bytes(axis.motor_id, xl330::POSITION_I_GAIN_ADDRESS, axis.position_i_gain),
        "failed to set I gain for ID=" + std::to_string(axis.motor_id));
      require_write(
        driver.write_two_bytes(axis.motor_id, xl330::POSITION_D_GAIN_ADDRESS, axis.position_d_gain),
        "failed to set D gain for ID=" + std::to_string(axis.motor_id));
      require_write(
        driver.write_two_bytes(axis.motor_id, xl330::GOAL_CURRENT_ADDRESS, axis.goal_current),
        "failed to set goal current for ID=" + std::to_string(axis.motor_id));
      require_write(
        driver.write_four_bytes(
          axis.motor_id, xl330::PROFILE_ACCELERATION_ADDRESS, axis.profile_acceleration),
        "failed to set profile acceleration for ID=" + std::to_string(axis.motor_id));
      require_write(
        driver.write_four_bytes(
          axis.motor_id, xl330::PROFILE_VELOCITY_ADDRESS, axis.profile_velocity),
        "failed to set profile velocity for ID=" + std::to_string(axis.motor_id));
      RCLCPP_INFO(
        get_logger(), "Motor configured: ID=%u, axis=%s, model=%u",
        static_cast<unsigned int>(axis.motor_id), axis.actuator_name.c_str(),
        static_cast<unsigned int>(model_number));
    }
  }

  double axis_command(const thing_interfaces::msg::HandCommand & command, const std::string & name)
  {
    if (name == "ring_flex") {
      return command.ring_flex;
    }
    if (name == "middle_flex") {
      return command.middle_flex;
    }
    if (name == "index_flex") {
      return command.index_flex;
    }
    if (name == "little_flex") {
      return command.little_flex;
    }
    if (name == "thumb_flex") {
      return command.thumb_flex;
    }
    if (name == "thumb_abduction") {
      return command.thumb_abd;
    }
    if (name == "thumb_opposition") {
      return command.thumb_opp;
    }
    throw std::invalid_argument("unsupported actuator name: " + name);
  }

  double pose_distance(std::size_t pose_index, double opposition, double abduction) const
  {
    const auto & pose = thumb_poses_.at(pose_index);
    return std::hypot(pose.opposition - opposition, pose.abduction - abduction);
  }

  std::size_t nearest_thumb_pose(double opposition, double abduction) const
  {
    std::size_t nearest = 0;
    double nearest_distance = pose_distance(0, opposition, abduction);
    for (std::size_t index = 1; index < thumb_poses_.size(); ++index) {
      const double distance = pose_distance(index, opposition, abduction);
      if (distance < nearest_distance) {
        nearest = index;
        nearest_distance = distance;
      }
    }
    return nearest;
  }

  std::size_t nearest_thumb_pose_from_raw() const
  {
    std::size_t nearest = 0;
    double nearest_distance = std::numeric_limits<double>::infinity();
    for (std::size_t index = 0; index < thumb_poses_.size(); ++index) {
      const auto & pose = thumb_poses_[index];
      const double distance = std::hypot(
        static_cast<double>(
          latest_present_positions_[thumb_opposition_index_] - pose.opposition_position),
        static_cast<double>(
          latest_present_positions_[thumb_abduction_index_] - pose.abduction_position));
      if (distance < nearest_distance) {
        nearest = index;
        nearest_distance = distance;
      }
    }
    return nearest;
  }

  void infer_thumb_state_from_present()
  {
    if (!thumb_control_enabled_ || !thumb_positions_valid()) {
      thumb_pose_initialized_ = false;
      return;
    }
    thumb_current_pose_index_ = nearest_thumb_pose_from_raw();
    thumb_requested_pose_index_ = thumb_current_pose_index_;
    thumb_candidate_pose_index_ = thumb_current_pose_index_;
    thumb_candidate_samples_ = 0;
    thumb_pose_initialized_ = true;
    const auto & pose = thumb_poses_[thumb_current_pose_index_];
    thumb_completed_flex_ = clamp(
      static_cast<double>(latest_present_positions_[thumb_flex_index_] - pose.flex_home) /
        static_cast<double>(pose.flex_closed - pose.flex_home),
      0.0, 1.0);
    thumb_active_target_flex_ = thumb_completed_flex_;
  }

  std::size_t neutral_thumb_pose_index() const
  {
    const auto neutral = std::find_if(
      thumb_poses_.begin(), thumb_poses_.end(),
      [](const auto & pose) { return pose.name == "neutral"; });
    if (neutral == thumb_poses_.end()) {
      throw std::runtime_error("thumb safe motion requires a neutral functional pose");
    }
    return static_cast<std::size_t>(std::distance(thumb_poses_.begin(), neutral));
  }

  void update_thumb_pose_candidate(double opposition, double abduction, bool immediate)
  {
    const std::size_t candidate = nearest_thumb_pose(opposition, abduction);
    if (candidate != thumb_candidate_pose_index_) {
      thumb_candidate_pose_index_ = candidate;
      thumb_candidate_samples_ = 1;
    } else {
      ++thumb_candidate_samples_;
    }

    if (!immediate && thumb_candidate_samples_ < thumb_pose_stable_samples_) {
      return;
    }
    if (!thumb_pose_initialized_) {
      thumb_requested_pose_index_ = candidate;
      return;
    }
    const double candidate_distance = pose_distance(candidate, opposition, abduction);
    const double current_distance =
      pose_distance(thumb_requested_pose_index_, opposition, abduction);
    if (
      candidate == thumb_requested_pose_index_ ||
      candidate_distance + thumb_pose_switch_margin_ < current_distance) {
      thumb_requested_pose_index_ = candidate;
    }
  }

  int32_t normalized_position(const AxisConfig & axis, double normalized) const
  {
    return static_cast<int32_t>(
      std::lround(axis.home_position + normalized * (axis.closed_position - axis.home_position)));
  }

  void receive_command(const thing_interfaces::msg::HandCommand & command)
  {
    if (safety_state_.load() != thing_interfaces::msg::SafetyState::RUN) {
      return;
    }
    if (
      !std::isfinite(command.speed_limit) || command.speed_limit <= 0.0F ||
      command.speed_limit > 1.0F) {
      RCLCPP_WARN(get_logger(), "Rejected command with invalid speed_limit");
      return;
    }

    std::vector<int32_t> validated_positions(axes_.size());
    for (std::size_t index = 0; index < axes_.size(); ++index) {
      if (!axes_[index].control_enabled) {
        validated_positions[index] = desired_positions_[index];
        continue;
      }
      const double normalized = axis_command(command, axes_[index].actuator_name);
      if (!std::isfinite(normalized) || normalized < 0.0 || normalized > 1.0) {
        RCLCPP_WARN(get_logger(), "Rejected command with invalid axis value");
        return;
      }
      validated_positions[index] =
        thumb_control_enabled_ && (index == thumb_flex_index_ || index == thumb_abduction_index_ ||
                                   index == thumb_opposition_index_)
          ? desired_positions_[index]
          : normalized_position(axes_[index], normalized);
    }

    std::lock_guard<std::mutex> lock(command_mutex_);
    desired_positions_ = std::move(validated_positions);
    if (thumb_control_enabled_) {
      latest_thumb_flex_ = command.thumb_flex;
      const bool discrete_manual_command =
        command.source == thing_interfaces::msg::HandCommand::SOURCE_GESTURE ||
        command.source == thing_interfaces::msg::HandCommand::SOURCE_SEQUENCE;
      update_thumb_pose_candidate(command.thumb_opp, command.thumb_abd, discrete_manual_command);
      if (thumb_failure_latched_) {
        const bool repeats_failed_target = thumb_requested_pose_index_ == thumb_failed_pose_index_;
        if (repeats_failed_target) {
          return;
        }
        thumb_failure_latched_ = false;
        thumb_controller_->reset();
        thumb_pose_initialized_ = false;
        thumb_candidate_samples_ = 0;
        RCLCPP_INFO(get_logger(), "New thumb target cleared the previous transition failure");
      }
    }
    command_speed_limit_ = command.speed_limit;
    last_command_time_ = std::chrono::steady_clock::now();
    command_available_ = true;
  }

  void receive_safety_state(const thing_interfaces::msg::SafetyState & message)
  {
    const uint8_t previous = safety_state_.exchange(message.state);
    if (
      message.state != thing_interfaces::msg::SafetyState::RUN &&
      previous == thing_interfaces::msg::SafetyState::RUN) {
      std::lock_guard<std::mutex> lock(command_mutex_);
      command_available_ = false;
      if (thumb_control_enabled_ && thumb_controller_) {
        thumb_controller_->reset();
        thumb_pose_initialized_ = false;
        thumb_candidate_samples_ = 0;
      }
    }

    if (message.state == thing_interfaces::msg::SafetyState::SAFE) {
      if (previous != thing_interfaces::msg::SafetyState::SAFE) {
        safe_action_active_ = true;
        safe_action_started_at_ = std::chrono::steady_clock::now();
        if (thumb_control_enabled_ && thumb_controller_) {
          thumb_controller_->reset();
          thumb_failure_latched_ = false;
          infer_thumb_state_from_present();
          try {
            thumb_requested_pose_index_ = neutral_thumb_pose_index();
            latest_thumb_flex_ = 0.0;
          } catch (const std::exception & exception) {
            RCLCPP_ERROR(get_logger(), "%s", exception.what());
            safe_action_active_ = false;
            disable_torque();
            return;
          }
        }
        reset_motion_clock();
        RCLCPP_WARN(
          get_logger(),
          "SAFE action started: moving controlled motors to safe positions at velocity limit %.2f",
          safe_velocity_limit_);
      }
      return;
    }

    safe_action_active_ = false;
    if (message.state == thing_interfaces::msg::SafetyState::RUN && previous != message.state) {
      reset_motion_clock();
    } else if (message.state != thing_interfaces::msg::SafetyState::RUN) {
      motion_clock_initialized_ = false;
    }

    if (
      message.state == thing_interfaces::msg::SafetyState::INIT ||
      message.state == thing_interfaces::msg::SafetyState::READY ||
      message.state == thing_interfaces::msg::SafetyState::FAULT ||
      message.state == thing_interfaces::msg::SafetyState::ESTOP) {
      disable_torque();
    }
  }

  void enable_torque()
  {
    if (torque_enabled_) {
      return;
    }
    if (!write_positions(commanded_positions_)) {
      return;
    }
    for (const auto & axis : axes_) {
      if (!axis.control_enabled) {
        continue;
      }
      const DriverResult result = buses_[axis.bus_index].driver->write_one_byte(
        axis.motor_id, xl330::TORQUE_ENABLE_ADDRESS, 1);
      if (!result.success) {
        RCLCPP_ERROR(
          get_logger(), "Failed to enable torque: ID=%u, error=%s",
          static_cast<unsigned int>(axis.motor_id), result.error_message.c_str());
        disable_torque();
        return;
      }
    }
    torque_enabled_ = true;
    torque_off_confirmed_ = false;
    RCLCPP_INFO(get_logger(), "Torque enabled for configured motors");
  }

  void disable_torque()
  {
    // SafetyState is a heartbeat. Once a complete status read or a successful
    // write has confirmed that every controlled motor is already torque-off,
    // do not send seven acknowledged writes again for every heartbeat.
    if (torque_off_confirmed_) {
      return;
    }
    bool all_succeeded = true;
    for (const auto & axis : axes_) {
      if (!buses_[axis.bus_index].initialized) {
        all_succeeded = false;
        continue;
      }
      const DriverResult result = buses_[axis.bus_index].driver->write_one_byte(
        axis.motor_id, xl330::TORQUE_ENABLE_ADDRESS, 0);
      if (!result.success) {
        all_succeeded = false;
        RCLCPP_ERROR(
          get_logger(), "Failed to disable torque: ID=%u, error=%s",
          static_cast<unsigned int>(axis.motor_id), result.error_message.c_str());
      }
    }
    if (all_succeeded) {
      torque_enabled_ = false;
      torque_off_confirmed_ = true;
    }
  }

  int32_t max_step_for_axis(
    const AxisConfig & axis, double speed_limit, double elapsed_seconds) const
  {
    const double pulses_per_second =
      axis.profile_velocity * xl330::VELOCITY_RPM_UNIT * 4096.0 / 60.0;
    return std::max(
      1, static_cast<int32_t>(std::floor(pulses_per_second * speed_limit * elapsed_seconds)));
  }

  void reset_motion_clock()
  {
    last_motion_update_time_ = std::chrono::steady_clock::now();
    motion_clock_initialized_ = true;
  }

  double motion_elapsed_seconds()
  {
    const auto now = std::chrono::steady_clock::now();
    if (!motion_clock_initialized_) {
      reset_motion_clock();
      return 1.0 / write_rate_hz_;
    }
    const double elapsed = std::chrono::duration<double>(now - last_motion_update_time_).count();
    last_motion_update_time_ = now;
    return clamp(elapsed, 0.0, 0.25);
  }

  bool thumb_positions_valid() const
  {
    return latest_position_valid_[thumb_flex_index_] &&
           latest_position_valid_[thumb_abduction_index_] &&
           latest_position_valid_[thumb_opposition_index_];
  }

  bool thumb_motion_active() const
  {
    if (!thumb_controller_) {
      return false;
    }
    const auto phase = thumb_controller_->phase();
    return phase != ThumbMotionPhase::IDLE && phase != ThumbMotionPhase::COMPLETE &&
           phase != ThumbMotionPhase::ERROR;
  }

  void start_thumb_motion(std::chrono::steady_clock::time_point now)
  {
    thumb_transition_source_index_ = thumb_current_pose_index_;
    thumb_transition_target_index_ = thumb_requested_pose_index_;
    thumb_controller_->start(
      thumb_poses_[thumb_transition_source_index_].name,
      thumb_poses_[thumb_transition_target_index_].name, latest_thumb_flex_, now);
    thumb_active_target_flex_ = latest_thumb_flex_;
    RCLCPP_INFO(
      get_logger(), "Thumb transition started: source=%s, target=%s, flex=%.3f",
      thumb_poses_[thumb_transition_source_index_].name.c_str(),
      thumb_poses_[thumb_transition_target_index_].name.c_str(), latest_thumb_flex_);
    log_thumb_phase_started();
  }

  void log_thumb_phase_started() const
  {
    if (!thumb_motion_active()) {
      return;
    }
    const auto target = thumb_controller_->phase_target();
    const std::array<std::size_t, 3> indices = {
      thumb_flex_index_, thumb_abduction_index_, thumb_opposition_index_};
    const std::size_t axis_index = indices[static_cast<std::size_t>(target.axis)];
    RCLCPP_INFO(
      get_logger(), "Thumb phase started: phase=%s, axis=%s, target=%d, present=%d",
      ThumbMotionController::phase_name(thumb_controller_->phase()),
      ThumbMotionController::axis_name(target.axis), target.position,
      latest_present_positions_[axis_index]);
  }

  bool update_thumb_motion(
    std::vector<int32_t> & desired, std::chrono::steady_clock::time_point now)
  {
    if (!thumb_control_enabled_ || !thumb_positions_valid()) {
      return true;
    }
    if (!thumb_pose_initialized_) {
      thumb_current_pose_index_ = nearest_thumb_pose_from_raw();
      thumb_requested_pose_index_ = thumb_current_pose_index_;
      thumb_candidate_pose_index_ = thumb_current_pose_index_;
      thumb_pose_initialized_ = true;
      const auto & inferred_pose = thumb_poses_[thumb_current_pose_index_];
      thumb_completed_flex_ = clamp(
        static_cast<double>(
          latest_present_positions_[thumb_flex_index_] - inferred_pose.flex_home) /
          static_cast<double>(inferred_pose.flex_closed - inferred_pose.flex_home),
        0.0, 1.0);
      RCLCPP_INFO(
        get_logger(), "Initial thumb pose inferred: %s",
        thumb_poses_[thumb_current_pose_index_].name.c_str());
    }

    ThumbMotionPhase active_phase = ThumbMotionPhase::IDLE;
    ThumbPhaseTarget active_target;
    if (thumb_motion_active()) {
      const std::array<int32_t, 3> present = {
        latest_present_positions_[thumb_flex_index_],
        latest_present_positions_[thumb_abduction_index_],
        latest_present_positions_[thumb_opposition_index_]};
      active_phase = thumb_controller_->phase();
      active_target = thumb_controller_->phase_target();
      thumb_controller_->update(present, now);
      if (thumb_motion_active() && thumb_controller_->phase() != active_phase) {
        log_thumb_phase_started();
      }
    }

    if (thumb_controller_->phase() == ThumbMotionPhase::ERROR) {
      const std::array<std::size_t, 3> indices = {
        thumb_flex_index_, thumb_abduction_index_, thumb_opposition_index_};
      const std::size_t failed_axis_index = indices[static_cast<std::size_t>(active_target.axis)];
      const auto & failed_axis = axes_[failed_axis_index];
      const int32_t present = latest_present_positions_[failed_axis_index];
      RCLCPP_ERROR(
        get_logger(),
        "Thumb transition failed: reason=%s, source=%s, target_pose=%s, target_flex=%.3f, "
        "phase=%s, phase_elapsed=%.3f s, phase_timeout=%.3f s, axis=%s, target=%d, present=%d, "
        "error=%d, motor_id=%u, commanded=%d, dynamixel_goal=%d, current=%.1f mA, torque=%s, "
        "speed_limit=%.3f; disabling torque",
        thumb_controller_->error_message().c_str(),
        thumb_poses_[thumb_transition_source_index_].name.c_str(),
        thumb_poses_[thumb_transition_target_index_].name.c_str(), thumb_active_target_flex_,
        ThumbMotionController::phase_name(active_phase),
        thumb_controller_->phase_elapsed_seconds(now), thumb_controller_->phase_timeout_seconds(),
        ThumbMotionController::axis_name(active_target.axis), active_target.position, present,
        active_target.position - present, static_cast<unsigned int>(failed_axis.motor_id),
        commanded_positions_[failed_axis_index], latest_goal_positions_[failed_axis_index],
        latest_present_currents_ampere_[failed_axis_index] * 1000.0,
        latest_torque_enabled_[failed_axis_index] ? "enabled" : "disabled", command_speed_limit_);
      thumb_failure_latched_ = true;
      thumb_failed_pose_index_ = thumb_transition_target_index_;
      thumb_controller_->reset();
      thumb_pose_initialized_ = false;
      thumb_candidate_samples_ = 0;
      return false;
    }
    if (thumb_controller_->phase() == ThumbMotionPhase::COMPLETE) {
      thumb_current_pose_index_ = thumb_transition_target_index_;
      thumb_completed_flex_ = thumb_active_target_flex_;
    }

    const bool pose_changed = thumb_requested_pose_index_ != thumb_current_pose_index_;
    const bool flex_changed = std::abs(latest_thumb_flex_ - thumb_completed_flex_) > 0.001;
    if (!thumb_motion_active() && (pose_changed || flex_changed)) {
      start_thumb_motion(now);
    }

    desired[thumb_flex_index_] = commanded_positions_[thumb_flex_index_];
    desired[thumb_abduction_index_] = commanded_positions_[thumb_abduction_index_];
    desired[thumb_opposition_index_] = commanded_positions_[thumb_opposition_index_];
    if (thumb_motion_active()) {
      const auto target = thumb_controller_->phase_target();
      const std::array<std::size_t, 3> indices = {
        thumb_flex_index_, thumb_abduction_index_, thumb_opposition_index_};
      desired[indices[static_cast<std::size_t>(target.axis)]] = target.position;
    }
    return true;
  }

  void run_control_cycle()
  {
    const uint8_t safety_state = safety_state_.load();
    if (!hardware_initialized_positions_) {
      return;
    }
    if (safety_state == thing_interfaces::msg::SafetyState::SAFE) {
      run_safe_cycle();
      return;
    }
    if (safety_state != thing_interfaces::msg::SafetyState::RUN) {
      return;
    }

    std::vector<int32_t> desired;
    double speed_limit = 1.0;
    {
      std::lock_guard<std::mutex> lock(command_mutex_);
      const bool thumb_transition_in_progress = thumb_motion_active();
      if (!command_available_ && !thumb_transition_in_progress) {
        return;
      }
      if (command_available_) {
        const auto age = std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - last_command_time_);
        if (age.count() > hardware_command_watchdog_ms_) {
          command_available_ = false;
          if (!thumb_transition_in_progress) {
            RCLCPP_WARN(get_logger(), "Hardware command watchdog expired; holding last setpoint");
            return;
          }
          RCLCPP_INFO(
            get_logger(),
            "Hardware command watchdog expired; completing the accepted thumb transition");
        }
      }
      if (!command_available_ && !thumb_transition_in_progress) {
        command_available_ = false;
        return;
      }
      desired = desired_positions_;
      speed_limit = command_speed_limit_;
    }

    const auto control_now = std::chrono::steady_clock::now();
    if (!update_thumb_motion(desired, control_now)) {
      {
        std::lock_guard<std::mutex> lock(command_mutex_);
        command_available_ = false;
      }
      disable_torque();
      return;
    }

    enable_torque();
    if (!torque_enabled_) {
      return;
    }

    const double elapsed_seconds = motion_elapsed_seconds();
    for (std::size_t index = 0; index < axes_.size(); ++index) {
      if (!axes_[index].control_enabled) {
        continue;
      }
      const int32_t difference = desired[index] - commanded_positions_[index];
      const int32_t maximum_step = max_step_for_axis(axes_[index], speed_limit, elapsed_seconds);
      commanded_positions_[index] += clamp(difference, -maximum_step, maximum_step);
    }
    write_positions(commanded_positions_);
  }

  void run_safe_cycle()
  {
    if (!safe_action_active_) {
      return;
    }
    const auto now = std::chrono::steady_clock::now();
    if (
      std::chrono::duration<double>(now - safe_action_started_at_).count() >
      safe_motion_timeout_seconds_) {
      if (thumb_control_enabled_ && thumb_motion_active()) {
        const auto target = thumb_controller_->phase_target();
        const std::array<std::size_t, 3> indices = {
          thumb_flex_index_, thumb_abduction_index_, thumb_opposition_index_};
        const std::size_t axis_index = indices[static_cast<std::size_t>(target.axis)];
        RCLCPP_ERROR(
          get_logger(),
          "SAFE position recovery timed out after %.2f seconds: phase=%s, axis=%s, target=%d, "
          "motor_id=%u, commanded=%d, dynamixel_goal=%d, present=%d, current=%.1f mA, torque=%s, "
          "speed_limit=%.3f; disabling torque",
          safe_motion_timeout_seconds_,
          ThumbMotionController::phase_name(thumb_controller_->phase()),
          ThumbMotionController::axis_name(target.axis), target.position,
          static_cast<unsigned int>(axes_[axis_index].motor_id), commanded_positions_[axis_index],
          latest_goal_positions_[axis_index], latest_present_positions_[axis_index],
          latest_present_currents_ampere_[axis_index] * 1000.0,
          latest_torque_enabled_[axis_index] ? "enabled" : "disabled", safe_velocity_limit_);
      } else {
        RCLCPP_ERROR(
          get_logger(), "SAFE position recovery timed out after %.2f seconds; disabling torque",
          safe_motion_timeout_seconds_);
      }
      finish_safe_action(false);
      return;
    }

    if (!torque_enabled_) {
      for (std::size_t index = 0; index < axes_.size(); ++index) {
        if (axes_[index].control_enabled && latest_position_valid_[index]) {
          commanded_positions_[index] = latest_present_positions_[index];
        }
      }
      enable_torque();
      if (!torque_enabled_) {
        RCLCPP_ERROR(get_logger(), "SAFE position recovery could not enable motor torque");
        finish_safe_action(false);
        return;
      }
    }

    std::vector<int32_t> desired(axes_.size());
    for (std::size_t index = 0; index < axes_.size(); ++index) {
      desired[index] = axes_[index].safe_position;
    }
    if (thumb_control_enabled_) {
      thumb_requested_pose_index_ = neutral_thumb_pose_index();
      latest_thumb_flex_ = 0.0;
      if (!update_thumb_motion(desired, now)) {
        finish_safe_action(false);
        return;
      }
    }

    const double elapsed_seconds = motion_elapsed_seconds();
    bool setpoints_reached = true;
    for (std::size_t index = 0; index < axes_.size(); ++index) {
      const auto & axis = axes_[index];
      if (!axis.control_enabled) {
        continue;
      }
      const int32_t difference = desired[index] - commanded_positions_[index];
      const int32_t maximum_step = max_step_for_axis(axis, safe_velocity_limit_, elapsed_seconds);
      commanded_positions_[index] += clamp(difference, -maximum_step, maximum_step);
      if (commanded_positions_[index] != desired[index]) {
        setpoints_reached = false;
      }
    }

    if (!write_positions(commanded_positions_)) {
      return;
    }
    if (!setpoints_reached || !controlled_motors_at_safe_positions()) {
      return;
    }

    RCLCPP_INFO(get_logger(), "SAFE positions reached; disabling torque for all motors");
    finish_safe_action(true);
  }

  void finish_safe_action(bool positions_reached)
  {
    if (thumb_control_enabled_ && thumb_controller_) {
      thumb_controller_->reset();
      thumb_failure_latched_ = false;
      thumb_candidate_samples_ = 0;
      if (positions_reached) {
        const std::size_t neutral = neutral_thumb_pose_index();
        thumb_current_pose_index_ = neutral;
        thumb_requested_pose_index_ = neutral;
        thumb_candidate_pose_index_ = neutral;
        thumb_completed_flex_ = 0.0;
        thumb_active_target_flex_ = 0.0;
        latest_thumb_flex_ = 0.0;
        thumb_pose_initialized_ = true;
      } else {
        thumb_pose_initialized_ = false;
      }
    }
    disable_torque();
    safe_action_active_ = false;
  }

  bool controlled_motors_at_safe_positions() const
  {
    for (std::size_t index = 0; index < axes_.size(); ++index) {
      const auto & axis = axes_[index];
      if (!axis.control_enabled) {
        continue;
      }
      if (
        !latest_position_valid_[index] ||
        std::abs(latest_present_positions_[index] - axis.safe_position) > axis.position_tolerance) {
        return false;
      }
    }
    return true;
  }

  bool write_positions(const std::vector<int32_t> & positions)
  {
    bool all_succeeded = true;
    for (std::size_t bus_index = 0; bus_index < buses_.size(); ++bus_index) {
      std::vector<uint8_t> motor_ids;
      std::vector<uint32_t> goal_positions;
      for (std::size_t axis_index = 0; axis_index < axes_.size(); ++axis_index) {
        if (axes_[axis_index].bus_index == bus_index && axes_[axis_index].control_enabled) {
          motor_ids.push_back(axes_[axis_index].motor_id);
          goal_positions.push_back(static_cast<uint32_t>(positions[axis_index]));
        }
      }
      if (motor_ids.empty()) {
        continue;
      }
      const DriverResult result =
        buses_[bus_index].driver->sync_write_goal_positions(motor_ids, goal_positions);
      if (!result.success) {
        all_succeeded = false;
        buses_[bus_index].last_error = result.error_message;
        RCLCPP_ERROR(
          get_logger(), "Goal position Sync Write failed: bus=%s, error=%s",
          buses_[bus_index].name.c_str(), result.error_message.c_str());
      }
    }
    return all_succeeded;
  }

  thing_interfaces::msg::MotorState make_motor_state(
    const AxisConfig & axis, const MotorStatusReadResult & read_result)
  {
    thing_interfaces::msg::MotorState state;
    state.motor_id = axis.motor_id;
    state.actuator_name = axis.actuator_name;
    const auto & raw = read_result.value;
    const int32_t signed_goal_position = static_cast<int32_t>(raw.goal_position);
    const int32_t signed_present_position = static_cast<int32_t>(raw.present_position);
    const int32_t signed_velocity = static_cast<int32_t>(raw.present_velocity);
    const int16_t signed_current = static_cast<int16_t>(raw.present_current);
    state.goal_position_raw = signed_goal_position;
    state.present_position_raw = signed_present_position;
    state.goal_position_rad = signed_goal_position * POSITION_RADIAN_UNIT;
    state.present_position_rad = signed_present_position * POSITION_RADIAN_UNIT;
    state.velocity_rad_s = signed_velocity * xl330::VELOCITY_RPM_UNIT * RPM_TO_RADIAN_PER_SECOND;
    state.current_ampere = signed_current * xl330::CURRENT_MILLIAMPERE_UNIT / 1000.0;
    state.voltage_volt = raw.present_voltage * xl330::INPUT_VOLTAGE_UNIT;
    state.temperature_celsius = static_cast<float>(raw.present_temperature);
    state.torque_enabled = raw.torque_enabled != 0;
    state.hardware_error = raw.hardware_error;
    state.communication_result = read_result.result.success ? 0 : -1;
    state.communication_ok = read_result.result.success;
    return state;
  }

  void publish_motor_status()
  {
    const auto callback_started = std::chrono::steady_clock::now();
    if (status_callback_started_) {
      const auto scheduling_gap = std::chrono::duration_cast<std::chrono::milliseconds>(
        callback_started - last_status_callback_start_);
      if (scheduling_gap.count() > SLOW_OPERATION_THRESHOLD_MS) {
        RCLCPP_WARN(
          get_logger(), "MotorStatus callback scheduling gap: elapsed=%ld ms",
          static_cast<long>(scheduling_gap.count()));
      }
    }
    last_status_callback_start_ = callback_started;
    status_callback_started_ = true;

    thing_interfaces::msg::MotorStatus message;
    message.header.stamp = now();
    message.motors.reserve(axes_.size());
    bool controlled_torque_state_complete = true;
    bool all_controlled_torque_enabled = true;
    bool all_controlled_torque_disabled = true;
    for (auto & bus : buses_) {
      bus.failed_read_count = 0;
      bus.last_error.clear();
    }

    std::map<uint8_t, MotorStatusReadResult> read_results;
    for (std::size_t bus_index = 0; bus_index < buses_.size(); ++bus_index) {
      std::vector<uint8_t> motor_ids;
      for (const auto & axis : axes_) {
        if (axis.bus_index == bus_index) {
          motor_ids.push_back(axis.motor_id);
        }
      }
      if (motor_ids.empty()) {
        continue;
      }
      const auto bus_read_started = std::chrono::steady_clock::now();
      auto results = buses_[bus_index].driver->sync_read_motor_status(motor_ids);
      const auto bus_read_elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - bus_read_started);
      if (bus_read_elapsed.count() > SLOW_OPERATION_THRESHOLD_MS) {
        RCLCPP_WARN(
          get_logger(), "Slow DYNAMIXEL status read: bus=%s, elapsed=%ld ms, motors=%zu",
          buses_[bus_index].name.c_str(), static_cast<long>(bus_read_elapsed.count()),
          motor_ids.size());
      }
      for (auto & result : results) {
        if (!result.result.success) {
          ++buses_[bus_index].failed_read_count;
          buses_[bus_index].last_error =
            "ID=" + std::to_string(result.motor_id) + ": " + result.result.error_message;
        }
        read_results.emplace(result.motor_id, std::move(result));
      }
    }

    bool initialize_positions = !hardware_initialized_positions_;
    for (std::size_t index = 0; index < axes_.size(); ++index) {
      auto state = make_motor_state(axes_[index], read_results.at(axes_[index].motor_id));
      if (!state.communication_ok) {
        ++message.failed_read_count;
        initialize_positions = false;
        latest_position_valid_[index] = false;
        if (axes_[index].control_enabled) {
          controlled_torque_state_complete = false;
        }
      } else {
        latest_present_positions_[index] = state.present_position_raw;
        latest_goal_positions_[index] = state.goal_position_raw;
        latest_present_currents_ampere_[index] = state.current_ampere;
        latest_torque_enabled_[index] = state.torque_enabled;
        latest_position_valid_[index] = true;
        if (axes_[index].control_enabled) {
          all_controlled_torque_enabled = all_controlled_torque_enabled && state.torque_enabled;
          all_controlled_torque_disabled = all_controlled_torque_disabled && !state.torque_enabled;
        }
        if (!hardware_initialized_positions_) {
          commanded_positions_[index] = state.present_position_raw;
          desired_positions_[index] = state.present_position_raw;
        }
      }
      message.motors.push_back(std::move(state));
    }
    if (controlled_torque_state_complete) {
      torque_enabled_ = all_controlled_torque_enabled;
      torque_off_confirmed_ = all_controlled_torque_disabled;
    }
    if (initialize_positions) {
      hardware_initialized_positions_ = true;
      RCLCPP_INFO(get_logger(), "Initial motor positions captured; control is ready");
    }

    message.bus_communication_ok = message.failed_read_count == 0;
    message.message = message.bus_communication_ok
                        ? "ok"
                        : std::to_string(message.failed_read_count) + " motor read(s) failed";
    const auto publish_started = std::chrono::steady_clock::now();
    status_publisher_->publish(message);
    const auto publish_elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - publish_started);
    if (publish_elapsed.count() > SLOW_OPERATION_THRESHOLD_MS) {
      RCLCPP_WARN(
        get_logger(), "Slow MotorStatus DDS publish: elapsed=%ld ms",
        static_cast<long>(publish_elapsed.count()));
    }
    const auto callback_elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - callback_started);
    if (callback_elapsed.count() > SLOW_OPERATION_THRESHOLD_MS) {
      RCLCPP_WARN(
        get_logger(), "Slow MotorStatus callback: elapsed=%ld ms, failed_reads=%u",
        static_cast<long>(callback_elapsed.count()), message.failed_read_count);
    }
  }

  static diagnostic_msgs::msg::KeyValue key_value(std::string key, std::string value)
  {
    diagnostic_msgs::msg::KeyValue pair;
    pair.key = std::move(key);
    pair.value = std::move(value);
    return pair;
  }

  void publish_diagnostics()
  {
    diagnostic_msgs::msg::DiagnosticArray message;
    message.header.stamp = now();
    for (const auto & bus : buses_) {
      diagnostic_msgs::msg::DiagnosticStatus status;
      status.name = "thing_hardware/" + bus.name;
      status.hardware_id = bus.device;
      const bool ok = bus.initialized && bus.failed_read_count == 0;
      status.level = ok ? diagnostic_msgs::msg::DiagnosticStatus::OK
                        : diagnostic_msgs::msg::DiagnosticStatus::ERROR;
      status.message =
        ok ? "communication ok" : (bus.last_error.empty() ? "motor read failed" : bus.last_error);
      status.values.push_back(key_value("device", bus.device));
      status.values.push_back(
        key_value("failed_read_count", std::to_string(bus.failed_read_count)));
      if (integration_test_mode_) {
        status.level = std::max(
          status.level, static_cast<uint8_t>(diagnostic_msgs::msg::DiagnosticStatus::WARN));
        status.values.push_back(key_value("integration_test_mode", "four_fingers"));
      }
      message.status.push_back(std::move(status));
    }
    const auto publish_started = std::chrono::steady_clock::now();
    diagnostic_publisher_->publish(message);
    const auto publish_elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - publish_started);
    if (publish_elapsed.count() > SLOW_OPERATION_THRESHOLD_MS) {
      RCLCPP_WARN(
        get_logger(), "Slow diagnostics DDS publish: elapsed=%ld ms",
        static_cast<long>(publish_elapsed.count()));
    }
  }

  bool integration_test_mode_{false};
  int operating_mode_{5};
  double read_rate_hz_{20.0};
  double write_rate_hz_{50.0};
  double safe_velocity_limit_{0.5};
  double safe_motion_timeout_seconds_{2.5};
  int hardware_command_watchdog_ms_{500};
  std::chrono::nanoseconds read_period_;
  std::chrono::nanoseconds write_period_;
  std::chrono::nanoseconds diagnostic_period_;
  std::vector<AxisConfig> axes_;
  std::vector<BusRuntime> buses_;

  std::atomic<uint8_t> safety_state_{thing_interfaces::msg::SafetyState::INIT};
  std::mutex command_mutex_;
  std::vector<int32_t> commanded_positions_;
  std::vector<int32_t> desired_positions_;
  std::vector<int32_t> latest_present_positions_;
  std::vector<int32_t> latest_goal_positions_;
  std::vector<double> latest_present_currents_ampere_;
  std::vector<bool> latest_torque_enabled_;
  std::vector<bool> latest_position_valid_;
  std::vector<ThumbPoseConfig> thumb_poses_;
  std::unique_ptr<ThumbMotionController> thumb_controller_;
  std::size_t thumb_flex_index_{0};
  std::size_t thumb_abduction_index_{0};
  std::size_t thumb_opposition_index_{0};
  std::size_t thumb_current_pose_index_{0};
  std::size_t thumb_requested_pose_index_{0};
  std::size_t thumb_candidate_pose_index_{0};
  std::size_t thumb_transition_source_index_{0};
  std::size_t thumb_transition_target_index_{0};
  int thumb_candidate_samples_{0};
  int thumb_pose_stable_samples_{3};
  double thumb_pose_switch_margin_{0.1};
  double latest_thumb_flex_{0.0};
  double thumb_active_target_flex_{0.0};
  double thumb_completed_flex_{0.0};
  bool thumb_control_enabled_{false};
  bool thumb_pose_initialized_{false};
  bool thumb_failure_latched_{false};
  std::size_t thumb_failed_pose_index_{0};
  double command_speed_limit_{1.0};
  std::chrono::steady_clock::time_point last_command_time_;
  std::chrono::steady_clock::time_point last_motion_update_time_;
  std::chrono::steady_clock::time_point last_status_callback_start_;
  std::chrono::steady_clock::time_point safe_action_started_at_;
  bool status_callback_started_{false};
  bool command_available_{false};
  bool hardware_initialized_positions_{false};
  bool torque_enabled_{false};
  bool torque_off_confirmed_{false};
  bool safe_action_active_{false};
  bool motion_clock_initialized_{false};

  rclcpp::Publisher<thing_interfaces::msg::MotorStatus>::SharedPtr status_publisher_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostic_publisher_;
  rclcpp::Subscription<thing_interfaces::msg::HandCommand>::SharedPtr command_subscription_;
  rclcpp::Subscription<thing_interfaces::msg::SafetyState>::SharedPtr safety_subscription_;
  rclcpp::TimerBase::SharedPtr read_timer_;
  rclcpp::TimerBase::SharedPtr write_timer_;
  rclcpp::TimerBase::SharedPtr diagnostic_timer_;
};

}  // namespace thing_hardware

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<thing_hardware::MotorDriverNode>());
  } catch (const std::exception & exception) {
    RCLCPP_FATAL(rclcpp::get_logger("motor_driver_node"), "%s", exception.what());
  }
  rclcpp::shutdown();
  return 0;
}
