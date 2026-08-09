"""Pure state management for seven-axis keyboard teleoperation."""

from __future__ import annotations

from dataclasses import dataclass, field


AXIS_NAMES = (
    'thumb_flex',
    'index_flex',
    'middle_flex',
    'ring_flex',
    'little_flex',
)
THUMB_POSES = {
    'open': (0.0, 1.0),
    'neutral': (0.2, 0.0),
    'grasp': (0.6, 0.2),
    'folded': (1.0, 1.0),
}
THUMB_POSE_KEYS = {
    'a': 'open',
    's': 'neutral',
    'd': 'grasp',
    'f': 'folded',
}


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp a normalized logical-axis value to its valid range."""
    return max(lower, min(upper, value))


@dataclass
class TeleopCore:
    """Track axis selection and normalized command targets."""

    step_size: float = 0.01
    selected_index: int = 0
    thumb_pose: str = 'neutral'
    targets: list[float] = field(
        default_factory=lambda: [0.0] * len(AXIS_NAMES)
    )

    def __post_init__(self) -> None:
        if not 0.0 < self.step_size <= 1.0:
            raise ValueError('step_size must be in the range (0.0, 1.0]')
        if len(self.targets) != len(AXIS_NAMES):
            raise ValueError('targets must contain exactly five flexion values')
        self.targets = [clamp(float(value)) for value in self.targets]
        self.select(self.selected_index)
        self.set_thumb_pose(self.thumb_pose)

    @property
    def selected_name(self) -> str:
        """Return the selected logical-axis name."""
        return AXIS_NAMES[self.selected_index]

    @property
    def selected_value(self) -> float:
        """Return the selected logical-axis target."""
        return self.targets[self.selected_index]

    def select(self, index: int) -> None:
        """Select an axis using its zero-based index."""
        if not 0 <= index < len(AXIS_NAMES):
            raise ValueError('selected axis index must be in the range 0..4')
        self.selected_index = index

    def select_key(self, key: str) -> bool:
        """Select a flexion axis from keyboard keys 1 through 5."""
        if key not in '12345':
            return False
        self.select(int(key) - 1)
        return True

    def adjust_selected(self, multiplier: float = 1.0) -> float:
        """Adjust and return the selected target by a step multiplier."""
        value = self.selected_value + self.step_size * multiplier
        self.targets[self.selected_index] = clamp(value)
        return self.selected_value

    def set_home(self) -> None:
        """Set all normalized logical-axis targets to their home value."""
        self.targets = [0.0] * len(AXIS_NAMES)
        self.thumb_pose = 'neutral'

    def set_thumb_pose(self, pose_name: str) -> None:
        """Select one of the validated functional thumb poses."""
        if pose_name not in THUMB_POSES:
            raise ValueError(f'unknown thumb pose: {pose_name}')
        self.thumb_pose = pose_name

    def set_thumb_pose_key(self, key: str) -> bool:
        """Select a functional thumb pose using the home-row shortcuts."""
        pose_name = THUMB_POSE_KEYS.get(key.lower())
        if pose_name is None:
            return False
        self.set_thumb_pose(pose_name)
        return True

    def command_values(self) -> dict[str, float]:
        """Return a copy of targets keyed by HandCommand field name."""
        thumb_opp, thumb_abd = THUMB_POSES[self.thumb_pose]
        values = dict(zip(AXIS_NAMES, self.targets))
        return {
            'thumb_opp': thumb_opp,
            'thumb_abd': thumb_abd,
            **values,
        }
