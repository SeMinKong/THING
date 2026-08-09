#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iterator>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "thing_hardware/dynamixel_bus.hpp"
#include "thing_hardware/xl330_control_table.hpp"

namespace thing_hardware
{

namespace
{

struct BaudSetting
{
  int baud_rate;
  uint8_t register_value;
};

constexpr BaudSetting SUPPORTED_BAUD_RATES[] = {
  {9600, 0}, {57600, 1}, {115200, 2}, {1000000, 3}, {2000000, 4}, {3000000, 5}, {4000000, 6}};

const BaudSetting & baud_setting(int baud_rate)
{
  const auto iterator = std::find_if(
    std::begin(SUPPORTED_BAUD_RATES), std::end(SUPPORTED_BAUD_RATES),
    [baud_rate](const BaudSetting & setting) { return setting.baud_rate == baud_rate; });
  if (iterator == std::end(SUPPORTED_BAUD_RATES)) {
    throw std::invalid_argument("unsupported XL330 baud rate: " + std::to_string(baud_rate));
  }
  return *iterator;
}

}  // namespace

class DynamixelBaudConfigNode : public rclcpp::Node
{
public:
  DynamixelBaudConfigNode() : Node("dynamixel_baud_config_node")
  {
    load_parameters();
    run();
  }

private:
  void load_parameters()
  {
    device_name_ = declare_parameter<std::string>("device_name", "");
    const int64_t motor_id = declare_parameter<int64_t>("motor_id", 3);
    protocol_version_ = declare_parameter<double>("protocol_version", 2.0);
    current_baud_rate_ = declare_parameter<int>("current_baud_rate", 57600);
    target_baud_rate_ = declare_parameter<int>("target_baud_rate", 1000000);
    execute_change_ = declare_parameter<bool>("execute_change", false);
    const auto scan_baud_rates = declare_parameter<std::vector<int64_t>>(
      "scan_baud_rates", std::vector<int64_t>{57600, 1000000});

    if (device_name_.empty()) {
      throw std::invalid_argument("device_name must not be empty");
    }
    if (motor_id < 0 || motor_id > 252) {
      throw std::invalid_argument("motor_id must be between 0 and 252");
    }
    if (protocol_version_ != 2.0) {
      throw std::invalid_argument("XL330 requires protocol_version 2.0");
    }
    static_cast<void>(baud_setting(current_baud_rate_));
    static_cast<void>(baud_setting(target_baud_rate_));
    if (scan_baud_rates.empty()) {
      throw std::invalid_argument("scan_baud_rates must not be empty");
    }

    motor_id_ = static_cast<uint8_t>(motor_id);
    scan_baud_rates_.reserve(scan_baud_rates.size());
    for (const int64_t baud_rate : scan_baud_rates) {
      if (baud_rate <= 0 || baud_rate > std::numeric_limits<int>::max()) {
        throw std::invalid_argument("scan_baud_rates contains an invalid value");
      }
      static_cast<void>(baud_setting(static_cast<int>(baud_rate)));
      scan_baud_rates_.push_back(static_cast<int>(baud_rate));
    }
  }

  std::unique_ptr<DynamixelBus> connect(int baud_rate) const
  {
    auto bus = std::make_unique<DynamixelBus>(
      device_name_, baud_rate, static_cast<float>(protocol_version_));
    const DriverResult result = bus->initialize();
    if (!result.success) {
      throw std::runtime_error(
        "failed to open " + device_name_ + " at " + std::to_string(baud_rate) +
        " bps: " + result.error_message);
    }
    return bus;
  }

  uint16_t require_ping(DynamixelBus & bus, int baud_rate) const
  {
    uint16_t model_number = 0;
    const DriverResult result = bus.ping(motor_id_, model_number);
    if (!result.success) {
      throw std::runtime_error(
        "ping failed at " + std::to_string(baud_rate) + " bps: " + result.error_message);
    }
    if (model_number != xl330::EXPECTED_MODEL_NUMBER) {
      throw std::runtime_error(
        "unexpected model number: expected=" + std::to_string(xl330::EXPECTED_MODEL_NUMBER) +
        ", actual=" + std::to_string(model_number));
    }
    return model_number;
  }

  void scan_for_motor() const
  {
    RCLCPP_WARN(
      get_logger(), "Scanning configured baud rates for motor ID=%u",
      static_cast<unsigned int>(motor_id_));
    bool found = false;
    for (const int baud_rate : scan_baud_rates_) {
      try {
        auto bus = connect(baud_rate);
        uint16_t model_number = 0;
        const DriverResult result = bus->ping(motor_id_, model_number);
        if (result.success && model_number == xl330::EXPECTED_MODEL_NUMBER) {
          found = true;
          RCLCPP_INFO(
            get_logger(), "Motor found: ID=%u, baud_rate=%d, model=%u",
            static_cast<unsigned int>(motor_id_), baud_rate,
            static_cast<unsigned int>(model_number));
        } else {
          RCLCPP_WARN(
            get_logger(), "Motor not found: ID=%u, baud_rate=%d, error=%s",
            static_cast<unsigned int>(motor_id_), baud_rate,
            result.success ? "unexpected model" : result.error_message.c_str());
        }
      } catch (const std::exception & exception) {
        RCLCPP_WARN(
          get_logger(), "Baud scan failed: baud_rate=%d, error=%s", baud_rate, exception.what());
      }
    }
    if (!found) {
      RCLCPP_ERROR(
        get_logger(), "Motor ID=%u was not found at any configured baud rate",
        static_cast<unsigned int>(motor_id_));
    }
  }

  void run()
  {
    RCLCPP_INFO(
      get_logger(), "Baud configuration target: device=%s, ID=%u, current=%d, target=%d",
      device_name_.c_str(), static_cast<unsigned int>(motor_id_), current_baud_rate_,
      target_baud_rate_);
    RCLCPP_WARN(
      get_logger(), "Do not run motor_driver_node or another serial client on this device");

    std::unique_ptr<DynamixelBus> current_bus;
    uint16_t model_number = 0;
    try {
      current_bus = connect(current_baud_rate_);
      model_number = require_ping(*current_bus, current_baud_rate_);
    } catch (const std::exception & exception) {
      RCLCPP_ERROR(get_logger(), "Current baud verification failed: %s", exception.what());
      current_bus.reset();
      scan_for_motor();
      throw;
    }
    uint8_t current_baud_value = 0;
    DriverResult result =
      current_bus->read_one_byte(motor_id_, xl330::BAUD_RATE_ADDRESS, current_baud_value);
    if (!result.success) {
      throw std::runtime_error("failed to read Baud Rate(8): " + result.error_message);
    }
    const auto & current_setting = baud_setting(current_baud_rate_);
    if (current_baud_value != current_setting.register_value) {
      throw std::runtime_error(
        "Baud Rate(8) mismatch: connected=" + std::to_string(current_baud_rate_) +
        ", register_value=" + std::to_string(current_baud_value));
    }
    RCLCPP_INFO(
      get_logger(), "Current baud verified: ID=%u, model=%u, baud_rate=%d, register_value=%u",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(model_number),
      current_baud_rate_, static_cast<unsigned int>(current_baud_value));

    if (!execute_change_) {
      RCLCPP_WARN(
        get_logger(), "Inspection only; rerun with -p execute_change:=true to change EEPROM");
      current_bus.reset();
      scan_for_motor();
      return;
    }
    if (current_baud_rate_ == target_baud_rate_) {
      RCLCPP_INFO(get_logger(), "Current and target baud rates already match; no change required");
      return;
    }

    uint8_t torque_enabled = 0;
    result = current_bus->read_one_byte(motor_id_, xl330::TORQUE_ENABLE_ADDRESS, torque_enabled);
    if (!result.success) {
      throw std::runtime_error("failed to read Torque Enable(64): " + result.error_message);
    }
    if (torque_enabled != 0) {
      RCLCPP_WARN(get_logger(), "Torque is enabled; disabling torque before EEPROM write");
      result = current_bus->write_one_byte(motor_id_, xl330::TORQUE_ENABLE_ADDRESS, 0);
      if (!result.success) {
        throw std::runtime_error("failed to disable torque: " + result.error_message);
      }
      result = current_bus->read_one_byte(motor_id_, xl330::TORQUE_ENABLE_ADDRESS, torque_enabled);
      if (!result.success || torque_enabled != 0) {
        throw std::runtime_error("torque disable verification failed; EEPROM was not changed");
      }
    }

    const auto & target_setting = baud_setting(target_baud_rate_);
    RCLCPP_WARN(
      get_logger(), "Changing Baud Rate(8): ID=%u, %d -> %d bps, register_value=%u", motor_id_,
      current_baud_rate_, target_baud_rate_,
      static_cast<unsigned int>(target_setting.register_value));
    result = current_bus->write_one_byte_tx_only(
      motor_id_, xl330::BAUD_RATE_ADDRESS, target_setting.register_value);
    if (!result.success) {
      throw std::runtime_error("failed to transmit Baud Rate(8) write: " + result.error_message);
    }

    current_bus.reset();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    try {
      auto target_bus = connect(target_baud_rate_);
      const uint16_t verified_model = require_ping(*target_bus, target_baud_rate_);
      uint8_t verified_baud_value = 0;
      result = target_bus->read_one_byte(motor_id_, xl330::BAUD_RATE_ADDRESS, verified_baud_value);
      if (!result.success || verified_baud_value != target_setting.register_value) {
        throw std::runtime_error("target Baud Rate(8) read-back verification failed");
      }
      RCLCPP_INFO(
        get_logger(), "Baud change succeeded: ID=%u, model=%u, baud_rate=%d, register_value=%u",
        motor_id_, static_cast<unsigned int>(verified_model), target_baud_rate_,
        static_cast<unsigned int>(verified_baud_value));
    } catch (const std::exception & exception) {
      RCLCPP_ERROR(get_logger(), "Target baud verification failed: %s", exception.what());
      scan_for_motor();
      throw;
    }
  }

  std::string device_name_;
  uint8_t motor_id_{3};
  double protocol_version_{2.0};
  int current_baud_rate_{57600};
  int target_baud_rate_{1000000};
  std::vector<int> scan_baud_rates_;
  bool execute_change_{false};
};

}  // namespace thing_hardware

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  try {
    const auto node = std::make_shared<thing_hardware::DynamixelBaudConfigNode>();
    rclcpp::shutdown();
    return 0;
  } catch (const std::exception & exception) {
    RCLCPP_FATAL(rclcpp::get_logger("dynamixel_baud_config_node"), "%s", exception.what());
    rclcpp::shutdown();
    return 1;
  }
}
