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

        // ==== port_handler_ init ====
        port_handler_.reset(
            dynamixel::PortHandler::getPortHandler(
                device_name_.c_str()));

        if (!port_handler_)
        {
            RCLCPP_ERROR(
                this->get_logger(),
                "Failed to create PortHandler");
            return;
        }

        if (!port_handler_->openPort())
        {
            RCLCPP_ERROR(
                this->get_logger(),
                "Failed to open port: %s",
                device_name_.c_str());
            return;
        }

        RCLCPP_INFO(
            this->get_logger(),
            "Port opened: %s",
            device_name_.c_str());

        if (!port_handler_->setBaudRate(baud_rate_))
        {
            RCLCPP_ERROR(
                this->get_logger(),
                "Failed to set baud rate: %d",
                baud_rate_);
            return;
        }

        RCLCPP_INFO(
            this->get_logger(),
            "Baud rate configured: %d",
            baud_rate_);
        // ============================

        // ==== packet_handler_ init ====
        packet_handler_ = dynamixel::PacketHandler::getPacketHandler(
            protocol_version_);

        if (!packet_handler_)
        {
            RCLCPP_ERROR(
                this->get_logger(),
                "Failed to get PacketHandler for protocol %.1f",
                protocol_version_);
            return;
        }

        RCLCPP_INFO(
            this->get_logger(),
            "PacketHandler initialized for protocol %.1f",
            packet_handler_->getProtocolVersion());
        // ==============================

        // ==== ping check ====
        uint16_t model_number = 0;
        uint8_t dynamixel_error = 0;

        const int communication_result = packet_handler_->ping(
            port_handler_.get(),
            motor_id_,
            &model_number,
            &dynamixel_error);

        if (communication_result != COMM_SUCCESS)
        {
            RCLCPP_ERROR(
                this->get_logger(),
                "Ping failed for ID %u: %s",
                static_cast<unsigned int>(motor_id_),
                packet_handler_->getTxRxResult(communication_result));
            return;
        }

        if (dynamixel_error != 0)
        {
            RCLCPP_ERROR(
                this->get_logger(),
                "DYNAMIXEL ID %u returned an error: %s",
                static_cast<unsigned int>(motor_id_),
                packet_handler_->getRxPacketError(dynamixel_error));
            return;
        }

        RCLCPP_INFO(
            this->get_logger(),
            "Ping succeeded: ID=%u, model number=%u",
            static_cast<unsigned int>(motor_id_),
            static_cast<unsigned int>(model_number));

        constexpr uint16_t expected_model_number = 1200;

        if (model_number != expected_model_number)
        {
            RCLCPP_WARN(
                this->get_logger(),
                "Unexpected model: expected=%u, received=%u",
                static_cast<unsigned int>(expected_model_number),
                static_cast<unsigned int>(model_number));
        }
        // ====================
    }

private:
    std::string device_name_{
        "/dev/serial/by-id/"
        "usb-FTDI_USB__-__Serial_Converter_FTBIN51S-if00-port0"};
    int baud_rate_{57600};
    float protocol_version_{2.0F};
    uint8_t motor_id_{3};

    std::unique_ptr<dynamixel::PortHandler> port_handler_;
    dynamixel::PacketHandler *packet_handler_{nullptr};
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);

    auto node = std::make_shared<MotorValidatorNode>();
    rclcpp::spin(node);

    rclcpp::shutdown();
    return 0;
}
