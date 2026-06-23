"""Claude Code adapter — read and write `~/.claude/projects/<slug>/<sid>.jsonl`.

Read direction (claude → canonical):
    Each JSONL line is one event. Top-level `type` ∈
    {user, assistant, system, attachment, permission-mode, ...}. For user /
    assistant lines, `message.content` is either a string (legacy) or already
    a list of Anthropic-style blocks (text / tool_use / tool_result /
    thinking / image). We preserve them as-is. Per-line metadata (model,
    usage, requestId, isSidechain, gitBranch, version) is stashed in
    Event.metadata so a round-trip can put it back.

Write direction (canonical → claude):
    Allocate a fresh session UUID (or use provided). Derive the slug from
    cwd. Write each canonical event as one JSONL line. parentUuid chain is
    rebuilt from canonical event order. Tool_use blocks live in assistant
    messages; tool_result blocks live in user messages (Anthropic
    convention); if a canonical assistant event mixes both, we split it.
    `forkedFrom: {sessionId, messageUuid}` is written if conversation
    metadata carries it (Claude /btw-compatible).
"""
from __future__ import annotations

import json
import os
import re
import uuid as _uuid_mod
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..canonical import (
    CANONICAL_VERSION,
    Conversation,
    Event,
    new_uuid,
    iso_from_epoch_ms,
)

# =====================================================================
# Paths
# =====================================================================

HOME = os.path.expanduser("~")


def _default_root() -> str:
    """The active account's `projects` dir. Honors CLAUDE_CONFIG_DIR so that
    multi-account setups read/write the same place `claude` itself uses."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
    return os.path.join(base, "projects")


# Evaluated at import for back-compat; pass `root=` to override per call.
CLAUDE_ROOT = _default_root()


def slug_for_cwd(cwd: str) -> str:
    """Claude derives the project dir from cwd by replacing every
    non-alphanumeric character with `-` (so `/`, `.`, `_`, spaces … all become
    `-`). Verified against real transcripts: current Claude sanitizes
    `~/Documents/x.nosync/y` → `-Users-…-x-nosync-y`. Older builds (≤2.1.0)
    only replaced `/`, which is why a session created then can land in a folder
    today's Claude won't resume from — matching this rule keeps writes
    resumable."""
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def session_path(slug: str, session_id: str, root: Optional[str] = None) -> str:
    return os.path.join(root or CLAUDE_ROOT, slug, session_id + ".jsonl")


def find_session_path(session_id: str, root: Optional[str] = None) -> Optional[str]:
    """Search all project slugs for <session_id>.jsonl. Returns first match
    or None. Sub-agent transcripts under a `subagents/` subfolder are
    matched too (read-only — they are not directly resumable)."""
    r = root or CLAUDE_ROOT
    if not os.path.isdir(r):
        return None
    target = session_id + ".jsonl"
    for project in os.listdir(r):
        proj_dir = os.path.join(r, project)
        if not os.path.isdir(proj_dir):
            continue
        direct = os.path.join(proj_dir, target)
        if os.path.isfile(direct):
            return direct
        # check subagents/agent-*/<uuid>.jsonl one level deep
        sub_dir = os.path.join(proj_dir, "subagents")
        if os.path.isdir(sub_dir):
            for agent in os.listdir(sub_dir):
                cand = os.path.join(sub_dir, agent, target)
                if os.path.isfile(cand):
                    return cand
    return None


# =====================================================================
# Read
# =====================================================================

_PRESERVED_KEYS = {
    "isSidechain", "userType", "version", "gitBranch", "entrypoint",
    "requestId", "promptId", "agentId", "sourceToolAssistantUUID",
    "isMeta", "level", "subtype", "permissionMode", "attachment",
    "isCompactSummary", "isApiErrorMessage", "toolUseResult",
}


def read_session(
    session_id: str,
    path: Optional[str] = None,
    root: Optional[str] = None,
) -> Conversation:
    """Load a Claude Code session into the canonical IR.

    If `path` is given we use it directly. Otherwise we look up
    `session_id` under root (default `~/.claude/projects`).
    """
    fp = path or find_session_path(session_id, root=root)
    if not fp or not os.path.isfile(fp):
        raise FileNotFoundError(
            f"Claude session not found: id={session_id} path={fp!r}"
        )

    conv = Conversation.new(
        source_agent="claude",
        source_session_id=session_id,
    )
    conv.metadata["jsonl_path"] = fp

    # Track id remapping so canonical event.parent_id chains use canonical
    # UUIDs, not the original Claude UUIDs. We also store the original UUID
    # in event.metadata["claude_uuid"] so the writer can preserve identity
    # for round-trips into the same file.
    claude_to_canonical: Dict[str, str] = {}

    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            ev = _parse_line(d, claude_to_canonical, conv)
            if ev is None:
                continue
            conv.events.append(ev)

    # Pull session-level metadata from the latest line that has cwd/version
    for d in _iter_lines(fp):
        if d.get("cwd"):
            conv.cwd = d["cwd"]
        if d.get("sessionId"):
            conv.metadata.setdefault("claude_session_id", d["sessionId"])
        if d.get("version"):
            conv.metadata.setdefault("claude_version", d["version"])
        if d.get("forkedFrom"):
            conv.metadata.setdefault("forkedFrom", d["forkedFrom"])

    if conv.events:
        conv.created_at = conv.events[0].timestamp or conv.created_at
        conv.updated_at = conv.events[-1].timestamp or conv.updated_at
    return conv


def _iter_lines(fp: str) -> Iterator[Dict[str, Any]]:
    with open(fp, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _parse_line(
    d: Dict[str, Any],
    claude_to_canonical: Dict[str, str],
    conv: Conversation,
) -> Optional[Event]:
    line_type = d.get("type")
    claude_uuid = d.get("uuid")
    canonical_id = claude_to_canonical.get(claude_uuid) if claude_uuid else None
    if canonical_id is None:
        canonical_id = new_uuid()
        if claude_uuid:
            claude_to_canonical[claude_uuid] = canonical_id

    parent_claude_uuid = d.get("parentUuid")
    parent_canonical = (
        claude_to_canonical.get(parent_claude_uuid)
        if parent_claude_uuid
        else None
    )

    timestamp = d.get("timestamp")
    metadata = {
        k: d[k] for k in _PRESERVED_KEYS if k in d
    }
    if claude_uuid:
        metadata["claude_uuid"] = claude_uuid
    if d.get("type"):
        metadata["claude_type"] = d["type"]

    if line_type in ("user", "assistant"):
        msg = d.get("message") or {}
        role = msg.get("role") or line_type
        content = _normalize_content(msg.get("content"))
        # Preserve provider details for assistant
        for k in ("model", "id", "stop_reason", "stop_sequence", "stop_details", "usage"):
            if k in msg:
                metadata.setdefault("claude_message", {})[k] = msg[k]
        return Event(
            id=canonical_id,
            parent_id=parent_canonical,
            type="message",
            role=role,
            content=content,
            timestamp=timestamp,
            metadata=metadata,
        )

    if line_type == "system":
        content = []
        if isinstance(d.get("content"), str):
            content = [{"type": "text", "text": d["content"]}]
        return Event(
            id=canonical_id,
            parent_id=parent_canonical,
            type="system",
            role="system",
            content=content,
            timestamp=timestamp,
            metadata=metadata,
        )

    if line_type == "attachment":
        att = d.get("attachment") or {}
        block_text = json.dumps(att, ensure_ascii=False)
        return Event(
            id=canonical_id,
            parent_id=parent_canonical,
            type="system",
            role="system",
            content=[{"type": "text", "text": block_text}],
            timestamp=timestamp,
            metadata=metadata,
        )

    # permission-mode and any other bookkeeping → meta
    return Event(
        id=canonical_id,
        parent_id=parent_canonical,
        type="meta",
        role=None,
        content=[],
        timestamp=timestamp,
        metadata=metadata,
    )


def _normalize_content(c: Any) -> List[Dict[str, Any]]:
    """Claude user.content can be a plain string OR a list of blocks.
    Canonical is always a list of blocks."""
    if c is None:
        return []
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    if isinstance(c, list):
        out = []
        for b in c:
            if isinstance(b, dict):
                out.append(b)
            elif isinstance(b, str):
                out.append({"type": "text", "text": b})
        return out
    return []


# =====================================================================
# Write
# =====================================================================

def write_session(
    conv: Conversation,
    target_session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    root: Optional[str] = None,
    forked_from: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Materialize a Conversation as a Claude-resumable JSONL file.

    Returns: {"session_id": ..., "path": ..., "cwd": ..., "resume_cmd": ...}
    """
    new_sid = target_session_id or new_uuid()
    use_cwd = cwd or conv.cwd or os.getcwd()
    slug = slug_for_cwd(use_cwd)
    out_root = root or CLAUDE_ROOT
    out_dir = os.path.join(out_root, slug)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, new_sid + ".jsonl")

    # Walk canonical events, allocate fresh Claude UUIDs (since canonical
    # IDs may be from another source), track parent_id remap.
    canonical_to_claude: Dict[str, str] = {}
    prev_claude_uuid: Optional[str] = None
    fork_marker = forked_from or conv.metadata.get("forkedFrom")

    with open(out_path, "w", encoding="utf-8") as f:
        for ev in conv.events:
            for line in _events_to_claude_lines(
                ev,
                canonical_to_claude=canonical_to_claude,
                prev_claude_uuid=prev_claude_uuid,
                session_id=new_sid,
                cwd=use_cwd,
                fork_marker=fork_marker,
            ):
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
                prev_claude_uuid = line.get("uuid", prev_claude_uuid)
                fork_marker = None  # only on the first emitted line

    return {
        "session_id": new_sid,
        "path": out_path,
        "cwd": use_cwd,
        "resume_cmd": ["claude", "--resume", new_sid],
    }


def _events_to_claude_lines(
    ev: Event,
    canonical_to_claude: Dict[str, str],
    prev_claude_uuid: Optional[str],
    session_id: str,
    cwd: str,
    fork_marker: Optional[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """One canonical event → one or more Claude JSONL line dicts.

    Splits a single canonical event whose content mixes tool_use and
    tool_result into the two-message Anthropic shape: tool_use stays with
    the assistant message; tool_result becomes a new user message.
    """
    lines: List[Dict[str, Any]] = []

    if ev.type == "meta":
        # meta events don't survive into Claude JSONL — drop. Their info
        # was already pulled into session-level metadata at read time.
        return lines

    if ev.type == "system":
        claude_uuid = _alloc_claude_uuid(ev.id, canonical_to_claude)
        line = _scaffold_line(
            ev, claude_uuid, prev_claude_uuid, session_id, cwd, line_type="system"
        )
        line["subtype"] = ev.metadata.get("subtype", "info")
        content = ""
        if ev.content:
            first = ev.content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                content = first.get("text", "")
        line["content"] = content
        line["isMeta"] = ev.metadata.get("isMeta", True)
        line["level"] = ev.metadata.get("level", "info")
        if fork_marker:
            line["forkedFrom"] = fork_marker
        lines.append(line)
        return lines

    if ev.type != "message":
        return lines

    role = ev.role or "user"
    blocks = ev.content or []
    tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
    non_results = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "tool_result")]

    # Anthropic convention: tool_result blocks live in user messages, even
    # if the canonical event was tagged "assistant". Split if needed.
    if role == "assistant" and tool_results:
        # 1) assistant line (non-results), 2) user line (results)
        if non_results:
            claude_uuid = _alloc_claude_uuid(ev.id, canonical_to_claude)
            assist_line = _scaffold_assistant_line(
                ev, claude_uuid, prev_claude_uuid, session_id, cwd, non_results
            )
            if fork_marker:
                assist_line["forkedFrom"] = fork_marker
                fork_marker = None
            lines.append(assist_line)
            prev_claude_uuid = claude_uuid
        # user line for tool_result
        user_uuid = new_uuid()  # synthetic — no canonical id maps to it
        user_line = _scaffold_user_line(
            ev, user_uuid, prev_claude_uuid, session_id, cwd, tool_results
        )
        if fork_marker:
            user_line["forkedFrom"] = fork_marker
        lines.append(user_line)
        return lines

    if role == "assistant":
        claude_uuid = _alloc_claude_uuid(ev.id, canonical_to_claude)
        line = _scaffold_assistant_line(
            ev, claude_uuid, prev_claude_uuid, session_id, cwd, non_results or blocks
        )
        if fork_marker:
            line["forkedFrom"] = fork_marker
        lines.append(line)
        return lines

    # user / system role / tool role → emit as user line
    claude_uuid = _alloc_claude_uuid(ev.id, canonical_to_claude)
    line = _scaffold_user_line(
        ev, claude_uuid, prev_claude_uuid, session_id, cwd, blocks
    )
    if fork_marker:
        line["forkedFrom"] = fork_marker
    lines.append(line)
    return lines


def _alloc_claude_uuid(canonical_id: str, m: Dict[str, str]) -> str:
    if canonical_id not in m:
        m[canonical_id] = new_uuid()
    return m[canonical_id]


def _scaffold_line(
    ev: Event,
    uuid: str,
    parent_uuid: Optional[str],
    session_id: str,
    cwd: str,
    line_type: str,
) -> Dict[str, Any]:
    line: Dict[str, Any] = {
        "parentUuid": parent_uuid,
        "type": line_type,
        "uuid": uuid,
        "timestamp": ev.timestamp or _now_iso(),
        "sessionId": session_id,
        "cwd": cwd,
        "userType": ev.metadata.get("userType", "external"),
        "version": ev.metadata.get("version", "2.1.0"),
    }
    if ev.metadata.get("isSidechain"):
        line["isSidechain"] = True
    if ev.metadata.get("gitBranch"):
        line["gitBranch"] = ev.metadata["gitBranch"]
    return line


def _scaffold_user_line(
    ev: Event,
    uuid: str,
    parent_uuid: Optional[str],
    session_id: str,
    cwd: str,
    blocks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    line = _scaffold_line(ev, uuid, parent_uuid, session_id, cwd, "user")
    # If the canonical event was a plain single-text-block user message,
    # collapse to the legacy string content shape Claude commonly writes.
    if (
        len(blocks) == 1
        and isinstance(blocks[0], dict)
        and blocks[0].get("type") == "text"
        and not any(b.get("type") == "tool_result" for b in blocks if isinstance(b, dict))
    ):
        line["message"] = {"role": "user", "content": blocks[0].get("text", "")}
    else:
        line["message"] = {"role": "user", "content": blocks}
    return line


def _scaffold_assistant_line(
    ev: Event,
    uuid: str,
    parent_uuid: Optional[str],
    session_id: str,
    cwd: str,
    blocks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    line = _scaffold_line(ev, uuid, parent_uuid, session_id, cwd, "assistant")
    claude_msg = (ev.metadata.get("claude_message") or {}).copy()
    msg = {
        "role": "assistant",
        "content": blocks,
        "type": "message",
    }
    msg.update(claude_msg)  # restore model, usage, etc. when present
    msg["role"] = "assistant"
    msg["content"] = blocks
    line["message"] = msg
    return line


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# =====================================================================
# List
# =====================================================================

def list_sessions(root: Optional[str] = None) -> List[Dict[str, Any]]:
    r = root or CLAUDE_ROOT
    if not os.path.isdir(r):
        return []
    out = []
    for project in os.listdir(r):
        pd = os.path.join(r, project)
        if not os.path.isdir(pd):
            continue
        for name in os.listdir(pd):
            if not name.endswith(".jsonl"):
                continue
            sid = name[:-len(".jsonl")]
            fp = os.path.join(pd, name)
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                mtime = 0
            out.append({
                "id": sid,
                "path": fp,
                "project_slug": project,
                "mtime": mtime,
                "source": "claude",
            })
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out
