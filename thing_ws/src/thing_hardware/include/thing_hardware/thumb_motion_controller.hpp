#ifndef THING_HARDWARE__THUMB_MOTION_CONTROLLER_HPP_
#define THING_HARDWARE__THUMB_MOTION_CONTROLLER_HPP_

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace thing_hardware
{

enum class ThumbAxis : std::size_t { FLEX = 0, ABDUCTION = 1, OPPOSITION = 2 };

enum class ThumbMotionPhase {
  IDLE,
  FLEX_TO_CLEARANCE,
  ABDUCTION_TO_REVERSAL_TRANSITION,
  OPPOSITION_TO_REVERSAL_TRANSITION,
  ABDUCTION_TO_APPROACH_START,
  OPPOSITION_TO_APPROACH_START,
  OPPOSITION_TO_TARGET,
  ABDUCTION_TO_TARGET,
  FLEX_TO_TARGET,
  COMPLETE,
  ERROR
};

struct ThumbPoseConfig
{
  std::string name;
  double opposition{0.0};
  double abduction{0.0};
  int32_t opposition_position{0};
  int8_t opposition_approach_direction{0};
  int32_t opposition_approach_start{0};
  int32_t abduction_position{0};
  int8_t abduction_approach_direction{0};
  int32_t abduction_approach_start{0};
  int32_t flex_home{0};
  int32_t flex_closed{0};
};

struct ThumbPhaseTarget
{
  ThumbAxis axis{ThumbAxis::FLEX};
  int32_t position{0};
};

class ThumbMotionController
{
public:
  ThumbMotionController(
    std::vector<ThumbPoseConfig> poses, int32_t position_tolerance, int32_t approach_tolerance,
    std::chrono::duration<double> phase_timeout, int32_t flex_clearance_position,
    std::vector<int32_t> opposition_reversal_positions,
    std::vector<int32_t> abduction_reversal_positions);

  void start(
    const std::string & source_pose, const std::string & target_pose, double target_flex,
    std::chrono::steady_clock::time_point now);

  void reset();

  bool update(
    const std::array<int32_t, 3> & present_positions, std::chrono::steady_clock::time_point now);

  ThumbMotionPhase phase() const;
  ThumbPhaseTarget phase_target() const;
  double phase_elapsed_seconds(std::chrono::steady_clock::time_point now) const;
  double phase_timeout_seconds() const;
  const std::string & source_pose_name() const;
  const std::string & target_pose_name() const;
  const std::string & error_message() const;

  static const char * phase_name(ThumbMotionPhase phase);
  static const char * axis_name(ThumbAxis axis);

private:
  std::size_t find_pose(const std::string & name) const;
  void enter_phase(ThumbMotionPhase phase, std::chrono::steady_clock::time_point now);
  void advance(std::chrono::steady_clock::time_point now);
  void advance_after_clearance(std::chrono::steady_clock::time_point now);
  void advance_after_reversals(std::chrono::steady_clock::time_point now);
  bool opposition_reverses() const;
  bool abduction_reverses() const;
  bool opposition_needs_approach() const;
  bool abduction_needs_approach() const;
  static bool movement_matches_direction(int32_t source, int32_t target, int8_t direction);
  int32_t reversal_position(const std::vector<int32_t> & positions) const;
  int32_t flex_target() const;

  std::vector<ThumbPoseConfig> poses_;
  int32_t position_tolerance_{0};
  int32_t approach_tolerance_{0};
  std::chrono::duration<double> phase_timeout_;
  std::size_t source_pose_index_{0};
  std::size_t target_pose_index_{0};
  double target_flex_{0.0};
  int32_t flex_clearance_position_{0};
  std::vector<int32_t> opposition_reversal_positions_;
  std::vector<int32_t> abduction_reversal_positions_;
  ThumbMotionPhase phase_{ThumbMotionPhase::IDLE};
  std::chrono::steady_clock::time_point phase_started_at_;
  std::string error_message_;
};

}  // namespace thing_hardware

#endif  // THING_HARDWARE__THUMB_MOTION_CONTROLLER_HPP_
