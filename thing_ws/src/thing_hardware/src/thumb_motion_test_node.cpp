#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "thing_hardware/dynamixel_bus.hpp"
#include "thing_hardware/thumb_motion_controller.hpp"
#include "thing_hardware/xl330_control_table.hpp"

namespace thing_hardware
{

namespace
{
template <typename ValueT>
std::vector<ValueT> convert_integer_array(const std::vector<int64_t> & values)
{
  std::vector<ValueT> result;
  result.reserve(values.size());
  for (const auto value : values) {
    result.push_back(static_cast<ValueT>(value));
  }
  return result;
}
}  // namespace

class ThumbMotionTestNode : public rclcpp::Node
{
public:
  ThumbMotionTestNode() : Node("thumb_motion_test_node")
  {
    load_parameters();
    build_test_steps();
    initialize_buses();
    verify_motors();
    start_current_step();

    if (!execute_motion_) {
      RCLCPP_WARN(
        get_logger(),
        "Dry run only: validated thumb configuration and motor connections; pass "
        "-p execute_motion:=true to enable torque and move");
      return;
    }

    configure_motors();
    capture_present_positions();
    write_current_positions_before_torque();
    enable_torque();

    command_current_phase();
    control_timer_ = create_wall_timer(control_period_, [this]() { run_control_cycle(); });

    RCLCPP_WARN(
      get_logger(),
      "Thumb motion test started: steps=%zu, source=%s, target=%s, flex=%.3f; do not run "
      "motor_driver_node at the same time; press Ctrl+C to disable torque",
      test_steps_.size(), test_steps_.front().source_pose.c_str(),
      test_steps_.front().target_pose.c_str(), test_steps_.front().target_flex);
  }

  ~ThumbMotionTestNode() override { disable_torque(); }

private:
  struct TestStep
  {
    std::string source_pose;
    std::string target_pose;
    double target_flex{0.0};
    std::string category;
  };

  void load_parameters()
  {
    bus_a_device_ = declare_parameter<std::string>("thumb_bus_a_device", "");
    bus_b_device_ = declare_parameter<std::string>("thumb_bus_b_device", "");
    const auto baud_rate = declare_parameter<int64_t>("thumb_baud_rate", 57600);
    protocol_version_ = declare_parameter<double>("thumb_protocol_version", 2.0);
    const auto flex_motor_id = declare_parameter<int64_t>("thumb_flex_motor_id", 2);
    const auto abduction_motor_id = declare_parameter<int64_t>("thumb_abduction_motor_id", 5);
    const auto opposition_motor_id = declare_parameter<int64_t>("thumb_opposition_motor_id", 6);

    const auto names = declare_parameter<std::vector<std::string>>(
      "thumb_functional_pose_names", std::vector<std::string>{});
    const auto opposition =
      declare_parameter<std::vector<double>>("thumb_functional_opposition", std::vector<double>{});
    const auto abduction =
      declare_parameter<std::vector<double>>("thumb_functional_abduction", std::vector<double>{});
    const auto opposition_positions =
      convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_opposition_positions_raw", std::vector<int64_t>{}));
    const auto opposition_directions =
      convert_integer_array<int8_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_opposition_approach_directions", std::vector<int64_t>{}));
    const auto abduction_positions =
      convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_abduction_positions_raw", std::vector<int64_t>{}));
    const auto abduction_directions =
      convert_integer_array<int8_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_abduction_approach_directions", std::vector<int64_t>{}));
    const auto opposition_starts =
      convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_opposition_approach_start_positions_raw", std::vector<int64_t>{}));
    const auto abduction_starts =
      convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_abduction_approach_start_positions_raw", std::vector<int64_t>{}));
    const auto opposition_reversal_positions =
      convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_opposition_reversal_positions_raw", std::vector<int64_t>{}));
    const auto abduction_reversal_positions =
      convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
        "thumb_abduction_reversal_positions_raw", std::vector<int64_t>{}));
    const auto flex_home = convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
      "thumb_flex_home_positions_raw", std::vector<int64_t>{}));
    const auto flex_closed = convert_integer_array<int32_t>(declare_parameter<std::vector<int64_t>>(
      "thumb_flex_closed_positions_raw", std::vector<int64_t>{}));

    execute_motion_ = declare_parameter<bool>("execute_motion", false);
    start_pose_ = declare_parameter<std::string>("start_pose", "neutral");
    target_pose_ = declare_parameter<std::string>("target_pose", "open");
    target_flex_ = declare_parameter<double>("target_flex", 0.0);
    run_pose_sequence_ = declare_parameter<bool>("run_pose_sequence", false);
    test_pose_sequence_ =
      declare_parameter<std::vector<std::string>>("test_pose_sequence", std::vector<std::string>{});
    run_flex_sequence_ = declare_parameter<bool>("run_flex_sequence", false);
    test_flex_pose_names_ = declare_parameter<std::vector<std::string>>(
      "test_flex_pose_names", std::vector<std::string>{});
    test_flex_values_ =
      declare_parameter<std::vector<double>>("test_flex_values", std::vector<double>{});
    const double step_hold_seconds = declare_parameter<double>("test_step_hold_seconds", 1.0);
    return_to_start_on_complete_ = declare_parameter<bool>("return_to_start_on_complete", true);
    disable_torque_on_complete_ = declare_parameter<bool>("disable_torque_on_complete", true);
    const auto flex_clearance =
      declare_parameter<int64_t>("thumb_flex_clearance_position_raw", 3200);
    position_tolerance_ = declare_parameter<int>("thumb_position_tolerance_raw", 30);
    const auto approach_tolerance = declare_parameter<int>("thumb_approach_tolerance_raw", 75);
    const double timeout = declare_parameter<double>("thumb_motion_timeout_seconds", 6.0);
    const auto period_ms = declare_parameter<int>("thumb_control_period_ms", 20);
    const auto operating_mode = declare_parameter<int>("thumb_operating_mode", 5);
    const auto position_p_gain = declare_parameter<int>("thumb_position_p_gain", 400);
    const auto position_i_gain = declare_parameter<int>("thumb_position_i_gain", 0);
    const auto position_d_gain = declare_parameter<int>("thumb_position_d_gain", 600);
    const auto goal_current = declare_parameter<int>("thumb_goal_current_ma", 200);
    const auto profile_acceleration = declare_parameter<int>("thumb_profile_acceleration_raw", 20);
    const auto profile_velocity = declare_parameter<int>("thumb_profile_velocity_raw", 50);

    const auto valid_id = [](int64_t value) { return value >= 0 && value <= 252; };
    if (bus_a_device_.empty() || bus_b_device_.empty()) {
      throw std::invalid_argument("thumb bus devices must not be empty");
    }
    if (baud_rate <= 0 || baud_rate > std::numeric_limits<int>::max()) {
      throw std::invalid_argument("thumb_baud_rate is invalid");
    }
    if (protocol_version_ != 2.0) {
      throw std::invalid_argument("XL330 requires protocol version 2.0");
    }
    if (
      !valid_id(flex_motor_id) || !valid_id(abduction_motor_id) || !valid_id(opposition_motor_id) ||
      flex_motor_id == abduction_motor_id || flex_motor_id == opposition_motor_id ||
      abduction_motor_id == opposition_motor_id) {
      throw std::invalid_argument("thumb motor IDs must be valid and unique");
    }
    if (
      names.empty() || opposition.size() != names.size() || abduction.size() != names.size() ||
      opposition_positions.size() != names.size() || opposition_directions.size() != names.size() ||
      abduction_positions.size() != names.size() || abduction_directions.size() != names.size() ||
      opposition_starts.size() != names.size() || abduction_starts.size() != names.size() ||
      flex_home.size() != names.size() || flex_closed.size() != names.size() ||
      opposition_reversal_positions.size() != names.size() * names.size() ||
      abduction_reversal_positions.size() != names.size() * names.size()) {
      throw std::invalid_argument("all thumb pose arrays must have the same non-zero size");
    }
    if (
      !std::isfinite(target_flex_) || target_flex_ < 0.0 || target_flex_ > 1.0 ||
      position_tolerance_ < 0 || approach_tolerance < 0 || timeout <= 0.0 || period_ms < 10 ||
      operating_mode != 5 || position_p_gain < 0 || position_p_gain > 16383 ||
      position_i_gain < 0 || position_i_gain > 16383 || position_d_gain < 0 ||
      position_d_gain > 16383 || goal_current <= 0 || goal_current > 32767 ||
      profile_acceleration < 0 || profile_velocity <= 0 ||
      flex_clearance < std::numeric_limits<int32_t>::min() ||
      flex_clearance > std::numeric_limits<int32_t>::max() || !std::isfinite(step_hold_seconds) ||
      step_hold_seconds < 0.0) {
      throw std::invalid_argument("invalid thumb motion or controller parameter");
    }
    if (
      (run_pose_sequence_ && test_pose_sequence_.empty()) ||
      (run_flex_sequence_ && (test_flex_pose_names_.empty() || test_flex_values_.empty()))) {
      throw std::invalid_argument("enabled thumb test sequences must not be empty");
    }
    for (const double value : test_flex_values_) {
      if (!std::isfinite(value) || value < 0.0 || value > 1.0) {
        throw std::invalid_argument("test_flex_values must be finite values in [0.0, 1.0]");
      }
    }

    poses_.reserve(names.size());
    for (std::size_t index = 0; index < names.size(); ++index) {
      if (
        names[index].empty() || opposition[index] < 0.0 || opposition[index] > 1.0 ||
        abduction[index] < 0.0 || abduction[index] > 1.0 ||
        (opposition_directions[index] != -1 && opposition_directions[index] != 1) ||
        (abduction_directions[index] != -1 && abduction_directions[index] != 1) ||
        flex_home[index] == flex_closed[index]) {
        throw std::invalid_argument("invalid thumb pose entry at index " + std::to_string(index));
      }
      const int64_t opposition_delta =
        static_cast<int64_t>(opposition_positions[index]) - opposition_starts[index];
      const int64_t abduction_delta =
        static_cast<int64_t>(abduction_positions[index]) - abduction_starts[index];
      if (
        (opposition_delta != 0 && (opposition_delta > 0) != (opposition_directions[index] > 0)) ||
        (abduction_delta != 0 && (abduction_delta > 0) != (abduction_directions[index] > 0))) {
        throw std::invalid_argument(
          "thumb approach direction does not match start and target at index " +
          std::to_string(index));
      }
      if (flex_clearance > std::min(flex_home[index], flex_closed[index])) {
        throw std::invalid_argument(
          "thumb flex clearance must not tighten flexion at index " + std::to_string(index));
      }
      poses_.push_back(
        {names[index], opposition[index], abduction[index], opposition_positions[index],
         opposition_directions[index], opposition_starts[index], abduction_positions[index],
         abduction_directions[index], abduction_starts[index], flex_home[index],
         flex_closed[index]});
    }

    baud_rate_ = static_cast<int>(baud_rate);
    operating_mode_ = static_cast<uint8_t>(operating_mode);
    position_p_gain_ = static_cast<uint16_t>(position_p_gain);
    position_i_gain_ = static_cast<uint16_t>(position_i_gain);
    position_d_gain_ = static_cast<uint16_t>(position_d_gain);
    goal_current_ = static_cast<uint16_t>(goal_current);
    profile_acceleration_ = static_cast<uint32_t>(profile_acceleration);
    profile_velocity_ = static_cast<uint32_t>(profile_velocity);
    motor_ids_ = {
      static_cast<uint8_t>(flex_motor_id), static_cast<uint8_t>(abduction_motor_id),
      static_cast<uint8_t>(opposition_motor_id)};
    control_period_ = std::chrono::milliseconds(period_ms);
    step_hold_duration_ = std::chrono::duration<double>(step_hold_seconds);
    controller_ = std::make_unique<ThumbMotionController>(
      poses_, position_tolerance_, approach_tolerance, std::chrono::duration<double>(timeout),
      static_cast<int32_t>(flex_clearance), opposition_reversal_positions,
      abduction_reversal_positions);
  }

  bool pose_exists(const std::string & name) const
  {
    return std::any_of(
      poses_.begin(), poses_.end(), [&name](const auto & pose) { return pose.name == name; });
  }

  void append_test_step(
    const std::string & source, const std::string & target, double flex,
    const std::string & category)
  {
    if (!pose_exists(source) || !pose_exists(target)) {
      throw std::invalid_argument(
        "unknown pose in thumb test sequence: " + source + " -> " + target);
    }
    test_steps_.push_back({source, target, flex, category});
  }

  void build_test_steps()
  {
    std::string current_pose = start_pose_;
    if (!pose_exists(current_pose)) {
      throw std::invalid_argument("unknown start_pose: " + current_pose);
    }

    if (!run_pose_sequence_ && !run_flex_sequence_) {
      append_test_step(current_pose, target_pose_, target_flex_, "single");
      return;
    }

    if (run_pose_sequence_) {
      for (const auto & target : test_pose_sequence_) {
        append_test_step(current_pose, target, 0.0, "pose");
        current_pose = target;
      }
    }

    if (run_flex_sequence_) {
      for (const auto & pose : test_flex_pose_names_) {
        if (current_pose != pose) {
          append_test_step(current_pose, pose, 0.0, "flex_pose_transition");
          current_pose = pose;
        }
        for (const double flex : test_flex_values_) {
          append_test_step(current_pose, current_pose, flex, "flex");
        }
      }
    }

    if (return_to_start_on_complete_ && current_pose != start_pose_) {
      append_test_step(current_pose, start_pose_, 0.0, "return");
    }
    if (test_steps_.empty()) {
      throw std::invalid_argument("thumb test sequence produced no steps");
    }
  }

  void start_current_step()
  {
    const auto & step = test_steps_.at(current_step_index_);
    controller_->start(
      step.source_pose, step.target_pose, step.target_flex, std::chrono::steady_clock::now());
    RCLCPP_WARN(
      get_logger(),
      "Thumb test step [%zu/%zu] started: category=%s, source=%s, target=%s, flex=%.3f",
      current_step_index_ + 1, test_steps_.size(), step.category.c_str(), step.source_pose.c_str(),
      step.target_pose.c_str(), step.target_flex);
  }

  void finish_test_sequence()
  {
    RCLCPP_INFO(
      get_logger(), "Thumb test sequence passed: passed=%zu, failed=0", test_steps_.size());
    control_timer_->cancel();
    if (disable_torque_on_complete_) {
      RCLCPP_INFO(get_logger(), "Thumb test sequence complete; disabling torque");
      disable_torque();
    } else {
      RCLCPP_WARN(get_logger(), "Thumb test sequence complete; holding torque until Ctrl+C");
    }
  }

  void initialize_buses()
  {
    bus_a_ = std::make_unique<DynamixelBus>(
      bus_a_device_, baud_rate_, static_cast<float>(protocol_version_));
    bus_b_ = std::make_unique<DynamixelBus>(
      bus_b_device_, baud_rate_, static_cast<float>(protocol_version_));
    require(bus_a_->initialize(), "failed to initialize BUS_A");
    require(bus_b_->initialize(), "failed to initialize BUS_B");
  }

  void verify_motors()
  {
    for (std::size_t index = 0; index < motor_ids_.size(); ++index) {
      uint16_t model = 0;
      require(bus_for_axis(index).ping(motor_ids_[index], model), "failed to ping thumb motor");
      if (model != xl330::EXPECTED_MODEL_NUMBER) {
        throw std::runtime_error("unexpected thumb motor model: " + std::to_string(model));
      }
      RCLCPP_INFO(
        get_logger(), "Thumb motor connected: axis=%s, ID=%u, model=%u",
        ThumbMotionController::axis_name(static_cast<ThumbAxis>(index)),
        static_cast<unsigned int>(motor_ids_[index]), static_cast<unsigned int>(model));
    }
  }

  void configure_motors()
  {
    for (std::size_t index = 0; index < motor_ids_.size(); ++index) {
      auto & bus = bus_for_axis(index);
      const uint8_t id = motor_ids_[index];
      require(bus.write_one_byte(id, xl330::TORQUE_ENABLE_ADDRESS, 0), "disable torque failed");
      require(
        bus.write_one_byte(id, xl330::OPERATING_MODE_ADDRESS, operating_mode_),
        "operating mode write failed");
      require(
        bus.write_two_bytes(id, xl330::POSITION_P_GAIN_ADDRESS, position_p_gain_),
        "P gain write failed");
      require(
        bus.write_two_bytes(id, xl330::POSITION_I_GAIN_ADDRESS, position_i_gain_),
        "I gain write failed");
      require(
        bus.write_two_bytes(id, xl330::POSITION_D_GAIN_ADDRESS, position_d_gain_),
        "D gain write failed");
      require(
        bus.write_two_bytes(id, xl330::GOAL_CURRENT_ADDRESS, goal_current_),
        "goal current write failed");
      require(
        bus.write_four_bytes(id, xl330::PROFILE_ACCELERATION_ADDRESS, profile_acceleration_),
        "profile acceleration write failed");
      require(
        bus.write_four_bytes(id, xl330::PROFILE_VELOCITY_ADDRESS, profile_velocity_),
        "profile velocity write failed");
    }
  }

  void capture_present_positions()
  {
    for (std::size_t index = 0; index < motor_ids_.size(); ++index) {
      uint32_t raw = 0;
      require(
        bus_for_axis(index).read_four_bytes(
          motor_ids_[index], xl330::PRESENT_POSITION_ADDRESS, raw),
        "present position read failed");
      present_positions_[index] = static_cast<int32_t>(raw);
    }
  }

  void write_current_positions_before_torque()
  {
    for (std::size_t index = 0; index < motor_ids_.size(); ++index) {
      require(
        bus_for_axis(index).write_four_bytes(
          motor_ids_[index], xl330::GOAL_POSITION_ADDRESS,
          static_cast<uint32_t>(present_positions_[index])),
        "initial goal position write failed");
    }
  }

  void enable_torque()
  {
    for (std::size_t index = 0; index < motor_ids_.size(); ++index) {
      require(
        bus_for_axis(index).write_one_byte(motor_ids_[index], xl330::TORQUE_ENABLE_ADDRESS, 1),
        "enable torque failed");
    }
    torque_enabled_ = true;
  }

  void disable_torque() noexcept
  {
    if (!torque_enabled_) {
      return;
    }
    for (std::size_t index = 0; index < motor_ids_.size(); ++index) {
      const auto result =
        bus_for_axis(index).write_one_byte(motor_ids_[index], xl330::TORQUE_ENABLE_ADDRESS, 0);
      if (!result.success) {
        RCLCPP_ERROR(
          get_logger(), "Failed to disable thumb torque: ID=%u, error=%s",
          static_cast<unsigned int>(motor_ids_[index]), result.error_message.c_str());
      }
    }
    torque_enabled_ = false;
  }

  void run_control_cycle()
  {
    try {
      capture_present_positions();
      const auto now = std::chrono::steady_clock::now();
      if (step_holding_) {
        if (now - step_hold_started_at_ < step_hold_duration_) {
          return;
        }
        step_holding_ = false;
        ++current_step_index_;
        if (current_step_index_ >= test_steps_.size()) {
          finish_test_sequence();
          return;
        }
        start_current_step();
        command_current_phase();
        return;
      }

      const auto active_phase = controller_->phase();
      const auto active_target = controller_->phase_target();
      if (!controller_->update(present_positions_, now)) {
        return;
      }
      if (controller_->phase() == ThumbMotionPhase::ERROR) {
        fail_motion(active_phase, active_target, controller_->error_message());
        return;
      }
      if (controller_->phase() == ThumbMotionPhase::COMPLETE) {
        const auto & step = test_steps_.at(current_step_index_);
        RCLCPP_INFO(
          get_logger(),
          "Thumb test step [%zu/%zu] passed: category=%s, source=%s, target=%s, flex=%.3f",
          current_step_index_ + 1, test_steps_.size(), step.category.c_str(),
          controller_->source_pose_name().c_str(), controller_->target_pose_name().c_str(),
          step.target_flex);
        step_holding_ = true;
        step_hold_started_at_ = now;
        return;
      }
      command_current_phase();
    } catch (const std::exception & exception) {
      fail(exception.what());
    }
  }

  void command_current_phase()
  {
    const auto target = controller_->phase_target();
    const auto index = static_cast<std::size_t>(target.axis);
    require(
      bus_for_axis(index).write_four_bytes(
        motor_ids_[index], xl330::GOAL_POSITION_ADDRESS, static_cast<uint32_t>(target.position)),
      "goal position write failed");
    RCLCPP_INFO(
      get_logger(), "Thumb phase started: phase=%s, axis=%s, target=%d, present=%d",
      ThumbMotionController::phase_name(controller_->phase()),
      ThumbMotionController::axis_name(target.axis), target.position, present_positions_[index]);
  }

  void fail(const std::string & message)
  {
    RCLCPP_ERROR(
      get_logger(), "Thumb test sequence stopped at step [%zu/%zu]: %s", current_step_index_ + 1,
      test_steps_.size(), message.c_str());
    if (control_timer_) {
      control_timer_->cancel();
    }
    disable_torque();
  }

  void fail_motion(
    ThumbMotionPhase phase, const ThumbPhaseTarget & target, const std::string & message)
  {
    const auto & step = test_steps_.at(current_step_index_);
    const auto index = static_cast<std::size_t>(target.axis);
    uint16_t raw_current = 0;
    const auto current_result = bus_for_axis(index).read_two_bytes(
      motor_ids_[index], xl330::PRESENT_CURRENT_ADDRESS, raw_current);
    const int32_t present = present_positions_[index];
    const int32_t position_error = target.position - present;

    if (current_result.success) {
      RCLCPP_ERROR(
        get_logger(),
        "Thumb test step [%zu/%zu] failed: category=%s, source=%s, target_pose=%s, flex=%.3f, "
        "reason=%s, phase=%s, axis=%s, target=%d, present=%d, error=%d, current=%d mA; "
        "disabling torque",
        current_step_index_ + 1, test_steps_.size(), step.category.c_str(),
        step.source_pose.c_str(), step.target_pose.c_str(), step.target_flex, message.c_str(),
        ThumbMotionController::phase_name(phase), ThumbMotionController::axis_name(target.axis),
        target.position, present, position_error,
        static_cast<int>(static_cast<int16_t>(raw_current)));
    } else {
      RCLCPP_ERROR(
        get_logger(),
        "Thumb test step [%zu/%zu] failed: category=%s, source=%s, target_pose=%s, flex=%.3f, "
        "reason=%s, phase=%s, axis=%s, target=%d, present=%d, error=%d, "
        "current=unavailable (%s); disabling torque",
        current_step_index_ + 1, test_steps_.size(), step.category.c_str(),
        step.source_pose.c_str(), step.target_pose.c_str(), step.target_flex, message.c_str(),
        ThumbMotionController::phase_name(phase), ThumbMotionController::axis_name(target.axis),
        target.position, present, position_error, current_result.error_message.c_str());
    }

    if (control_timer_) {
      control_timer_->cancel();
    }
    disable_torque();
  }

  void require(const DriverResult & result, const std::string & operation) const
  {
    if (!result.success) {
      throw std::runtime_error(operation + ": " + result.error_message);
    }
  }

  DynamixelBus & bus_for_axis(std::size_t index)
  {
    return index == static_cast<std::size_t>(ThumbAxis::FLEX) ? *bus_a_ : *bus_b_;
  }

  std::string bus_a_device_;
  std::string bus_b_device_;
  int baud_rate_{57600};
  double protocol_version_{2.0};
  std::array<uint8_t, 3> motor_ids_{};
  std::vector<ThumbPoseConfig> poses_;
  bool execute_motion_{false};
  std::string start_pose_;
  std::string target_pose_;
  double target_flex_{0.0};
  bool run_pose_sequence_{false};
  std::vector<std::string> test_pose_sequence_;
  bool run_flex_sequence_{false};
  std::vector<std::string> test_flex_pose_names_;
  std::vector<double> test_flex_values_;
  bool return_to_start_on_complete_{true};
  bool disable_torque_on_complete_{true};
  int position_tolerance_{30};
  int operating_mode_{5};
  uint16_t position_p_gain_{400};
  uint16_t position_i_gain_{0};
  uint16_t position_d_gain_{600};
  uint16_t goal_current_{200};
  uint32_t profile_acceleration_{20};
  uint32_t profile_velocity_{50};
  std::chrono::milliseconds control_period_{20};
  std::chrono::duration<double> step_hold_duration_{1.0};
  std::array<int32_t, 3> present_positions_{};
  bool torque_enabled_{false};
  std::vector<TestStep> test_steps_;
  std::size_t current_step_index_{0};
  bool step_holding_{false};
  std::chrono::steady_clock::time_point step_hold_started_at_;

  std::unique_ptr<DynamixelBus> bus_a_;
  std::unique_ptr<DynamixelBus> bus_b_;
  std::unique_ptr<ThumbMotionController> controller_;
  rclcpp::TimerBase::SharedPtr control_timer_;
};

}  // namespace thing_hardware

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<thing_hardware::ThumbMotionTestNode>());
  } catch (const std::exception & exception) {
    RCLCPP_FATAL(rclcpp::get_logger("thumb_motion_test_node"), "%s", exception.what());
  }
  rclcpp::shutdown();
  return 0;
}
