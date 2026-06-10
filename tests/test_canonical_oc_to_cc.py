"""End-to-end smoke test: real OpenCode session → canonical → Claude JSONL.

Verifies:
    1. ccfind_core can be imported.
    2. An OpenCode session reads into Conversation.
    3. Canonical Conversation serializes round-trip (dict → JSON → dict).
    4. Writing produces a JSONL file under a temp Claude root.
    5. Every output line is valid JSON with parentUuid chain unbroken.
    6. sessionId field is consistent across all lines.

Run with: python3 -m tests.test_canonical_oc_to_cc
"""
import json
import os
import sqlite3
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from ccfind_core import adapters, Conversation, CANONICAL_VERSION


def find_smallest_opencode_session(db_path):
    """Pick the smallest session with at least one message to keep the
    test cheap."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT s.id, COUNT(m.id) AS n_msg "
            "FROM session s LEFT JOIN message m ON m.session_id = s.id "
            "GROUP BY s.id HAVING n_msg > 0 ORDER BY n_msg ASC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def main():
    db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if not os.path.isfile(db):
        print("SKIP: no OpenCode DB at", db)
        return 0

    pick = find_smallest_opencode_session(db)
    if not pick:
        print("SKIP: no OpenCode sessions with messages")
        return 0
    sid = pick["id"]
    print(f"[1/6] Picked OpenCode session: id={sid!r} msgs={pick['n_msg']}")

    print("[2/6] Reading via opencode adapter…")
    conv = adapters.read("opencode", sid)
    assert isinstance(conv, Conversation), "read did not return Conversation"
    assert conv.source_agent == "opencode"
    assert conv.source_session_id == sid
    print(f"      events={len(conv.events)} cwd={conv.cwd!r}")

    print("[3/6] Round-tripping canonical dict→JSON→dict…")
    j = conv.to_json(indent=None)
    conv2 = Conversation.from_json(j)
    assert conv2.source_session_id == conv.source_session_id
    assert len(conv2.events) == len(conv.events)
    print(f"      json bytes={len(j)}")

    print("[4/6] Writing into temp Claude root…")
    with tempfile.TemporaryDirectory() as tmp:
        result = adapters.write(
            "claude",
            conv,
            cwd=conv.cwd or "/tmp/test",
            root=tmp,
        )
        out_path = result["path"]
        out_sid = result["session_id"]
        assert os.path.isfile(out_path), f"output not created: {out_path}"
        print(f"      wrote {out_path}")

        print("[5/6] Validating JSONL output…")
        seen_sids = set()
        seen_uuids = set()
        prev_uuid = None
        line_count = 0
        with open(out_path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                d = json.loads(ln)  # must parse
                line_count += 1
                if "sessionId" in d:
                    seen_sids.add(d["sessionId"])
                uid = d.get("uuid")
                if uid:
                    seen_uuids.add(uid)
                pu = d.get("parentUuid")
                if pu is not None:
                    assert pu in seen_uuids or pu == prev_uuid, \
                        f"parentUuid {pu!r} not seen before on line {line_count}"
                prev_uuid = uid or prev_uuid
        assert seen_sids == {out_sid}, \
            f"sessionId not consistent: {seen_sids} vs {out_sid}"
        print(f"      lines={line_count} consistent sessionId={out_sid}")

        print("[6/6] Reading the new Claude file BACK through the claude adapter…")
        conv3 = adapters.read("claude", out_sid, path=out_path)
        # message count should be ≥ OC message count (we may have added
        # synthesized user events for tool_result splits, plus skipped meta
        # events on the OC side).
        oc_msgs = len(conv.message_events())
        cc_msgs = len(conv3.message_events())
        assert cc_msgs >= 1, "no messages survived the round trip"
        print(f"      canonical messages: OC={oc_msgs} CC={cc_msgs}")

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
