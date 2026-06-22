"""Real-write end-to-end test for the Ctrl-X port flow.

This DOES write to ~/.claude/projects/. The new transcript is real and
would be resumable via `claude --resume`. We pick the smallest non-empty
OpenCode session to minimize footprint. The test prints the new session
id and path so you can spot-check / clean up.

Run with: python3 tests/test_port_ctrlx_real.py
"""
import json
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from ccfind_core import adapters


def smallest_oc_session(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT s.id, s.title, COUNT(m.id) AS n_msg "
            "FROM session s LEFT JOIN message m ON m.session_id = s.id "
            "GROUP BY s.id HAVING n_msg > 0 ORDER BY n_msg ASC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def main():
    db = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if not os.path.isfile(db):
        print("SKIP: no OpenCode DB")
        return 0
    pick = smallest_oc_session(db)
    if not pick:
        print("SKIP: no OpenCode sessions with messages")
        return 0
    sid = pick["id"]
    print(f"Source OC session: {sid}  title={pick['title']!r}  msgs={pick['n_msg']}")

    # === simulate exactly what the Ctrl-X handler does ===
    conv = adapters.read("opencode", sid)
    conv.metadata.setdefault("forkedFrom", {
        "source_agent": "opencode",
        "source_session_id": sid,
    })
    result = adapters.write("claude", conv, cwd=conv.cwd)
    new_sid = result["session_id"]
    out_path = result["path"]
    cwd = result["cwd"]

    print(f"  → New Claude session: {new_sid}")
    print(f"  → Path: {out_path}")
    print(f"  → Resume CWD: {cwd}")

    # Verify file is under the active Claude root. The adapter honors
    # CLAUDE_CONFIG_DIR (multi-account setups), so derive root the same way.
    real_base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    real_root = os.path.join(real_base, "projects")
    assert out_path.startswith(real_root + os.sep), (
        f"output path not under {real_root}: {out_path}"
    )

    # Walk lines and check the resume invariants
    sids_seen = set()
    uuids = set()
    line_count = 0
    forked_from_found = False
    with open(out_path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            line_count += 1
            if "sessionId" in d:
                sids_seen.add(d["sessionId"])
            uid = d.get("uuid")
            if uid:
                uuids.add(uid)
            pu = d.get("parentUuid")
            if pu is not None:
                assert pu in uuids, f"line {line_count}: parentUuid {pu!r} not seen"
            if d.get("forkedFrom"):
                ff = d["forkedFrom"]
                assert ff.get("source_session_id") == sid, ff
                forked_from_found = True

    assert sids_seen == {new_sid}, f"sessionId mismatch: {sids_seen} vs {new_sid}"
    assert forked_from_found, "forkedFrom metadata missing — Ctrl-X provenance lost"

    print(f"  ✓ {line_count} JSONL lines, parentUuid chain consistent")
    print(f"  ✓ forkedFrom points back to opencode session {sid}")
    print()
    print("To verify in Claude Code, run:")
    print(f"  cd {cwd}")
    print(f"  claude --resume {new_sid}")
    print()
    print("To clean up:")
    print(f"  rm {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
