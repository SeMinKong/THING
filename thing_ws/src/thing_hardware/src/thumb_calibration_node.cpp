#include <array>
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

class ThumbCalibrationNode : public rclcpp::Node
{
public:
  ThumbCalibrationNode() : Node("thumb_calibrator")
  {
    declare_parameters();
    load_and_validate_parameters();

    bus_a_ =
      std::make_unique<thing_hardware::DynamixelBus>(bus_a_device_, baud_rate_, protocol_version_);
    bus_b_ =
      std::make_unique<thing_hardware::DynamixelBus>(bus_b_device_, baud_rate_, protocol_version_);

    const auto bus_a_result = bus_a_->initialize();
    const auto bus_b_result = bus_b_->initialize();
    if (!bus_a_result.success || !bus_b_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to initialize thumb buses: BUS_A=%s, BUS_B=%s",
        bus_a_result.success ? "ok" : bus_a_result.error_message.c_str(),
        bus_b_result.success ? "ok" : bus_b_result.error_message.c_str());
      rclcpp::shutdown();
      return;
    }

    axes_ = {
      {{"flex", flex_motor_id_, bus_a_.get()},
       {"abduction", abduction_motor_id_, bus_b_.get()},
       {"opposition", opposition_motor_id_, bus_b_.get()}}};

    for (const auto & axis : axes_) {
      uint16_t model_number = 0;
      const auto result = axis.bus->ping(axis.motor_id, model_number);
      if (!result.success) {
        RCLCPP_ERROR(
          this->get_logger(), "Ping failed: axis=%s, ID=%u, error=%s", axis.name,
          static_cast<unsigned int>(axis.motor_id), result.error_message.c_str());
        rclcpp::shutdown();
        return;
      }

      RCLCPP_INFO(
        this->get_logger(), "Thumb axis connected: axis=%s, ID=%u, model=%u", axis.name,
        static_cast<unsigned int>(axis.motor_id), static_cast<unsigned int>(model_number));
    }

    RCLCPP_INFO(
      this->get_logger(),
      "Read-only thumb calibration started: samples=%s, period=%ld ms; no control values "
      "will be written; press Ctrl+C to stop",
      sample_limit_ == 0U ? "unlimited" : std::to_string(sample_limit_).c_str(),
      static_cast<long>(sample_period_.count()));

    sample_timer_ = this->create_wall_timer(sample_period_, [this]() { sample_thumb_axes(); });
  }

private:
  struct AxisSample
  {
    const char * name{""};
    uint8_t motor_id{0};
    thing_hardware::DynamixelBus * bus{nullptr};
    int64_t position_sum{0};
    int32_t min_position{std::numeric_limits<int32_t>::max()};
    int32_t max_position{std::numeric_limits<int32_t>::min()};
  };

  void declare_parameters()
  {
    this->declare_parameter<std::string>("bus_a_device", "");
    this->declare_parameter<std::string>("bus_b_device", "");
    this->declare_parameter<int64_t>("baud_rate", 57600);
    this->declare_parameter<double>("protocol_version", 2.0);
    this->declare_parameter<int64_t>("flex_motor_id", 2);
    this->declare_parameter<int64_t>("abduction_motor_id", 5);
    this->declare_parameter<int64_t>("opposition_motor_id", 6);
    this->declare_parameter<int64_t>("sample_count", 50);
    this->declare_parameter<int64_t>("sample_period_ms", 100);
  }

  void load_and_validate_parameters()
  {
    bus_a_device_ = this->get_parameter("bus_a_device").as_string();
    bus_b_device_ = this->get_parameter("bus_b_device").as_string();
    const int64_t baud_rate = this->get_parameter("baud_rate").as_int();
    const double protocol_version = this->get_parameter("protocol_version").as_double();
    const int64_t flex_motor_id = this->get_parameter("flex_motor_id").as_int();
    const int64_t abduction_motor_id = this->get_parameter("abduction_motor_id").as_int();
    const int64_t opposition_motor_id = this->get_parameter("opposition_motor_id").as_int();
    const int64_t sample_count = this->get_parameter("sample_count").as_int();
    const int64_t sample_period_ms = this->get_parameter("sample_period_ms").as_int();

    if (bus_a_device_.empty() || bus_b_device_.empty()) {
      throw std::runtime_error("bus_a_device and bus_b_device must not be empty");
    }

    if (baud_rate <= 0 || baud_rate > std::numeric_limits<int>::max()) {
      throw std::runtime_error("baud_rate must be between 1 and INT_MAX");
    }

    if (protocol_version != 2.0) {
      throw std::runtime_error("protocol_version must be 2.0 for XL330");
    }

    const auto valid_motor_id = [](int64_t motor_id) { return motor_id >= 0 && motor_id <= 252; };
    if (
      !valid_motor_id(flex_motor_id) || !valid_motor_id(abduction_motor_id) ||
      !valid_motor_id(opposition_motor_id)) {
      throw std::runtime_error("thumb motor IDs must be between 0 and 252");
    }

    if (
      flex_motor_id == abduction_motor_id || flex_motor_id == opposition_motor_id ||
      abduction_motor_id == opposition_motor_id) {
      throw std::runtime_error("thumb motor IDs must be unique");
    }

    if (sample_count < 0 || sample_count > 10000) {
      throw std::runtime_error("sample_count must be between 0 and 10000; 0 means unlimited");
    }

    if (sample_period_ms < 10 || sample_period_ms > 60000) {
      throw std::runtime_error("sample_period_ms must be between 10 and 60000");
    }

    baud_rate_ = static_cast<int>(baud_rate);
    protocol_version_ = static_cast<float>(protocol_version);
    flex_motor_id_ = static_cast<uint8_t>(flex_motor_id);
    abduction_motor_id_ = static_cast<uint8_t>(abduction_motor_id);
    opposition_motor_id_ = static_cast<uint8_t>(opposition_motor_id);
    sample_limit_ = static_cast<std::size_t>(sample_count);
    sample_period_ = std::chrono::milliseconds(sample_period_ms);
  }

  bool read_axis(AxisSample & axis, int32_t & position, int16_t & current, uint8_t & torque)
  {
    uint32_t raw_position = 0;
    uint16_t raw_current = 0;

    const auto position_result = axis.bus->read_four_bytes(
      axis.motor_id, thing_hardware::xl330::PRESENT_POSITION_ADDRESS, raw_position);
    const auto current_result = axis.bus->read_two_bytes(
      axis.motor_id, thing_hardware::xl330::PRESENT_CURRENT_ADDRESS, raw_current);
    const auto torque_result =
      axis.bus->read_one_byte(axis.motor_id, thing_hardware::xl330::TORQUE_ENABLE_ADDRESS, torque);

    if (!position_result.success || !current_result.success || !torque_result.success) {
      RCLCPP_ERROR(
        this->get_logger(), "Failed to sample axis=%s, ID=%u: position=%s, current=%s, torque=%s",
        axis.name, static_cast<unsigned int>(axis.motor_id),
        position_result.success ? "ok" : position_result.error_message.c_str(),
        current_result.success ? "ok" : current_result.error_message.c_str(),
        torque_result.success ? "ok" : torque_result.error_message.c_str());
      return false;
    }

    position = static_cast<int32_t>(raw_position);
    current = static_cast<int16_t>(raw_current);
    return true;
  }

  void sample_thumb_axes()
  {
    std::array<int32_t, 3> positions{};
    std::array<int16_t, 3> currents{};
    std::array<uint8_t, 3> torques{};

    for (std::size_t index = 0; index < axes_.size(); ++index) {
      if (!read_axis(axes_[index], positions[index], currents[index], torques[index])) {
        finish_sampling();
        return;
      }

      axes_[index].position_sum += positions[index];
      if (positions[index] < axes_[index].min_position) {
        axes_[index].min_position = positions[index];
      }
      if (positions[index] > axes_[index].max_position) {
        axes_[index].max_position = positions[index];
      }
    }

    ++sample_count_;
    if (sample_limit_ == 0U) {
      RCLCPP_INFO(
        this->get_logger(),
        "Thumb sample %zu: flex(ID=%u,pos=%d,current=%d,torque=%u), "
        "abduction(ID=%u,pos=%d,current=%d,torque=%u), "
        "opposition(ID=%u,pos=%d,current=%d,torque=%u)",
        sample_count_, static_cast<unsigned int>(axes_[0].motor_id), positions[0],
        static_cast<int>(currents[0]), static_cast<unsigned int>(torques[0]),
        static_cast<unsigned int>(axes_[1].motor_id), positions[1], static_cast<int>(currents[1]),
        static_cast<unsigned int>(torques[1]), static_cast<unsigned int>(axes_[2].motor_id),
        positions[2], static_cast<int>(currents[2]), static_cast<unsigned int>(torques[2]));
      return;
    }

    RCLCPP_INFO(
      this->get_logger(),
      "Thumb sample %zu/%zu: flex(ID=%u,pos=%d,current=%d,torque=%u), "
      "abduction(ID=%u,pos=%d,current=%d,torque=%u), "
      "opposition(ID=%u,pos=%d,current=%d,torque=%u)",
      sample_count_, sample_limit_, static_cast<unsigned int>(axes_[0].motor_id), positions[0],
      static_cast<int>(currents[0]), static_cast<unsigned int>(torques[0]),
      static_cast<unsigned int>(axes_[1].motor_id), positions[1], static_cast<int>(currents[1]),
      static_cast<unsigned int>(torques[1]), static_cast<unsigned int>(axes_[2].motor_id),
      positions[2], static_cast<int>(currents[2]), static_cast<unsigned int>(torques[2]));

    if (sample_count_ < sample_limit_) {
      return;
    }

    for (const auto & axis : axes_) {
      const double average =
        static_cast<double>(axis.position_sum) / static_cast<double>(sample_count_);
      RCLCPP_INFO(
        this->get_logger(),
        "Thumb calibration result: axis=%s, ID=%u, samples=%zu, average=%.1f, min=%d, max=%d, "
        "range=%d pulse",
        axis.name, static_cast<unsigned int>(axis.motor_id), sample_count_, average,
        axis.min_position, axis.max_position, axis.max_position - axis.min_position);
    }

    finish_sampling();
  }

  void finish_sampling()
  {
    if (sample_timer_) {
      sample_timer_->cancel();
    }
    rclcpp::shutdown();
  }

  std::string bus_a_device_;
  std::string bus_b_device_;
  int baud_rate_{57600};
  float protocol_version_{2.0F};
  uint8_t flex_motor_id_{2};
  uint8_t abduction_motor_id_{5};
  uint8_t opposition_motor_id_{6};
  std::size_t sample_limit_{50};
  std::chrono::milliseconds sample_period_{100};

  std::unique_ptr<thing_hardware::DynamixelBus> bus_a_;
  std::unique_ptr<thing_hardware::DynamixelBus> bus_b_;
  std::array<AxisSample, 3> axes_{};
  rclcpp::TimerBase::SharedPtr sample_timer_;
  std::size_t sample_count_{0};
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<ThumbCalibrationNode>();
  rclcpp::spin(node);

  if (rclcpp::ok()) {
    rclcpp::shutdown();
  }
  return 0;
}
