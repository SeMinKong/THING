#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <map>
#include <memory>
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
#include "thing_hardware/xl330_control_table.hpp"
#include "thing_interfaces/msg/motor_state.hpp"
#include "thing_interfaces/msg/motor_status.hpp"

namespace thing_hardware
{

namespace
{
constexpr double TWO_PI = 6.28318530717958647692;
constexpr double POSITION_RADIAN_UNIT = TWO_PI / 4096.0;
constexpr double RPM_TO_RADIAN_PER_SECOND = TWO_PI / 60.0;
}  // namespace

struct AxisConfig
{
  uint8_t motor_id;
  std::string actuator_name;
  std::size_t bus_index;
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

class MotorStatusNode : public rclcpp::Node
{
public:
  MotorStatusNode() : Node("motor_status_node")
  {
    load_parameters();
    initialize_buses();

    status_publisher_ = create_publisher<thing_interfaces::msg::MotorStatus>(
      "/thing/motor_status", rclcpp::QoS(rclcpp::KeepLast(5)).reliable());
    diagnostic_publisher_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
      "/thing/diagnostics", rclcpp::QoS(rclcpp::KeepLast(10)).reliable());

    status_timer_ = create_wall_timer(
      std::chrono::milliseconds(publish_period_ms_), [this]() { publish_motor_status(); });
    diagnostic_timer_ = create_wall_timer(
      std::chrono::milliseconds(diagnostic_period_ms_), [this]() { publish_diagnostics(); });

    RCLCPP_INFO(
      get_logger(), "Motor status collection started: motors=%zu, buses=%zu, period=%d ms",
      axes_.size(), buses_.size(), publish_period_ms_);
  }

private:
  void load_parameters()
  {
    const int64_t baud_rate = declare_parameter<int64_t>("baud_rate", 57600);
    const double protocol_version = declare_parameter<double>("protocol_version", 2.0);
    publish_period_ms_ = declare_parameter<int>("publish_period_ms", 500);
    diagnostic_period_ms_ = declare_parameter<int>("diagnostic_period_ms", 1000);
    const auto bus_names =
      declare_parameter<std::vector<std::string>>("bus_names", std::vector<std::string>{});
    const auto bus_devices =
      declare_parameter<std::vector<std::string>>("bus_devices", std::vector<std::string>{});
    const auto motor_ids =
      declare_parameter<std::vector<int64_t>>("motor_ids", std::vector<int64_t>{});
    const auto actuator_names =
      declare_parameter<std::vector<std::string>>("actuator_names", std::vector<std::string>{});
    const auto bus_indices =
      declare_parameter<std::vector<int64_t>>("bus_indices", std::vector<int64_t>{});

    if (baud_rate <= 0 || protocol_version <= 0.0) {
      throw std::invalid_argument("baud_rate and protocol_version must be positive");
    }
    if (publish_period_ms_ <= 0 || diagnostic_period_ms_ <= 0) {
      throw std::invalid_argument("timer periods must be positive");
    }
    if (bus_names.empty() || bus_names.size() != bus_devices.size()) {
      throw std::invalid_argument("bus_names and bus_devices must have the same non-zero size");
    }
    if (
      motor_ids.empty() || motor_ids.size() != actuator_names.size() ||
      motor_ids.size() != bus_indices.size()) {
      throw std::invalid_argument(
        "motor_ids, actuator_names, and bus_indices must have the same non-zero size");
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
    axes_.reserve(motor_ids.size());
    for (std::size_t index = 0; index < motor_ids.size(); ++index) {
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
      axes_.push_back(
        {static_cast<uint8_t>(motor_ids[index]), actuator_names[index],
         static_cast<std::size_t>(bus_indices[index])});
    }
    std::sort(axes_.begin(), axes_.end(), [](const AxisConfig & left, const AxisConfig & right) {
      return left.motor_id < right.motor_id;
    });
  }

  void initialize_buses()
  {
    for (auto & bus : buses_) {
      const DriverResult result = bus.driver->initialize();
      bus.initialized = result.success;
      bus.last_error = result.error_message;
      if (result.success) {
        RCLCPP_INFO(
          get_logger(), "DYNAMIXEL bus initialized: %s (%s)", bus.name.c_str(), bus.device.c_str());
      } else {
        RCLCPP_ERROR(
          get_logger(), "Failed to initialize %s: %s", bus.name.c_str(),
          result.error_message.c_str());
      }
    }
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
    thing_interfaces::msg::MotorStatus message;
    message.header.stamp = now();
    message.motors.reserve(axes_.size());
    for (auto & bus : buses_) {
      bus.failed_read_count = 0;
      if (bus.initialized) {
        bus.last_error.clear();
      }
    }

    std::map<uint8_t, MotorStatusReadResult> read_results;
    for (std::size_t bus_index = 0; bus_index < buses_.size(); ++bus_index) {
      auto & bus = buses_[bus_index];
      std::vector<uint8_t> motor_ids;
      for (const auto & axis : axes_) {
        if (axis.bus_index == bus_index) {
          motor_ids.push_back(axis.motor_id);
        }
      }

      std::vector<MotorStatusReadResult> bus_results;
      if (bus.initialized) {
        bus_results = bus.driver->sync_read_motor_status(motor_ids);
      } else {
        for (const uint8_t motor_id : motor_ids) {
          bus_results.push_back({motor_id, {}, {false, bus.last_error}});
        }
      }

      for (auto & result : bus_results) {
        if (!result.result.success) {
          ++bus.failed_read_count;
          bus.last_error =
            "ID=" + std::to_string(result.motor_id) + ": " + result.result.error_message;
        }
        read_results.emplace(result.motor_id, std::move(result));
      }
    }

    for (const auto & axis : axes_) {
      auto state = make_motor_state(axis, read_results.at(axis.motor_id));
      if (!state.communication_ok) {
        ++message.failed_read_count;
      }
      message.motors.push_back(std::move(state));
    }
    message.bus_communication_ok = message.failed_read_count == 0;
    message.message = message.bus_communication_ok
                        ? "ok"
                        : std::to_string(message.failed_read_count) + " motor read(s) failed";
    status_publisher_->publish(message);
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
      message.status.push_back(std::move(status));
    }
    diagnostic_publisher_->publish(message);
  }

  int publish_period_ms_{500};
  int diagnostic_period_ms_{1000};
  std::vector<AxisConfig> axes_;
  std::vector<BusRuntime> buses_;
  rclcpp::Publisher<thing_interfaces::msg::MotorStatus>::SharedPtr status_publisher_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostic_publisher_;
  rclcpp::TimerBase::SharedPtr status_timer_;
  rclcpp::TimerBase::SharedPtr diagnostic_timer_;
};

}  // namespace thing_hardware

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<thing_hardware::MotorStatusNode>());
  } catch (const std::exception & exception) {
    RCLCPP_FATAL(rclcpp::get_logger("motor_status_node"), "%s", exception.what());
  }
  rclcpp::shutdown();
  return 0;
}
