#include "thing_hardware/thumb_motion_controller.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <utility>

namespace thing_hardware
{

ThumbMotionController::ThumbMotionController(
  std::vector<ThumbPoseConfig> poses, int32_t position_tolerance, int32_t approach_tolerance,
  std::chrono::duration<double> phase_timeout, int32_t flex_clearance_position,
  std::vector<int32_t> opposition_reversal_positions,
  std::vector<int32_t> abduction_reversal_positions)
: poses_(std::move(poses)),
  position_tolerance_(position_tolerance),
  approach_tolerance_(approach_tolerance),
  phase_timeout_(phase_timeout),
  flex_clearance_position_(flex_clearance_position),
  opposition_reversal_positions_(std::move(opposition_reversal_positions)),
  abduction_reversal_positions_(std::move(abduction_reversal_positions))
{
  if (poses_.empty()) {
    throw std::invalid_argument("thumb poses must not be empty");
  }
  if (position_tolerance_ < 0 || approach_tolerance_ < 0 || phase_timeout_.count() <= 0.0) {
    throw std::invalid_argument("thumb tolerance and timeout must be valid");
  }
  const std::size_t transition_count = poses_.size() * poses_.size();
  if (
    opposition_reversal_positions_.size() != transition_count ||
    abduction_reversal_positions_.size() != transition_count) {
    throw std::invalid_argument("thumb reversal matrices must have pose_count squared entries");
  }
}

void ThumbMotionController::start(
  const std::string & source_pose, const std::string & target_pose, double target_flex,
  std::chrono::steady_clock::time_point now)
{
  if (!std::isfinite(target_flex) || target_flex < 0.0 || target_flex > 1.0) {
    throw std::invalid_argument("target_flex must be in [0.0, 1.0]");
  }
  source_pose_index_ = find_pose(source_pose);
  target_pose_index_ = find_pose(target_pose);
  target_flex_ = target_flex;
  error_message_.clear();
  enter_phase(
    source_pose_index_ == target_pose_index_ ? ThumbMotionPhase::FLEX_TO_TARGET
                                             : ThumbMotionPhase::FLEX_TO_CLEARANCE,
    now);
}

void ThumbMotionController::reset()
{
  phase_ = ThumbMotionPhase::IDLE;
  error_message_.clear();
}

bool ThumbMotionController::update(
  const std::array<int32_t, 3> & present_positions, std::chrono::steady_clock::time_point now)
{
  if (
    phase_ == ThumbMotionPhase::IDLE || phase_ == ThumbMotionPhase::COMPLETE ||
    phase_ == ThumbMotionPhase::ERROR) {
    return false;
  }

  if (now - phase_started_at_ > phase_timeout_) {
    error_message_ = std::string("timeout in phase ") + phase_name(phase_);
    phase_ = ThumbMotionPhase::ERROR;
    return true;
  }

  const auto target = phase_target();
  const auto index = static_cast<std::size_t>(target.axis);
  const bool approach_phase = phase_ == ThumbMotionPhase::OPPOSITION_TO_APPROACH_START ||
                              phase_ == ThumbMotionPhase::ABDUCTION_TO_APPROACH_START ||
                              phase_ == ThumbMotionPhase::OPPOSITION_TO_REVERSAL_TRANSITION ||
                              phase_ == ThumbMotionPhase::ABDUCTION_TO_REVERSAL_TRANSITION;
  const int32_t tolerance = approach_phase ? approach_tolerance_ : position_tolerance_;
  if (std::abs(present_positions[index] - target.position) <= tolerance) {
    advance(now);
    return true;
  }
  return false;
}

ThumbMotionPhase ThumbMotionController::phase() const { return phase_; }

double ThumbMotionController::phase_elapsed_seconds(std::chrono::steady_clock::time_point now) const
{
  if (phase_ == ThumbMotionPhase::IDLE) {
    return 0.0;
  }
  return std::chrono::duration<double>(now - phase_started_at_).count();
}

double ThumbMotionController::phase_timeout_seconds() const { return phase_timeout_.count(); }

ThumbPhaseTarget ThumbMotionController::phase_target() const
{
  const auto & target = poses_.at(target_pose_index_);
  switch (phase_) {
    case ThumbMotionPhase::FLEX_TO_CLEARANCE:
      return {ThumbAxis::FLEX, flex_clearance_position_};
    case ThumbMotionPhase::ABDUCTION_TO_REVERSAL_TRANSITION:
      return {ThumbAxis::ABDUCTION, reversal_position(abduction_reversal_positions_)};
    case ThumbMotionPhase::OPPOSITION_TO_REVERSAL_TRANSITION:
      return {ThumbAxis::OPPOSITION, reversal_position(opposition_reversal_positions_)};
    case ThumbMotionPhase::ABDUCTION_TO_APPROACH_START:
      return {ThumbAxis::ABDUCTION, target.abduction_approach_start};
    case ThumbMotionPhase::OPPOSITION_TO_APPROACH_START:
      return {ThumbAxis::OPPOSITION, target.opposition_approach_start};
    case ThumbMotionPhase::OPPOSITION_TO_TARGET:
      return {ThumbAxis::OPPOSITION, target.opposition_position};
    case ThumbMotionPhase::ABDUCTION_TO_TARGET:
      return {ThumbAxis::ABDUCTION, target.abduction_position};
    case ThumbMotionPhase::FLEX_TO_TARGET:
      return {ThumbAxis::FLEX, flex_target()};
    default:
      throw std::logic_error("current thumb phase has no target");
  }
}

const std::string & ThumbMotionController::source_pose_name() const
{
  return poses_.at(source_pose_index_).name;
}

const std::string & ThumbMotionController::target_pose_name() const
{
  return poses_.at(target_pose_index_).name;
}

const std::string & ThumbMotionController::error_message() const { return error_message_; }

const char * ThumbMotionController::phase_name(ThumbMotionPhase phase)
{
  switch (phase) {
    case ThumbMotionPhase::IDLE:
      return "idle";
    case ThumbMotionPhase::FLEX_TO_CLEARANCE:
      return "flex_to_clearance";
    case ThumbMotionPhase::ABDUCTION_TO_REVERSAL_TRANSITION:
      return "abduction_to_reversal_transition";
    case ThumbMotionPhase::OPPOSITION_TO_REVERSAL_TRANSITION:
      return "opposition_to_reversal_transition";
    case ThumbMotionPhase::ABDUCTION_TO_APPROACH_START:
      return "abduction_to_approach_start";
    case ThumbMotionPhase::OPPOSITION_TO_APPROACH_START:
      return "opposition_to_approach_start";
    case ThumbMotionPhase::OPPOSITION_TO_TARGET:
      return "opposition_to_target";
    case ThumbMotionPhase::ABDUCTION_TO_TARGET:
      return "abduction_to_target";
    case ThumbMotionPhase::FLEX_TO_TARGET:
      return "flex_to_target";
    case ThumbMotionPhase::COMPLETE:
      return "complete";
    case ThumbMotionPhase::ERROR:
      return "error";
  }
  return "unknown";
}

const char * ThumbMotionController::axis_name(ThumbAxis axis)
{
  switch (axis) {
    case ThumbAxis::FLEX:
      return "flex";
    case ThumbAxis::ABDUCTION:
      return "abduction";
    case ThumbAxis::OPPOSITION:
      return "opposition";
  }
  return "unknown";
}

std::size_t ThumbMotionController::find_pose(const std::string & name) const
{
  const auto iterator = std::find_if(
    poses_.begin(), poses_.end(), [&name](const auto & pose) { return pose.name == name; });
  if (iterator == poses_.end()) {
    throw std::invalid_argument("unknown thumb pose: " + name);
  }
  return static_cast<std::size_t>(std::distance(poses_.begin(), iterator));
}

void ThumbMotionController::enter_phase(
  ThumbMotionPhase phase, std::chrono::steady_clock::time_point now)
{
  phase_ = phase;
  phase_started_at_ = now;
}

void ThumbMotionController::advance(std::chrono::steady_clock::time_point now)
{
  switch (phase_) {
    case ThumbMotionPhase::FLEX_TO_CLEARANCE:
      advance_after_clearance(now);
      break;
    case ThumbMotionPhase::ABDUCTION_TO_REVERSAL_TRANSITION:
      if (opposition_reverses()) {
        enter_phase(ThumbMotionPhase::OPPOSITION_TO_REVERSAL_TRANSITION, now);
      } else {
        advance_after_reversals(now);
      }
      break;
    case ThumbMotionPhase::OPPOSITION_TO_REVERSAL_TRANSITION:
      advance_after_reversals(now);
      break;
    case ThumbMotionPhase::OPPOSITION_TO_APPROACH_START:
      if (abduction_needs_approach()) {
        enter_phase(ThumbMotionPhase::ABDUCTION_TO_APPROACH_START, now);
      } else {
        enter_phase(ThumbMotionPhase::OPPOSITION_TO_TARGET, now);
      }
      break;
    case ThumbMotionPhase::ABDUCTION_TO_APPROACH_START:
      enter_phase(ThumbMotionPhase::OPPOSITION_TO_TARGET, now);
      break;
    case ThumbMotionPhase::OPPOSITION_TO_TARGET:
      enter_phase(ThumbMotionPhase::ABDUCTION_TO_TARGET, now);
      break;
    case ThumbMotionPhase::ABDUCTION_TO_TARGET:
      enter_phase(ThumbMotionPhase::FLEX_TO_TARGET, now);
      break;
    case ThumbMotionPhase::FLEX_TO_TARGET:
      enter_phase(ThumbMotionPhase::COMPLETE, now);
      break;
    default:
      throw std::logic_error("cannot advance current thumb phase");
  }
}

void ThumbMotionController::advance_after_clearance(std::chrono::steady_clock::time_point now)
{
  if (abduction_reverses()) {
    enter_phase(ThumbMotionPhase::ABDUCTION_TO_REVERSAL_TRANSITION, now);
  } else if (opposition_reverses()) {
    enter_phase(ThumbMotionPhase::OPPOSITION_TO_REVERSAL_TRANSITION, now);
  } else {
    advance_after_reversals(now);
  }
}

void ThumbMotionController::advance_after_reversals(std::chrono::steady_clock::time_point now)
{
  if (opposition_needs_approach()) {
    enter_phase(ThumbMotionPhase::OPPOSITION_TO_APPROACH_START, now);
  } else if (abduction_needs_approach()) {
    enter_phase(ThumbMotionPhase::ABDUCTION_TO_APPROACH_START, now);
  } else {
    enter_phase(ThumbMotionPhase::OPPOSITION_TO_TARGET, now);
  }
}

bool ThumbMotionController::opposition_reverses() const
{
  return poses_.at(source_pose_index_).opposition_approach_direction !=
         poses_.at(target_pose_index_).opposition_approach_direction;
}

bool ThumbMotionController::abduction_reverses() const
{
  return poses_.at(source_pose_index_).abduction_approach_direction !=
         poses_.at(target_pose_index_).abduction_approach_direction;
}

bool ThumbMotionController::opposition_needs_approach() const
{
  const auto & source = poses_.at(source_pose_index_);
  const auto & target = poses_.at(target_pose_index_);
  return !opposition_reverses() && !movement_matches_direction(
                                     source.opposition_position, target.opposition_position,
                                     target.opposition_approach_direction);
}

bool ThumbMotionController::abduction_needs_approach() const
{
  const auto & source = poses_.at(source_pose_index_);
  const auto & target = poses_.at(target_pose_index_);
  return !abduction_reverses() && !movement_matches_direction(
                                    source.abduction_position, target.abduction_position,
                                    target.abduction_approach_direction);
}

bool ThumbMotionController::movement_matches_direction(
  int32_t source, int32_t target, int8_t direction)
{
  if (source == target) {
    return true;
  }
  return (target > source) == (direction > 0);
}

int32_t ThumbMotionController::reversal_position(const std::vector<int32_t> & positions) const
{
  return positions.at(source_pose_index_ * poses_.size() + target_pose_index_);
}

int32_t ThumbMotionController::flex_target() const
{
  const auto & pose = poses_.at(target_pose_index_);
  return static_cast<int32_t>(std::lround(
    pose.flex_home + target_flex_ * static_cast<double>(pose.flex_closed - pose.flex_home)));
}

}  // namespace thing_hardware
