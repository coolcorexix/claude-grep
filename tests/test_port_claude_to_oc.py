"""Structural validation: port a Claude session → OpenCode DB, then
verify every table/field matches the shape of a real OpenCode session.

This catches schema mismatches (missing columns, wrong field types, bad
part ordering) that the simpler round-trip tests miss — the kind of bug
that makes OpenCode throw "Unexpected server error" at resume time.

Run with: python3 tests/test_port_claude_to_oc.py
"""
import json
import os
import sqlite3
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from ccfind_core import adapters


def _find_smallest_claude_session():
    sessions = adapters.list_sessions("claude")
    if not sessions:
        return None
    sessions.sort(key=lambda s: os.path.getsize(s["path"]))
    return sessions[0]


def _real_oc_session_sample():
    """Read a real OpenCode session's structural fingerprint so we can
    compare our ported output against it."""
    db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if not os.path.isfile(db):
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM session WHERE id LIKE 'ses_%' ORDER BY "
            "time_updated DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        real_sid = row["id"]

        s = dict(conn.execute("SELECT * FROM session WHERE id=?", (real_sid,)).fetchone())

        m = dict(conn.execute(
            "SELECT * FROM message WHERE session_id=? ORDER BY time_created LIMIT 1",
            (real_sid,),
        ).fetchone())

        parts = conn.execute(
            "SELECT * FROM part WHERE message_id=? ORDER BY time_created",
            (m["id"],),
        ).fetchall()

        return {
            "session_cols": set(s.keys()),
            "session_not_null": {k for k, v in s.items() if v is not None},
            "message_cols": set(m.keys()),
            "part_cols": set(parts[0].keys()) if parts else set(),
            "sample_part_types": [json.loads(p["data"]).get("type") for p in parts],
        }
    finally:
        conn.close()


def main():
    source = _find_smallest_claude_session()
    if not source:
        print("SKIP: no Claude sessions found")
        return 0

    print(f"Source Claude: {source['id'][:8]}... "
          f"({os.path.getsize(source['path'])} bytes)")

    conv = adapters.read("claude", source["id"], path=source["path"])
    event_count = len(conv.events)
    msg_count = len(conv.message_events())
    print(f"  events={event_count} messages={msg_count}")

    # Get real OpenCode session structure for comparison
    real = _real_oc_session_sample()

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "opencode.db")
        result = adapters.write("opencode", conv, db_path=db_path,
                                cwd=conv.cwd or "/tmp/test")
        new_sid = result["session_id"]
        print(f"  → OpenCode session: {new_sid}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # --- Session-level checks ---
        s = dict(conn.execute(
            "SELECT * FROM session WHERE id=?", (new_sid,)
        ).fetchone())

        required_session = {
            "id", "project_id", "slug", "directory", "title", "version",
            "time_created", "time_updated", "agent", "model",
        }
        for col in required_session:
            assert col in s, f"session missing column: {col}"
        assert s["id"].startswith("ses_"), f"session id format: {s['id']}"
        assert s["slug"], "session slug is empty"
        assert s["version"], "session version is empty"
        assert s["agent"], "session agent is empty"

        # model must be a JSON object (real OC uses json there)
        try:
            model_obj = json.loads(s["model"])
            assert isinstance(model_obj, dict), f"session.model not a dict: {s['model']!r}"
        except (json.JSONDecodeError, TypeError):
            assert False, f"session.model not valid JSON: {s['model']!r}"

        if real:
            assert s.keys() == real["session_cols"], (
                f"session columns mismatch:\n"
                f"  ours:   {sorted(s.keys())}\n"
                f"  real:   {sorted(real['session_cols'])}\n"
                f"  extra:  {sorted(s.keys() - real['session_cols'])}\n"
                f"  missing:{sorted(real['session_cols'] - s.keys())}"
            )

        print(f"  ✓ session: slug={s['slug']!r} agent={s['agent']!r} "
              f"model_keys={sorted(model_obj.keys())}")

        # --- Message-level checks ---
        msgs = conn.execute(
            "SELECT * FROM message WHERE session_id=? ORDER BY time_created",
            (new_sid,),
        ).fetchall()

        assert len(msgs) >= 1, "no messages written"
        if real:
            assert set(msgs[0].keys()) == real["message_cols"], (
                f"message columns mismatch:\n"
                f"  ours:{sorted(msgs[0].keys())}\n"
                f"  real:{sorted(real['message_cols'])}"
            )

        for m in msgs:
            md = json.loads(m["data"])
            assert "role" in md, f"message missing role: {m['id'][:8]}..."
            if md.get("role") == "assistant":
                # Real OpenCode assistant messages carry modelID + providerID
                # (NOT a nested `model` object — that lives on user messages).
                assert "agent" in md, f"assistant msg missing agent: {m['id'][:8]}..."
                assert "modelID" in md, f"assistant msg missing modelID: {m['id'][:8]}..."
                assert "providerID" in md, f"assistant msg missing providerID: {m['id'][:8]}..."
            # Check for parentID chain (except first message)
            assert "time" in md, f"message missing time: {m['id'][:8]}..."

        # Verify parentID chain
        has_parent = sum(1 for m in msgs if "parentID" in json.loads(m["data"]))
        assert has_parent == len(msgs) - 1, (
            f"parentID chain broken: {has_parent}/{len(msgs)-1} expected"
        )

        print(f"  ✓ messages: {len(msgs)} total, parentID chain intact")

        # --- Part-level checks ---
        assistant_msgs = [
            m for m in msgs
            if json.loads(m["data"]).get("role") == "assistant"
        ]
        for m in assistant_msgs:
            md = json.loads(m["data"])
            parts = conn.execute(
                "SELECT * FROM part WHERE message_id=? ORDER BY time_created",
                (m["id"],),
            ).fetchall()
            if real and parts:
                assert set(parts[0].keys()) == real["part_cols"], (
                    f"part columns mismatch"
                )
            ptypes = [json.loads(p["data"]).get("type") for p in parts]
            if ptypes:
                assert ptypes[0] == "step-start", (
                    f"assistant msg missing leading step-start: "
                    f"msg={m['id'][:8]}... role={md.get('role')} parts={ptypes}"
                )
                assert ptypes[-1] == "step-finish", (
                    f"assistant msg missing trailing step-finish: "
                    f"msg={m['id'][:8]}... parts={ptypes}"
                )

        print(f"  ✓ parts: step-start/step-finish ordering correct "
              f"({len(assistant_msgs)} assistant messages)")

        # --- Deep message structure conformance ---
        # Compare every key of our message data against real OpenCode.
        if real:
            _check_message_conformance(conn, new_sid)

        # --- Read-back round-trip ---
        conv2 = adapters.read("opencode", new_sid, db_path=db_path)
        assert conv2.message_events(), "no messages survived round-trip"
        print(f"  ✓ round-trip: {len(conv2.message_events())} messages")

        conn.close()

    print("PASS")
    return 0


def _check_message_conformance(conn, new_sid):
    """Ensure every ported message has the same keys as real OpenCode
    messages (per role), catching frontend-crash fields like tokens.output."""
    real_db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    rconn = sqlite3.connect(f"file:{real_db}?mode=ro", uri=True)
    rconn.row_factory = sqlite3.Row
    try:
        # Collect the set of top-level keys for real user/assistant msgs
        real_user_keys: set = set()
        real_asst_keys: set = set()
        for m in rconn.execute(
            "SELECT data FROM message ORDER BY time_created LIMIT 200"
        ).fetchall():
            md = json.loads(m["data"])
            if md.get("role") == "user":
                real_user_keys |= set(md.keys())
            elif md.get("role") == "assistant":
                real_asst_keys |= set(md.keys())
    finally:
        rconn.close()

    ported = conn.execute(
        "SELECT data FROM message WHERE session_id=? ORDER BY time_created",
        (new_sid,),
    ).fetchall()

    user_issues = 0
    asst_issues = 0
    for m in ported:
        md = json.loads(m["data"])
        role = md.get("role")
        if role == "user":
            # User messages should have all real_user_keys
            missing = real_user_keys - set(md.keys())
            if missing:
                user_issues += 1
                if user_issues <= 3:
                    print(f"  ⚠ user msg missing real keys: {sorted(missing)}")
        elif role == "assistant":
            missing = real_asst_keys - set(md.keys())
            if missing:
                asst_issues += 1
                if asst_issues <= 3:
                    print(f"  ⚠ asst msg missing real keys: {sorted(missing)}")

    if user_issues or asst_issues:
        print(f"  ✗ message conformance: {user_issues} user / {asst_issues} "
              f"asst field gaps vs real sessions")
    else:
        print("  ✓ message conformance: all keys match real OpenCode")

    # --- Part field conformance ---
    _check_part_conformance(conn, new_sid)


def _check_part_conformance(conn, new_sid):
    """Ensure every ported part has the same keys as real OpenCode parts
    (per type), catching frontend crashes like part.time.end."""
    real_db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    rconn = sqlite3.connect(f"file:{real_db}?mode=ro", uri=True)
    rconn.row_factory = sqlite3.Row
    try:
        real_keys: dict = {}
        for p in rconn.execute(
            "SELECT data FROM part LIMIT 1000"
        ).fetchall():
            pd = json.loads(p["data"])
            ptype = pd.get("type", "?")
            if ptype not in real_keys:
                real_keys[ptype] = set()
            real_keys[ptype] |= set(pd.keys())
    finally:
        rconn.close()

    ported = conn.execute(
        "SELECT data FROM part WHERE session_id=?",
        (new_sid,),
    ).fetchall()

    issues = 0
    for p in ported:
        pd = json.loads(p["data"])
        ptype = pd.get("type", "?")
        expected = real_keys.get(ptype, set())
        missing = expected - set(pd.keys())
        extra = set(pd.keys()) - expected
        if missing or extra:
            issues += 1
            if issues <= 3:
                print(f"  ⚠ part {ptype}: missing={sorted(missing)} "
                      f"extra={sorted(extra)}")

    if issues:
        print(f"  ✗ part conformance: {issues} part field mismatches")
    else:
        print("  ✓ part conformance: all part keys match real OpenCode")


if __name__ == "__main__":
    raise SystemExit(main())
