"""OpenCode adapter — read `~/.local/share/opencode/opencode.db`.

OpenCode storage is a SQLite DB with a 4-level schema:
    project (1) → session (1) → message (n) → part (n)

Each `message.data` is JSON: role, mode, agent, modelID, providerID, cost,
tokens, time. Each `part.data` is JSON with type ∈
    {text, tool, reasoning, step-start, step-finish, patch, file}.

Important mapping detail — tool calls:
    OpenCode collapses tool_use + tool_result into one `tool` part:
        {type:"tool", tool:"<name>", callID:"<id>", state:{
            status:"completed", input:{...}, output:"<str>", metadata:{...}
        }}
    Claude splits these into a tool_use block (assistant message) and a
    tool_result block (next user message). Canonical follows Anthropic:
    one OC tool part → one tool_use block AND one tool_result block.
    The reader places the tool_use under the assistant message that owned
    the OC `part`, and synthesizes an adjacent canonical user-role event
    holding the tool_result so it round-trips into Claude cleanly.

Write direction is deferred — needs to synthesize the OC-specific
per-session economics fields (modelID, providerID, cost, tokens_*) which
Claude transcripts don't carry. Stub raises NotImplementedError so the
registry can advertise read-only capability cleanly.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ..canonical import (
    Conversation,
    Event,
    new_uuid,
    iso_from_epoch_ms,
)

HOME = os.path.expanduser("~")
OPENCODE_DB = os.path.join(HOME, ".local", "share", "opencode", "opencode.db")


# =====================================================================
# Read
# =====================================================================

def read_session(
    session_id: str,
    db_path: Optional[str] = None,
) -> Conversation:
    fp = db_path or OPENCODE_DB
    if not os.path.isfile(fp):
        raise FileNotFoundError(f"OpenCode DB not found: {fp!r}")

    conn = sqlite3.connect(f"file:{fp}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        sess_row = conn.execute(
            "SELECT * FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        if sess_row is None:
            raise KeyError(f"OpenCode session not found: {session_id!r}")

        sess = dict(sess_row)
        conv = Conversation.new(
            source_agent="opencode",
            source_session_id=session_id,
            cwd=sess.get("directory"),
            title=sess.get("title"),
        )
        conv.created_at = iso_from_epoch_ms(sess.get("time_created"))
        conv.updated_at = iso_from_epoch_ms(sess.get("time_updated"))
        # Session-level metadata
        for k in (
            "project_id", "parent_id", "slug", "version", "share_url",
            "agent", "model", "cost", "workspace_id", "path",
            "tokens_input", "tokens_output", "tokens_reasoning",
            "tokens_cache_read", "tokens_cache_write",
        ):
            if k in sess and sess[k] is not None:
                conv.metadata[f"opencode_{k}"] = sess[k]

        # Pull messages in time order; for each message, pull its parts in
        # time order and translate.
        msg_rows = conn.execute(
            "SELECT id, data, time_created, time_updated "
            "FROM message WHERE session_id = ? ORDER BY time_created",
            (session_id,),
        ).fetchall()

        for mrow in msg_rows:
            parts = conn.execute(
                "SELECT id, data, time_created FROM part "
                "WHERE message_id = ? ORDER BY time_created",
                (mrow["id"],),
            ).fetchall()
            _ingest_message(conv, dict(mrow), [dict(p) for p in parts])
    finally:
        conn.close()

    return conv


def _ingest_message(
    conv: Conversation,
    mrow: Dict[str, Any],
    parts: List[Dict[str, Any]],
) -> None:
    """Translate one OC message + its parts into one or more canonical
    events, respecting the tool_use/tool_result split convention."""
    try:
        mdata = json.loads(mrow["data"])
    except Exception:
        mdata = {}
    role = mdata.get("role") or "assistant"
    ts = iso_from_epoch_ms(mrow.get("time_created"))

    msg_meta = {
        "opencode_message_id": mrow["id"],
        "opencode_role": role,
    }
    for k in ("mode", "agent", "modelID", "providerID", "variant",
              "cost", "tokens", "time", "finish"):
        if k in mdata and mdata[k] is not None:
            msg_meta[f"opencode_{k}"] = mdata[k]

    # Collect content blocks from non-tool parts; tool parts emit their
    # own pair of (tool_use → into THIS message, tool_result → into a
    # synthesized adjacent user event).
    main_blocks: List[Dict[str, Any]] = []
    tool_results_to_emit: List[Tuple[str, Dict[str, Any]]] = []
    # list of (tool_use_id, tool_result_block) — emitted as one user event
    # after the assistant event below.

    for prow in parts:
        try:
            pdata = json.loads(prow["data"])
        except Exception:
            continue
        ptype = pdata.get("type")
        if ptype == "text":
            txt = pdata.get("text")
            if isinstance(txt, str) and txt:
                main_blocks.append({"type": "text", "text": txt})
        elif ptype == "reasoning":
            txt = pdata.get("text")
            if isinstance(txt, str) and txt:
                main_blocks.append({"type": "thinking", "thinking": txt})
        elif ptype == "tool":
            tu, tr = _translate_tool_part(pdata)
            if tu:
                main_blocks.append(tu)
            if tr:
                tool_results_to_emit.append((tu.get("id") if tu else "", tr))
        elif ptype == "file":
            mime = pdata.get("mime") or "application/octet-stream"
            url = pdata.get("url") or ""
            filename = pdata.get("filename")
            if url.startswith("data:") and ";base64," in url:
                media_type, b64 = _parse_data_url(url)
                if media_type and media_type.startswith("image/"):
                    main_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    })
                else:
                    main_blocks.append({
                        "type": "attachment",
                        "path": filename or "",
                        "mime_type": media_type or mime,
                    })
            else:
                main_blocks.append({
                    "type": "attachment",
                    "path": filename or url,
                    "mime_type": mime,
                })
        elif ptype in ("step-start", "step-finish"):
            # bookkeeping — emit a meta event after the message so order is
            # preserved but contents stay clean. We collect and emit below.
            pass
        elif ptype == "patch":
            # Render as text so it survives. Keep raw in metadata if needed.
            patch = pdata.get("hash") or pdata.get("files") or pdata
            main_blocks.append({
                "type": "text",
                "text": f"[opencode patch part]\n{json.dumps(patch, ensure_ascii=False)[:2000]}",
            })

    # Emit the main message event (only if it has content or is the only
    # carrier of metadata for an empty message).
    if main_blocks or role == "user":
        conv.append_event(
            type="message",
            role=role,
            content=main_blocks,
            timestamp=ts,
            metadata=msg_meta,
        )

    # Then emit the tool_result-bearing user event (Anthropic convention),
    # if any tool parts produced results.
    if tool_results_to_emit:
        results_blocks = [tr for (_id, tr) in tool_results_to_emit]
        conv.append_event(
            type="message",
            role="user",
            content=results_blocks,
            timestamp=ts,
            metadata={
                "opencode_message_id": mrow["id"],
                "synthesized": "tool_result_split",
            },
        )


def _translate_tool_part(
    pdata: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return (tool_use_block, tool_result_block) from an OC tool part."""
    tool_name = pdata.get("tool") or "unknown"
    call_id = pdata.get("callID") or new_uuid()
    state = pdata.get("state") or {}
    input_ = state.get("input") or {}
    output = state.get("output")
    status = state.get("status")

    tool_use = {
        "type": "tool_use",
        "id": call_id,
        "name": tool_name,
        "input": input_ if isinstance(input_, dict) else {"_raw": input_},
    }

    if status not in ("completed", "error", "success"):
        return tool_use, None

    is_error = (status == "error")
    if isinstance(output, str):
        tr_content: Any = output
    elif isinstance(output, list):
        tr_content = output
    elif isinstance(output, dict):
        tr_content = json.dumps(output, ensure_ascii=False)
    elif output is None:
        tr_content = ""
    else:
        tr_content = str(output)

    tool_result = {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": tr_content,
    }
    if is_error:
        tool_result["is_error"] = True
    return tool_use, tool_result


def _parse_data_url(url: str) -> Tuple[Optional[str], str]:
    # data:image/png;base64,XXXX
    try:
        head, b64 = url.split(",", 1)
        # head = "data:image/png;base64"
        media = head.split(";")[0].split(":", 1)[1]
        return media, b64
    except Exception:
        return None, ""


# =====================================================================
# Write — deferred
# =====================================================================

def write_session(
    conv: Conversation,
    target_session_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    raise NotImplementedError(
        "OpenCode write adapter is not implemented yet. Writing requires "
        "synthesizing per-session economics fields (modelID, providerID, "
        "cost, tokens_*) that Claude transcripts do not carry. Tracking "
        "in the project roadmap as 'OC writer'."
    )


# =====================================================================
# List
# =====================================================================

def list_sessions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    fp = db_path or OPENCODE_DB
    if not os.path.isfile(fp):
        return []
    conn = sqlite3.connect(f"file:{fp}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, title, directory, time_updated FROM session "
            "ORDER BY time_updated DESC"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "cwd": r["directory"],
            "updated_at": iso_from_epoch_ms(r["time_updated"]),
            "source": "opencode",
        }
        for r in rows
    ]
