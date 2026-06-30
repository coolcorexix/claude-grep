"""OpenCode adapter — read/write `~/.local/share/opencode/opencode.db`.

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

    READ:  places tool_use in the assistant event and synthesizes an adjacent
           user event for tool_result (so it round-trips into Claude cleanly).
    WRITE: reverses the split — collects all tool_results from user events,
           then embeds them back into the matching tool_use part under the
           assistant message, skipping the synthesized user events.
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
    epoch_ms_from_iso,
)

HOME = os.path.expanduser("~")
OPENCODE_DB = os.path.join(HOME, ".local", "share", "opencode", "opencode.db")

# Claude Code and OpenCode name the same built-in tools differently
# (TitleCase vs lowercase). Map so resumed history renders against
# OpenCode's real tool registry instead of falling back to "invalid".
_CLAUDE_TO_OC_TOOL = {
    "Bash": "bash",
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Glob": "glob",
    "Grep": "grep",
    "WebFetch": "webfetch",
    "Task": "task",
    "TodoWrite": "todowrite",
    "AskUserQuestion": "question",
    "Skill": "skill",
}


def _map_tool_name(name: str) -> str:
    """Translate a Claude tool name to its OpenCode equivalent. Unknown
    names (incl. mcp__* tools) pass through unchanged — OpenCode renders
    them via its built-in 'invalid' fallback without crashing."""
    if name in _CLAUDE_TO_OC_TOOL:
        return _CLAUDE_TO_OC_TOOL[name]
    return name

# ---------------------------------------------------------------------
# OpenCode Identifier — faithful replica of OpenCode's id generator.
#
# OpenCode validates every id it loads against a schema that requires the
# correct prefix (e.g. message ids must start with "msg", part ids with
# "prt"). A non-conforming id makes the prompt loop throw at step=0
# ("Expected a string starting with \"msg\"") *before* any model call —
# which is why a ported session's LLM never responds. We must mint ids in
# OpenCode's exact shape: <prefix>_<12 hex of (ts*4096+counter, low 48
# bits, big-endian)><14 random base62 chars>, ascending.
# ---------------------------------------------------------------------
_OC_PREFIXES = {
    "session": "ses",
    "message": "msg",
    "part": "prt",
    "user": "usr",
    "permission": "per",
}
_OC_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_OC_ID_LENGTH = 26  # total chars after the "<prefix>_": 12 hex + 14 base62
# Monotonic counter state, matching OpenCode: reset whenever the ms changes.
_oc_id_state = {"last_ts": 0, "counter": 0}


def _oc_random_base62(n: int) -> str:
    return "".join(_OC_BASE62[b % 62] for b in os.urandom(n))


def _oc_id(kind: str, ts_ms: Optional[int] = None) -> str:
    """Generate an OpenCode-schema-valid id (ascending) for `kind`."""
    prefix = _OC_PREFIXES[kind]
    ts = ts_ms if ts_ms is not None else _now_epoch_ms()
    if ts != _oc_id_state["last_ts"]:
        _oc_id_state["last_ts"] = ts
        _oc_id_state["counter"] = 0
    _oc_id_state["counter"] += 1
    k = (ts * 4096 + _oc_id_state["counter"]) & ((1 << 48) - 1)
    hex12 = k.to_bytes(6, "big").hex()
    return f"{prefix}_{hex12}{_oc_random_base62(_OC_ID_LENGTH - 12)}"


# OpenCode requires session IDs starting with "ses_".
def _new_oc_id(prefix: str = "ses") -> str:
    kind = {"ses": "session", "msg": "message", "prt": "part"}.get(prefix)
    if kind:
        return _oc_id(kind)
    return f"{prefix}_{new_uuid().replace('-', '')}"


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
# Write
# =====================================================================

def write_session(
    conv: Conversation,
    target_session_id: Optional[str] = None,
    db_path: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Materialize a canonical Conversation as an OpenCode-resumable session.

    Reverse-maps the tool_use/tool_result split: collects tool_results from
    user events and embeds them back into the matching tool_use parts under
    assistant messages. Synthesized user events (tool_results only) are
    skipped.

    Returns: {"session_id": ..., "path": ..., "cwd": ..., "resume_cmd": ...}
    """
    fp = db_path or OPENCODE_DB
    use_cwd = cwd or conv.cwd or os.getcwd()
    new_sid = target_session_id or _new_oc_id("ses")

    if not os.path.exists(os.path.dirname(fp)):
        os.makedirs(os.path.dirname(fp), exist_ok=True)

    conn = sqlite3.connect(f"file:{fp}", uri=True)
    try:
        _ensure_schema(conn)
        created_ms = epoch_ms_from_iso(conv.created_at) or _now_epoch_ms()
        updated_ms = epoch_ms_from_iso(conv.updated_at) or _now_epoch_ms()
        title = conv.title or ""

        # Detect the user's actual OpenCode model config from an existing
        # session, so the ported session uses a real provider/model that
        # OpenCode knows how to call (not synthetic "ported" strings).
        model_config = _detect_model_config(conn)

        # Collect source provenance.
        fork = conv.metadata.get("forkedFrom")
        session_meta: Dict[str, Any] = {}
        if fork:
            session_meta["forkedFrom"] = fork

        # Match actual OpenCode session schema (discovered at runtime).
        project_id = _find_or_create_project(conn, use_cwd)
        slug = _make_slug(use_cwd)

        agent_name = model_config["agent"]
        session_model = model_config["session_model"]
        msg_model_id = model_config["model_id"]
        msg_provider_id = model_config["provider_id"]
        msg_variant = model_config["variant"]

        conn.execute(
            "INSERT INTO session ("
            "id, project_id, parent_id, slug, directory, title, version, "
            "time_created, time_updated, agent, model, cost, workspace_id, "
            "path, summary_additions, summary_deletions, summary_files, "
            "tokens_input, tokens_output, tokens_reasoning, "
            "tokens_cache_read, tokens_cache_write, metadata"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_sid,
                project_id,
                None,
                slug,
                use_cwd,
                title,
                "ported-1.0.0",
                created_ms,
                updated_ms,
                agent_name,
                session_model,
                0,
                None,
                None,
                0, 0, 0,
                0, 0, 0, 0, 0,
                json.dumps(session_meta) if session_meta else None,
            ),
        )

        # ---- Phase 1: collect all tool_results by tool_use_id ----
        tr_map: Dict[str, Tuple[Any, bool]] = {}
        for ev in conv.events:
            if ev.type != "message":
                continue
            for block in ev.content or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tuid = block.get("tool_use_id")
                    if tuid:
                        tr_map[tuid] = (
                            block.get("content"),
                            block.get("is_error", False),
                        )

        # ---- Phase 2: write messages and parts ----
        prev_msg_id: Optional[str] = None
        # Mutable counter so each part gets a strictly-increasing timestamp,
        # ensuring step-start → content → step-finish ordering.
        ts_counter = [created_ms]
        for ev in conv.events:
            if ev.type != "message":
                continue

            blocks = ev.content or []
            role = ev.role or "user"

            main_blocks = [
                b for b in blocks
                if not (isinstance(b, dict) and b.get("type") == "tool_result")
            ]

            # Synthesized user events (created by reader to hold
            # tool_results) carry *only* tool_result blocks — skip.
            if role == "user" and not main_blocks:
                continue

            ts = epoch_ms_from_iso(ev.timestamp) or _now_epoch_ms()
            msg_id = _oc_id("message", ts)

            if role == "user":
                msg_data: Dict[str, Any] = {
                    "role": "user",
                    "agent": agent_name,
                    "model": {"providerID": msg_provider_id,
                              "modelID": msg_model_id,
                              "variant": msg_variant},
                    "time": {"created": ts},
                }
            else:
                has_tools = any(
                    isinstance(b, dict) and b.get("type") == "tool_use"
                    for b in main_blocks
                )
                msg_data: Dict[str, Any] = {
                    "role": "assistant",
                    "agent": agent_name,
                    "mode": "build",
                    "variant": msg_variant,
                    "modelID": msg_model_id,
                    "providerID": msg_provider_id,
                    "cost": 0,
                    "tokens": {"input": 0, "output": 0, "reasoning": 0,
                               "cache": {"write": 0, "read": 0}},
                    "time": {"created": ts},
                    "path": {"cwd": use_cwd, "root": use_cwd},
                }
                if has_tools:
                    msg_data["finish"] = "tool-calls"
                    msg_data["time"]["completed"] = ts
                    msg_data["tokens"]["total"] = 0
                else:
                    # Completed text-only responses use finish: "stop"
                    # so OpenCode knows the turn is done and accepts
                    # new user input (otherwise it shows "QUEUE").
                    msg_data["finish"] = "stop"
                    msg_data["time"]["completed"] = ts
                    msg_data["tokens"]["total"] = 0
            # Pull opencode-specific per-message metadata if carried over.
            for k in ("mode", "agent", "modelID", "providerID",
                       "cost", "tokens", "time"):
                mk = f"opencode_{k}"
                if mk in ev.metadata:
                    msg_data[k] = ev.metadata[mk]

            if prev_msg_id:
                msg_data["parentID"] = prev_msg_id

            conn.execute(
                "INSERT INTO message (id, session_id, time_created,"
                " time_updated, data) VALUES (?,?,?,?,?)",
                (msg_id, new_sid, ts, ts,
                 json.dumps(msg_data, ensure_ascii=False)),
            )
            prev_msg_id = msg_id

            if role == "assistant" and main_blocks:
                _insert_part_row(conn, msg_id, new_sid, {
                    "type": "step-start",
                    "snapshot": "",
                }, _inc_ts(ts_counter))

            for block in main_blocks:
                _write_part(conn, msg_id, new_sid, block, tr_map,
                            _inc_ts(ts_counter))

            if role == "assistant" and main_blocks:
                _insert_part_row(conn, msg_id, new_sid, {
                    "type": "step-finish",
                    "reason": "tool-calls" if has_tools else "stop",
                    "snapshot": "",
                    "tokens": {"total": 0, "input": 0, "output": 0,
                               "reasoning": 0,
                               "cache": {"write": 0, "read": 0}},
                    "cost": 0,
                }, _inc_ts(ts_counter))

        # Orphaned tool_results → synthetic user message.
        if tr_map:
            ts = _now_epoch_ms()
            orphan_msg_id = _oc_id("message", ts)
            orphan_data = {
                "role": "user",
                "agent": agent_name,
                "model": {"providerID": msg_provider_id,
                          "modelID": msg_model_id,
                          "variant": msg_variant},
                "time": {"created": ts},
            }
            if prev_msg_id:
                orphan_data["parentID"] = prev_msg_id
            conn.execute(
                "INSERT INTO message (id, session_id, time_created,"
                " time_updated, data) VALUES (?,?,?,?,?)",
                (orphan_msg_id, new_sid, ts, ts,
                 json.dumps(orphan_data, ensure_ascii=False)),
            )
            for _tuid, (content, is_error) in tr_map.items():
                text = json.dumps(content, ensure_ascii=False) if not isinstance(
                    content, (str, type(None))
                ) else str(content or "")
                prefix = "[tool_error] " if is_error else "[tool_result] "
                _insert_part_row(
                    conn, orphan_msg_id, new_sid,
                    {"type": "text", "text": prefix + text}, ts,
                )

        conn.commit()
    finally:
        conn.close()

    return {
        "session_id": new_sid,
        "path": fp,
        "cwd": use_cwd,
        "resume_cmd": ["opencode", "--session", new_sid],
    }


# =====================================================================
# Write helpers
# =====================================================================

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """CREATE IF NOT EXISTS matching the real OpenCode DB schema."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project ("
        "id TEXT PRIMARY KEY, worktree TEXT NOT NULL, vcs TEXT, name TEXT, "
        "icon_url TEXT, icon_color TEXT, time_created INTEGER NOT NULL, "
        "time_updated INTEGER NOT NULL, time_initialized INTEGER, "
        "sandboxes TEXT NOT NULL DEFAULT '[]', commands TEXT, "
        "icon_url_override TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session ("
        "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, parent_id TEXT, "
        "slug TEXT NOT NULL, directory TEXT NOT NULL, title TEXT NOT NULL, "
        "version TEXT NOT NULL, share_url TEXT, summary_additions INTEGER, "
        "summary_deletions INTEGER, summary_files INTEGER, summary_diffs TEXT, "
        "revert TEXT, permission TEXT, time_created INTEGER NOT NULL, "
        "time_updated INTEGER NOT NULL, time_compacting INTEGER, "
        "time_archived INTEGER, workspace_id TEXT, path TEXT, agent TEXT, "
        "model TEXT, cost REAL NOT NULL DEFAULT 0, "
        "tokens_input INTEGER NOT NULL DEFAULT 0, "
        "tokens_output INTEGER NOT NULL DEFAULT 0, "
        "tokens_reasoning INTEGER NOT NULL DEFAULT 0, "
        "tokens_cache_read INTEGER NOT NULL DEFAULT 0, "
        "tokens_cache_write INTEGER NOT NULL DEFAULT 0, "
        "metadata TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS message ("
        "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, "
        "data TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS part ("
        "id TEXT PRIMARY KEY, message_id TEXT NOT NULL, "
        "session_id TEXT NOT NULL, time_created INTEGER NOT NULL, "
        "time_updated INTEGER NOT NULL, data TEXT NOT NULL)"
    )


def _detect_model_config(conn: sqlite3.Connection) -> Dict[str, str]:
    """Read the user's actual model config from their latest OpenCode
    session so ported sessions use a real provider/model."""
    cursor = conn.execute(
        "SELECT agent, model FROM session WHERE agent IS NOT NULL "
        "AND model IS NOT NULL AND model != '' "
        "ORDER BY time_updated DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if row and row[0] and row[1]:
        agent = row[0]
        try:
            sm = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            sm = {}
        return {
            "agent": agent,
            "session_model": row[1],
            "model_id": sm.get("id", sm.get("modelID", "")),
            "provider_id": sm.get("providerID", ""),
            "variant": sm.get("variant", "high"),
        }
    # Ultimate fallback — unlikely to work for LLM calls but won't crash
    return {
        "agent": "build",
        "session_model": json.dumps({"id": "", "providerID": "", "variant": ""}),
        "model_id": "",
        "provider_id": "",
        "variant": "high",
    }


def _make_slug(directory: str) -> str:
    """Generate a human-readable slug from the directory basename."""
    import random
    adjectives = [
        "eager", "brave", "calm", "kind", "bold", "sharp", "bright",
        "cool", "warm", "swift", "keen", "wild", "quiet", "proud",
    ]
    nouns = [
        "mountain", "river", "forest", "cloud", "star", "wave", "meadow",
        "harbor", "valley", "peak", "storm", "field", "canyon", "lake",
    ]
    base = os.path.basename(directory)
    if base:
        # Use a deterministic-ish noun from the directory name
        idx = sum(ord(c) for c in base) % len(nouns)
        return f"{random.choice(adjectives)}-{nouns[idx]}"
    return f"{random.choice(adjectives)}-{random.choice(nouns)}"


def _find_or_create_project(
    conn: sqlite3.Connection, directory: str
) -> str:
    import hashlib
    # OpenCode uses sha1(worktree) as the project id.
    pid = hashlib.sha1(directory.encode()).hexdigest()
    cursor = conn.execute(
        "SELECT id FROM project WHERE worktree = ?", (directory,)
    )
    row = cursor.fetchone()
    if row:
        return row[0]
    now = _now_epoch_ms()
    name = os.path.basename(directory) or directory
    try:
        conn.execute(
            "INSERT INTO project (id, worktree, vcs, name, time_created,"
            " time_updated, sandboxes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, directory, "git", name, now, now, "[]"),
        )
    except sqlite3.OperationalError:
        conn.execute(
            "INSERT INTO project (id, worktree, vcs, time_created,"
            " time_updated, sandboxes) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, directory, "git", now, now, "[]"),
        )
    return pid


def _write_part(
    conn: sqlite3.Connection,
    msg_id: str,
    session_id: str,
    block: Dict[str, Any],
    tr_map: Dict[str, Tuple[Any, bool]],
    ts: int,
) -> None:
    if not isinstance(block, dict):
        return
    btype = block.get("type")

    if btype == "text":
        _insert_part_row(conn, msg_id, session_id, {
            "type": "text",
            "text": block.get("text", ""),
            "time": {"start": ts, "end": ts},
        }, ts)
    elif btype == "thinking":
        _insert_part_row(conn, msg_id, session_id, {
            "type": "reasoning",
            "text": block.get("thinking", ""),
            "time": {"start": ts, "end": ts},
        }, ts)
    elif btype == "tool_use":
        tu_id = block.get("id", "")
        tu_name = _map_tool_name(block.get("name", "unknown"))
        tu_input = block.get("input", {})
        safe_input = tu_input if isinstance(tu_input, dict) else {"_raw": tu_input}
        tr_info = tr_map.pop(tu_id, None)
        if tr_info:
            tr_content, is_error = tr_info
            # OpenCode requires state.output to be a string and reads
            # state.time.compacted — omitting state.time throws a
            # TypeError that aborts the whole prompt loop on resume.
            state: Dict[str, Any] = {
                "status": "error" if is_error else "completed",
                "input": safe_input,
                "output": _coerce_output(tr_content),
                "title": "",
                "metadata": {},
                "time": {"start": ts, "end": ts},
            }
        else:
            # No matching result (truncated turn). Mark completed with an
            # empty output rather than leaving a dangling call the provider
            # would reject; still carry the time object OpenCode expects.
            state = {
                "status": "completed",
                "input": safe_input,
                "output": "",
                "title": "",
                "metadata": {},
                "time": {"start": ts, "end": ts},
            }
        _insert_part_row(conn, msg_id, session_id, {
            "type": "tool",
            "tool": tu_name,
            "callID": tu_id,
            "state": state,
        }, ts)
    elif btype == "image":
        source = block.get("source") or {}
        if source.get("type") == "base64":
            media = source.get("media_type", "image/png")
            data = source.get("data", "")
            url = f"data:{media};base64,{data}"
            filename = "image." + media.split("/")[-1] if "/" in media else "image.png"
            _insert_part_row(conn, msg_id, session_id, {
                "type": "file",
                "mime": media,
                "url": url,
                "filename": filename,
            }, ts)
        elif source.get("type") == "url":
            _insert_part_row(conn, msg_id, session_id, {
                "type": "file",
                "mime": source.get("media_type", "application/octet-stream"),
                "url": source.get("url", ""),
                "filename": source.get("url", "").split("/")[-1],
            }, ts)
    elif btype == "attachment":
        _insert_part_row(conn, msg_id, session_id, {
            "type": "file",
            "mime": block.get("mime_type", "application/octet-stream"),
            "url": block.get("path", ""),
            "filename": block.get("path", "").split("/")[-1] if block.get("path") else "",
        }, ts)


def _insert_part_row(
    conn: sqlite3.Connection,
    msg_id: str,
    session_id: str,
    pdata: Dict[str, Any],
    ts: int,
) -> None:
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created,"
        " time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (_oc_id("part", ts), msg_id, session_id, ts, ts,
         json.dumps(pdata, ensure_ascii=False)),
    )


def _inc_ts(counter: List[int]) -> int:
    """Return the current counter value, then increment for the next call."""
    v = counter[0]
    counter[0] = v + 1
    return v


def _coerce_output(content: Any) -> str:
    """OpenCode's tool state.output must be a string. Claude tool_result
    content can be a string, a list of content blocks, or a dict."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # list of {type:"text", text:...} blocks (Anthropic shape) → join text
        parts: List[str] = []
        for b in content:
            if isinstance(b, dict):
                if isinstance(b.get("text"), str):
                    parts.append(b["text"])
                else:
                    parts.append(json.dumps(b, ensure_ascii=False))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _now_epoch_ms() -> int:
    from datetime import datetime, timezone
    return int(datetime.now(timezone.utc).timestamp() * 1000)


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
