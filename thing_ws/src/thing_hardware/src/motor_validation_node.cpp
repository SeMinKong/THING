#include <cstdint>
#include <string>
#include <memory>

#include "rclcpp/rclcpp.hpp"
#include "dynamixel_sdk/packet_handler.h"
#include "dynamixel_sdk/port_handler.h"

class MotorValidatorNode : public rclcpp::Node
{
public:
    MotorValidatorNode() : Node("motor_validator")
    {
        RCLCPP_INFO(
            this->get_logger(),
            "Device: %s, baud rate: %d, protocol: %.1f, motor ID: %u",
            device_name_.c_str(),
            baud_rate_,
            protocol_version_,
            static_cast<unsigned int>(motor_id_));
    }

private:
    std::string device_name_{
        "/dev/serial/by-id/"
        "usb-FTDI_USB__-__Serial_Converter_FTBIN51S-if00-port0"};
    int baud_rate_{57600};
    float protocol_version_{2.0F};
    uint8_t motor_id_{1};
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);

    auto node = std::make_shared<MotorValidatorNode>();
    rclcpp::spin(node);

    rclcpp::shutdown();
    return 0;
}
