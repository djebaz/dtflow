"""The planner's error type, shared by the planning, random-draw and dynamic-prompt modules."""

from dtflow.services.config import ConfigError


class PlanError(ConfigError):
    """Unreadable, unsupported or invalid plan; nothing was enqueued."""
