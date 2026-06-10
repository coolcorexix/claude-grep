"""Hermes adapter — read `~/.hermes/sessions/<ts>_<hash>.jsonl` (and the
single-JSON variant used for older / shared sessions).

Hermes uses an OpenAI-style chat-completions transcript with extensions:

    line 1  role=session_meta  {model, platform, tools[]}
    line n  role=user          {content:str, timestamp, message_id}
    line n  role=assistant     {content:str (may be ""),
                                reasoning, reasoning_content,
                                finish_reason,
                                tool_calls?: [{id, type:"function",
                                               function:{name, arguments}}]}
    line n  role=tool          {content:str, name, tool_call_id, timestamp}

Mapping to canonical (Anthropic-style blocks):
    session_meta line          → conv.metadata["hermes_*"], NOT an event
    user message               → message event(role=user) with [text block]
    assistant.reasoning        → thinking block
    assistant.content          → text block (skipped if empty + tool_calls)
    assistant.tool_calls[i]    → tool_use block (input = json.loads(arguments))
    tool message               → message event(role=user, Anthropic convention)
                                 with [tool_result block referencing tool_call_id]

Write direction deferred — Hermes carries model+tools at session start that
we'd need to synthesize cleanly, and the resume command needs the Hermes
runtime to be present. Reader is enough to power the Ctrl-X port flow.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..canonical import Conversation, Event, new_uuid

HOME = os.path.expanduser("~")
HERMES_ROOT = os.path.join(HOME, ".hermes")


# =====================================================================
# Discovery
# =====================================================================

def _session_dirs() -> List[str]:
    dirs = []
    top = os.path.join(HERMES_ROOT, "sessions")
    if os.path.isdir(top):
        dirs.append(top)
    profiles_root = os.path.join(HERMES_ROOT, "profiles")
    if os.path.isdir(profiles_root):
        for name in os.listdir(profiles_root):
            cand = os.path.join(profiles_root, name, "sessions")
            if os.path.isdir(cand):
                dirs.append(cand)
    return dirs


def _sid_from_path(path: str) -> str:
    base = os.path.basename(path)
    for ext in (".jsonl", ".json"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    if base.startswith("session_"):
        base = base[len("session_"):]
    return base


def _is_real_session_file(name: str) -> bool:
    if not name.endswith((".jsonl", ".json")):
        return False
    if name.startswith("request_dump_"):  # raw API dumps
        return False
    if name == "sessions.json":  # the session-index, not a session
        return False
    return True


def find_session_path(session_id: str) -> Optional[str]:
    """Resolve a session_id to its file path. Prefers `.jsonl` (live
    append log) over the paired single-JSON snapshot."""
    candidates: List[str] = []
    for d in _session_dirs():
        for name in os.listdir(d):
            if not _is_real_session_file(name):
                continue
            fp = os.path.join(d, name)
            if _sid_from_path(fp) == session_id:
                candidates.append(fp)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (0 if p.endswith(".jsonl") else 1, p))
    return candidates[0]


# =====================================================================
# Read
# =====================================================================

def read_session(
    session_id: str,
    path: Optional[str] = None,
) -> Conversation:
    fp = path or find_session_path(session_id)
    if not fp or not os.path.isfile(fp):
        raise FileNotFoundError(
            f"Hermes session not found: id={session_id!r} path={fp!r}"
        )

    conv = Conversation.new(
        source_agent="hermes",
        source_session_id=session_id,
    )
    conv.metadata["hermes_path"] = fp

    # Hermes doesn't record cwd. Pick the profile name as a soft hint so
    # writers / callers can surface it.
    if "/profiles/" in fp:
        try:
            profile = fp.split("/profiles/")[1].split("/")[0]
            conv.metadata["hermes_profile"] = profile
        except Exception:
            pass

    if fp.endswith(".jsonl"):
        _ingest_jsonl(conv, fp)
    elif fp.endswith(".json"):
        _ingest_single_json(conv, fp)
    else:
        raise ValueError(f"unknown Hermes session format: {fp}")

    if conv.events:
        conv.created_at = conv.events[0].timestamp or conv.created_at
        conv.updated_at = conv.events[-1].timestamp or conv.updated_at
    return conv


def _ingest_jsonl(conv: Conversation, fp: str) -> None:
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            _ingest_line(conv, d)


def _ingest_single_json(conv: Conversation, fp: str) -> None:
    """`.json` sessions store the whole transcript as one object with a
    `messages: [...]` array plus session-level fields."""
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            d = json.load(f)
    except Exception:
        return
    if not isinstance(d, dict):
        return

    for k in ("title", "session_id", "session_start", "last_updated",
              "model", "platform"):
        if k in d and d[k] is not None:
            conv.metadata.setdefault(f"hermes_{k}", d[k])

    if isinstance(d.get("title"), str) and not conv.title:
        conv.title = d["title"]

    for msg in d.get("messages") or []:
        if isinstance(msg, dict):
            _ingest_line(conv, msg)


def _ingest_line(conv: Conversation, d: Dict[str, Any]) -> None:
    role = d.get("role")
    ts = d.get("timestamp")

    if role == "session_meta":
        # Stash in conv.metadata; not an event.
        for k in ("model", "platform", "tools"):
            if k in d and d[k] is not None:
                conv.metadata.setdefault(f"hermes_{k}", d[k])
        return

    if role == "user":
        content = d.get("content")
        blocks: List[Dict[str, Any]] = []
        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    blocks.append({"type": "text", "text": b["text"]})
                elif isinstance(b, str):
                    blocks.append({"type": "text", "text": b})
        conv.append_event(
            type="message",
            role="user",
            content=blocks,
            timestamp=ts,
            metadata={"hermes_message_id": d.get("message_id")},
        )
        return

    if role == "assistant":
        blocks: List[Dict[str, Any]] = []
        reasoning = d.get("reasoning") or d.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            blocks.append({"type": "thinking", "thinking": reasoning})
        content = d.get("content")
        if isinstance(content, str) and content.strip():
            blocks.append({"type": "text", "text": content})
        for tc in d.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tu = _translate_tool_call(tc)
            if tu:
                blocks.append(tu)
        meta = {
            "hermes_finish_reason": d.get("finish_reason"),
        }
        # Preserve raw tool_calls for round-trip if ever needed.
        if d.get("tool_calls"):
            meta["hermes_tool_calls_raw"] = d["tool_calls"]
        conv.append_event(
            type="message",
            role="assistant",
            content=blocks,
            timestamp=ts,
            metadata=meta,
        )
        return

    if role == "tool":
        # Anthropic convention: tool_result blocks live in user messages.
        call_id = d.get("tool_call_id") or new_uuid()
        content_str = d.get("content")
        if not isinstance(content_str, str):
            content_str = json.dumps(content_str, ensure_ascii=False)
        block = {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": content_str,
        }
        conv.append_event(
            type="message",
            role="user",
            content=[block],
            timestamp=ts,
            metadata={
                "hermes_tool_name": d.get("name"),
                "synthesized": "tool_result_from_role_tool",
            },
        )
        return

    # Unknown role → meta event so we don't break the chain.
    conv.append_event(
        type="meta",
        role=None,
        content=[],
        timestamp=ts,
        metadata={"hermes_role": role, "hermes_raw_keys": sorted(d.keys())},
    )


def _translate_tool_call(tc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """OpenAI-style tool_call → canonical tool_use block."""
    fn = tc.get("function") or {}
    name = fn.get("name") or tc.get("name") or "unknown"
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            input_ = json.loads(args)
            if not isinstance(input_, dict):
                input_ = {"_raw": args}
        except json.JSONDecodeError:
            input_ = {"_raw": args}
    elif isinstance(args, dict):
        input_ = args
    else:
        input_ = {}
    call_id = tc.get("id") or tc.get("call_id") or new_uuid()
    return {
        "type": "tool_use",
        "id": call_id,
        "name": name,
        "input": input_,
    }


# =====================================================================
# List
# =====================================================================

def list_sessions() -> List[Dict[str, Any]]:
    """Return one row per session, preferring the `.jsonl` file over the
    paired `session_<sid>.json` snapshot for the same sid."""
    by_sid: Dict[str, Dict[str, Any]] = {}
    for d in _session_dirs():
        for name in os.listdir(d):
            if not _is_real_session_file(name):
                continue
            fp = os.path.join(d, name)
            sid = _sid_from_path(fp)
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                mtime = 0
            row = {
                "id": sid,
                "path": fp,
                "mtime": mtime,
                "source": "hermes",
            }
            if "/profiles/" in fp:
                try:
                    row["profile"] = fp.split("/profiles/")[1].split("/")[0]
                except Exception:
                    pass
            prev = by_sid.get(sid)
            if prev is None:
                by_sid[sid] = row
                continue
            # prefer .jsonl over .json for the same sid
            if fp.endswith(".jsonl") and not prev["path"].endswith(".jsonl"):
                by_sid[sid] = row
    out = list(by_sid.values())
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


# Write deferred — see module docstring.
