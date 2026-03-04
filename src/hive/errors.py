"""
HIVE Errors — exception hierarchy for the HIVE Agent Mesh.
"""


class HiveError(Exception):
    """Base exception for all HIVE subsystem errors."""


class AgentCollapsedError(HiveError):
    """Raised when attempting to operate on a collapsed agent."""

    def __init__(self, agent_id: str, message: str = ""):
        self.agent_id = agent_id
        super().__init__(message or f"Agent {agent_id} is collapsed")


class AgentPausedError(HiveError):
    """Raised when attempting to execute on a paused agent."""

    def __init__(self, agent_id: str, message: str = ""):
        self.agent_id = agent_id
        super().__init__(message or f"Agent {agent_id} is paused")


class AgentSpawnDeniedError(HiveError):
    """Raised when agent spawn is denied by governance or capacity."""

    def __init__(self, reason: str = ""):
        super().__init__(reason or "Agent spawn denied")


class ScopedSoulViolationError(HiveError):
    """Raised when an agent action violates its scoped Soul constraints."""

    def __init__(self, agent_id: str, action: str, reason: str = ""):
        self.agent_id = agent_id
        self.action = action
        super().__init__(
            reason or f"Agent {agent_id} soul violation on action: {action}"
        )


class TaskDecompositionError(HiveError):
    """Raised when task decomposition fails."""


class SubAgentTimeoutError(HiveError):
    """Raised when a sub-agent exceeds its timeout."""

    def __init__(self, agent_id: str, timeout_seconds: int):
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Agent {agent_id} exceeded timeout of {timeout_seconds}s"
        )


class UABControlError(HiveError):
    """Raised when UAB bridge operations fail."""


class MaxAgentsExceededError(HiveError):
    """Raised when max concurrent agent limit is reached."""

    def __init__(self, max_agents: int):
        self.max_agents = max_agents
        super().__init__(f"Max concurrent agents ({max_agents}) exceeded")


class InterventionRequiresReasonError(HiveError):
    """Raised when an intervention is attempted without a reason."""

    def __init__(self, intervention_type: str):
        self.intervention_type = intervention_type
        super().__init__(
            f"Intervention '{intervention_type}' requires a non-empty reason"
        )
