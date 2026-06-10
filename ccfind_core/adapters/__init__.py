"""Adapter registry — discover capabilities per agent and dispatch
read/write/list calls.

Each adapter exposes (optionally) these module-level callables:
    read_session(session_id, **opts) -> Conversation
    write_session(conv, target_session_id=None, **opts) -> dict
    list_sessions(**opts) -> list[dict]

The registry tracks which are implemented per agent so callers can refuse
unsupported directions cleanly (e.g. "OpenCode write not yet supported").
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..canonical import Conversation
from . import claude, opencode, hermes


# (agent_name, capability) → callable | None
_REGISTRY: Dict[str, Dict[str, Optional[Callable]]] = {
    "claude": {
        "read": getattr(claude, "read_session", None),
        "write": getattr(claude, "write_session", None),
        "list": getattr(claude, "list_sessions", None),
    },
    "opencode": {
        "read": getattr(opencode, "read_session", None),
        "write": None,  # write deferred; expose as not capable
        "list": getattr(opencode, "list_sessions", None),
    },
    "hermes": {
        "read": getattr(hermes, "read_session", None),
        "write": None,  # Hermes write deferred — see hermes.py docstring
        "list": getattr(hermes, "list_sessions", None),
    },
}


def supported_agents() -> List[str]:
    return sorted(_REGISTRY.keys())


def capabilities(agent: str) -> Dict[str, bool]:
    if agent not in _REGISTRY:
        raise KeyError(f"unknown agent {agent!r}")
    return {cap: (fn is not None) for cap, fn in _REGISTRY[agent].items()}


def can(agent: str, capability: str) -> bool:
    if agent not in _REGISTRY:
        return False
    return _REGISTRY[agent].get(capability) is not None


def read(agent: str, session_id: str, **opts: Any) -> Conversation:
    fn = _resolve(agent, "read")
    return fn(session_id, **opts)


def write(
    agent: str,
    conversation: Conversation,
    target_session_id: Optional[str] = None,
    **opts: Any,
) -> Dict[str, Any]:
    fn = _resolve(agent, "write")
    return fn(conversation, target_session_id=target_session_id, **opts)


def list_sessions(agent: str, **opts: Any) -> List[Dict[str, Any]]:
    fn = _resolve(agent, "list")
    return fn(**opts)


def _resolve(agent: str, capability: str) -> Callable:
    if agent not in _REGISTRY:
        raise KeyError(
            f"unknown agent {agent!r}. supported: {supported_agents()}"
        )
    fn = _REGISTRY[agent].get(capability)
    if fn is None:
        caps = capabilities(agent)
        raise NotImplementedError(
            f"adapter {agent!r} does not support {capability!r}. "
            f"capabilities: {caps}"
        )
    return fn


__all__ = [
    "supported_agents",
    "capabilities",
    "can",
    "read",
    "write",
    "list_sessions",
    "claude",
    "opencode",
    "hermes",
]
