"""Input handling — chatbox conversation, task types, attachments."""
from .task_types import TASK_TYPES, PROJECTS, SCOPE_LEVELS, clarify_instructions
from .conversation import ConversationManager

__all__ = [
    "TASK_TYPES", "PROJECTS", "SCOPE_LEVELS",
    "clarify_instructions", "ConversationManager",
]
