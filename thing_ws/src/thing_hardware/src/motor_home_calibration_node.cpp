#include <chrono>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "thing_hardware/dynamixel_bus.hpp"
#include "thing_hardware/xl330_control_table.hpp"

class MotorHomeCalibrationNode : public rclcpp::Node
{
public:
  MotorHomeCalibrationNode() : Node("motor_home_calibrator")
  {
    declare_parameters();
    load_and_validate_parameters();

    RCLCPP_INFO(
      this->get_logger(), "Device: %s, baud rate: %d, protocol: %.1f, motor ID: %u",
      device_name_.c_str(), baud_rate_, protocol_version_, static_cast<unsigned int>(motor_id_));

    bus_ =
      std::make_unique<thing_hardware::DynamixelBus>(device_name_, baud_rate_, protocol_version_);

    const auto initialize_result = bus_->initialize();
    if (!initialize_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to initialize DYNAMIXEL bus: %s",
        initialize_result.error_message.c_str());
      finish_sampling();
      return;
    }

    uint16_t model_number = 0;
    const auto ping_result = bus_->ping(motor_id_, model_number);
    if (!ping_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Ping failed for ID %u: %s", static_cast<unsigned int>(motor_id_),
        ping_result.error_message.c_str());
      finish_sampling();
      return;
    }

    RCLCPP_INFO(
      this->get_logger(), "Ping succeeded: ID=%u, model number=%u",
      static_cast<unsigned int>(motor_id_), static_cast<unsigned int>(model_number));

    uint8_t torque_enable = 0;
    const auto torque_result =
      bus_->read_one_byte(motor_id_, thing_hardware::xl330::TORQUE_ENABLE_ADDRESS, torque_enable);
    if (!torque_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Torque Enable: %s",
        torque_result.error_message.c_str());
      finish_sampling();
      return;
    }

    if (torque_enable != 0U) {
      RCLCPP_ERROR(
        this->get_logger(), "Home calibration requires Torque Enable=0, received=%u",
        static_cast<unsigned int>(torque_enable));
      finish_sampling();
      return;
    }

    RCLCPP_INFO(
      this->get_logger(),
      "Torque is disabled. Keep the finger fully extended during %zu position samples",
      SAMPLE_COUNT);

    sample_timer_ =
      this->create_wall_timer(std::chrono::milliseconds(100), [this]() { sample_home_position(); });
  }

private:
  static constexpr std::size_t SAMPLE_COUNT = 50;

  void declare_parameters()
  {
    this->declare_parameter<std::string>("device_name", "");
    this->declare_parameter<int64_t>("baud_rate", 57600);
    this->declare_parameter<double>("protocol_version", 2.0);
    this->declare_parameter<int64_t>("motor_id", 3);
  }

  void load_and_validate_parameters()
  {
    device_name_ = this->get_parameter("device_name").as_string();
    const int64_t baud_rate = this->get_parameter("baud_rate").as_int();
    const double protocol_version = this->get_parameter("protocol_version").as_double();
    const int64_t motor_id = this->get_parameter("motor_id").as_int();

    if (device_name_.empty()) {
      throw std::runtime_error("device_name must not be empty");
    }

    if (baud_rate <= 0 || baud_rate > std::numeric_limits<int>::max()) {
      throw std::runtime_error("baud_rate must be between 1 and INT_MAX");
    }

    if (protocol_version != 2.0) {
      throw std::runtime_error("protocol_version must be 2.0 for XL330");
    }

    if (motor_id < 0 || motor_id > 252) {
      throw std::runtime_error("motor_id must be between 0 and 252");
    }

    baud_rate_ = static_cast<int>(baud_rate);
    protocol_version_ = static_cast<float>(protocol_version);
    motor_id_ = static_cast<uint8_t>(motor_id);
  }

  void sample_home_position()
  {
    uint32_t raw_position = 0;
    const auto result = bus_->read_four_bytes(
      motor_id_, thing_hardware::xl330::PRESENT_POSITION_ADDRESS, raw_position);

    if (!result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to read Present Position: %s", result.error_message.c_str());
      finish_sampling();
      return;
    }

    const int32_t position = static_cast<int32_t>(raw_position);
    position_sum_ += static_cast<int64_t>(position);

    if (position < min_position_) {
      min_position_ = position;
    }

    if (position > max_position_) {
      max_position_ = position;
    }

    ++sample_count_;

    RCLCPP_INFO(
      this->get_logger(), "Home sample: %zu/%zu, position=%d pulse", sample_count_, SAMPLE_COUNT,
      position);

    if (sample_count_ < SAMPLE_COUNT) {
      return;
    }

    const double average_position =
      static_cast<double>(position_sum_) / static_cast<double>(sample_count_);

    RCLCPP_INFO(
      this->get_logger(),
      "Home calibration result: samples=%zu, average=%.1f, min=%d, max=%d, range=%d pulse",
      sample_count_, average_position, min_position_, max_position_, max_position_ - min_position_);

    finish_sampling();
  }

  void finish_sampling()
  {
    if (sample_timer_) {
      sample_timer_->cancel();
    }

    rclcpp::shutdown();
  }

  std::string device_name_{
    "/dev/serial/by-id/"
    "usb-FTDI_USB__-__Serial_Converter_FTBIN51S-if00-port0"};
  int baud_rate_{57600};
  float protocol_version_{2.0F};
  uint8_t motor_id_{3};

  std::unique_ptr<thing_hardware::DynamixelBus> bus_;
  rclcpp::TimerBase::SharedPtr sample_timer_;
  std::size_t sample_count_{0};
  int64_t position_sum_{0};
  int32_t min_position_{std::numeric_limits<int32_t>::max()};
  int32_t max_position_{std::numeric_limits<int32_t>::min()};
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);

  auto node = std::make_shared<MotorHomeCalibrationNode>();
  rclcpp::spin(node);

  if (rclcpp::ok()) {
    rclcpp::shutdown();
  }
  return 0;
}
