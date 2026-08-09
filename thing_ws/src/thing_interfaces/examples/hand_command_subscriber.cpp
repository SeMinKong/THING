#include <memory>

#include "rclcpp/rclcpp.hpp"
#include "thing_interfaces/msg/hand_command.hpp"

class HandCommandSubscriber : public rclcpp::Node
{
public:
  HandCommandSubscriber()
  : Node("hand_command_subscriber")
  {
    subscription_ =
      create_subscription<thing_interfaces::msg::HandCommand>(
      "/thing/command/test",
      10,
      std::bind(
        &HandCommandSubscriber::command_callback,
        this,
        std::placeholders::_1));
  }

private:
  void command_callback(
    const thing_interfaces::msg::HandCommand::SharedPtr message)
  {
    RCLCPP_INFO(
      get_logger(),
      "sequence=%u source=%u "
      "thumb_flex=%.3f thumb_opp=%.3f thumb_abd=%.3f "
      "index_flex=%.3f middle_flex=%.3f "
      "ring_flex=%.3f little_flex=%.3f "
      "speed_limit=%.3f confidence=%.3f",
      message->sequence,
      message->source,
      message->thumb_flex,
      message->thumb_opp,
      message->thumb_abd,
      message->index_flex,
      message->middle_flex,
      message->ring_flex,
      message->little_flex,
      message->speed_limit,
      message->confidence);
  }

  rclcpp::Subscription<
    thing_interfaces::msg::HandCommand>::SharedPtr subscription_;
};

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<HandCommandSubscriber>());
  rclcpp::shutdown();

  return 0;
}