"""Canonical conversation IR — the shared in-memory shape every adapter
reads into and every adapter writes from.

Why a new IR (no community standard exists)
    As of mid-2026 there is no community spec for *stored* conversation
    portability across agent tools. MCP, A2A, ACP, and Agent Client Protocol
    standardize live interaction. The closest message-shape standard is
    OpenTelemetry's GenAI Semantic Conventions `InputMessages` JSON schema —
    designed for telemetry, but structurally a near-match for the shape we
    need. Our internal block names follow Anthropic Messages conventions
    (text / thinking / tool_use / tool_result / image / attachment) so the
    Claude Code adapter is near-identity; an OTel-conformant view can be
    derived by renaming (tool_use→tool_call_request,
    tool_result→tool_call_response, thinking→reasoning, image→blob|uri).

Design notes:
    - Content blocks follow Anthropic Messages API shape (text / thinking /
      tool_use / tool_result / image / attachment) because both Claude Code's
      JSONL and modern OpenCode parts can flatten into it without loss.
    - Events are linked-list ordered via parent_id (Claude's native model).
      Adapters that store messages as flat ordered rows (OpenCode) recover
      ordering from timestamps and synthesize parent_id at read time.
    - One OpenCode `tool` part = one canonical tool_use block + one
      canonical tool_result block, conventionally placed in adjacent
      assistant/user events (Anthropic convention). The opencode adapter
      performs this split; the claude adapter never needs to.
    - Content blocks are plain dicts, not dataclasses, so they round-trip
      through json.dumps/json.loads without conversion. Helper constructors
      give a typed feel without the boilerplate.

Schema version is bumped when block shapes change incompatibly.
"""
from __future__ import annotations

import json
import uuid as _uuid_mod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CANONICAL_VERSION = "1.0"


# =====================================================================
# Content block constructors (return plain dicts — JSON-native)
# =====================================================================

def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def thinking_block(thinking: str, signature: Optional[str] = None) -> Dict[str, Any]:
    b = {"type": "thinking", "thinking": thinking}
    if signature:
        b["signature"] = signature
    return b


def tool_use_block(id: str, name: str, input: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_use", "id": id, "name": name, "input": input}


def tool_result_block(
    tool_use_id: str,
    content: Any,
    is_error: bool = False,
) -> Dict[str, Any]:
    # content can be a string OR a list of blocks (text/image). Mirrors
    # Anthropic's accepted shapes.
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def image_block(
    media_type: str,
    data: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    if data is not None:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    if url is not None:
        return {
            "type": "image",
            "source": {"type": "url", "media_type": media_type, "url": url},
        }
    raise ValueError("image_block requires either data or url")


def attachment_block(
    path: str,
    mime_type: Optional[str] = None,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    b: Dict[str, Any] = {"type": "attachment", "path": path}
    if mime_type:
        b["mime_type"] = mime_type
    if text is not None:
        b["text"] = text
    return b


# =====================================================================
# Event + Conversation
# =====================================================================

@dataclass
class Event:
    """One atomic record in a conversation timeline.

    type:
        "message"  user / assistant utterance with content blocks.
        "system"   system-level event (permission change, session-resumed,
                   summarization marker, attachment side-load).
        "meta"     adapter-private bookkeeping (step-start/step-finish in
                   OpenCode). Lossless adapters may preserve via metadata;
                   most targets drop these.

    role: "user" | "assistant" | "system" | "tool" | None
    content: list of content-block dicts (see helpers above)
    parent_id: prior event's id (linked-list ordering). None for the first
               event in the conversation.
    metadata: free-form per-adapter extras (model, usage tokens, cost,
              provider IDs, original IDs for back-mapping, etc.).
    """
    id: str
    parent_id: Optional[str]
    type: str
    role: Optional[str]
    content: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        return cls(
            id=d["id"],
            parent_id=d.get("parent_id"),
            type=d["type"],
            role=d.get("role"),
            content=list(d.get("content") or []),
            timestamp=d.get("timestamp"),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class Conversation:
    """A whole session in canonical form.

    source_agent: "claude" | "opencode" | "hermes" | future
    source_session_id: the original session/transcript identifier
    cwd: working directory the session ran in (used by writers to slug a
         project dir for Claude or to populate session.directory for OC).
    metadata: free-form session-level extras (model, agent name, provider,
              cost totals, share URLs, original schema version, ...).
    events: ordered timeline. Order = correct linear order; parent_id chain
            mirrors it for adapters that store events as linked lists.
    """
    version: str
    source_agent: str
    source_session_id: str
    cwd: Optional[str]
    title: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    events: List[Event] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ----- construction helpers -----

    @classmethod
    def new(
        cls,
        source_agent: str,
        source_session_id: str,
        cwd: Optional[str] = None,
        title: Optional[str] = None,
    ) -> "Conversation":
        now = _iso_now()
        return cls(
            version=CANONICAL_VERSION,
            source_agent=source_agent,
            source_session_id=source_session_id,
            cwd=cwd,
            title=title,
            created_at=now,
            updated_at=now,
            events=[],
            metadata={},
        )

    def append_event(
        self,
        type: str,
        role: Optional[str],
        content: Optional[List[Dict[str, Any]]] = None,
        timestamp: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        id: Optional[str] = None,
    ) -> Event:
        ev = Event(
            id=id or new_uuid(),
            parent_id=self.events[-1].id if self.events else None,
            type=type,
            role=role,
            content=list(content or []),
            timestamp=timestamp,
            metadata=dict(metadata or {}),
        )
        self.events.append(ev)
        return ev

    # ----- serialization -----

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # asdict converts Event → dict; nothing else to do
        return d

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Conversation":
        return cls(
            version=d.get("version", CANONICAL_VERSION),
            source_agent=d["source_agent"],
            source_session_id=d["source_session_id"],
            cwd=d.get("cwd"),
            title=d.get("title"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            events=[Event.from_dict(e) for e in d.get("events", [])],
            metadata=dict(d.get("metadata") or {}),
        )

    @classmethod
    def from_json(cls, s: str) -> "Conversation":
        return cls.from_dict(json.loads(s))

    # ----- introspection -----

    def message_events(self) -> List[Event]:
        return [e for e in self.events if e.type == "message"]

    def user_text_events(self) -> List[Event]:
        """Events whose role is user and whose content contains plain text
        (not tool_result). Useful for branch pickers."""
        out = []
        for e in self.events:
            if e.type != "message" or e.role != "user":
                continue
            blocks = e.content or []
            if any(b.get("type") == "text" for b in blocks):
                out.append(e)
            elif isinstance(blocks, str):
                # legacy shape never appears here because adapters normalize
                out.append(e)
        return out


# =====================================================================
# Utilities
# =====================================================================

def new_uuid() -> str:
    return str(_uuid_mod.uuid4())


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_from_epoch_ms(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    except Exception:
        return None


def epoch_ms_from_iso(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        # accept "...Z" or "...+00:00"
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        return int(datetime.fromisoformat(s2).timestamp() * 1000)
    except Exception:
        return None
