"""ccfind_core — canonical conversation IR and adapter framework.

The foundation for porting AI coding-agent conversations between tools
(Claude Code, OpenCode, Hermes, future agents) via a shared in-memory
data model: source format → Conversation → target format.

Public surface:
    canonical.Conversation, Event, ContentBlock helpers
    adapters.read(agent, session_id, **opts) -> Conversation
    adapters.write(agent, conversation, **opts) -> dict
    adapters.capabilities(agent) -> dict
"""
from .canonical import (
    CANONICAL_VERSION,
    Conversation,
    Event,
    text_block,
    thinking_block,
    tool_use_block,
    tool_result_block,
    image_block,
    attachment_block,
)
from . import adapters

__all__ = [
    "CANONICAL_VERSION",
    "Conversation",
    "Event",
    "text_block",
    "thinking_block",
    "tool_use_block",
    "tool_result_block",
    "image_block",
    "attachment_block",
    "adapters",
]
