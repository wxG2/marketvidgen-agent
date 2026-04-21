class WaitingConfirmation(Exception):
    """Raised when the replication planner needs user confirmation before continuing."""


class WaitingPromptReview(Exception):
    """Raised after PromptEngineer when HITL prompt review is enabled."""
