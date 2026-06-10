"""Real-write end-to-end test for Hermes → Claude Ctrl-X port.

Picks the smallest non-trivial Hermes session, ports it, verifies the
resulting Claude JSONL.

Run: python3 tests/test_port_hermes_to_claude.py
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from ccfind_core import adapters


def smallest_hermes_jsonl():
    """Pick the smallest .jsonl Hermes session — has the cleanest line
    coverage and minimizes write footprint."""
    candidates = []
    for row in adapters.list_sessions("hermes"):
        if not row["path"].endswith(".jsonl"):
            continue
        try:
            sz = os.path.getsize(row["path"])
        except OSError:
            sz = 0
        if sz > 0:
            candidates.append((sz, row))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def main():
    pick = smallest_hermes_jsonl()
    if not pick:
        print("SKIP: no Hermes .jsonl sessions")
        return 0
    sid = pick["id"]
    print(f"Source Hermes session: {sid}")
    print(f"  path: {pick['path']}")

    conv = adapters.read("hermes", sid, path=pick["path"])
    conv.metadata.setdefault("forkedFrom", {
        "source_agent": "hermes",
        "source_session_id": sid,
    })
    print(f"  canonical events: {len(conv.events)}")
    print(f"  metadata keys: {sorted(conv.metadata.keys())}")

    # Hermes doesn't carry cwd — use a stable test dir.
    target_cwd = os.path.expanduser("~/Documents/nemo-lab.nosync/ccfind")
    if not os.path.isdir(target_cwd):
        target_cwd = os.getcwd()

    result = adapters.write("claude", conv, cwd=target_cwd)
    new_sid = result["session_id"]
    out_path = result["path"]
    print(f"  → New Claude session: {new_sid}")
    print(f"  → Path: {out_path}")
    print(f"  → Resume CWD: {result['cwd']}")

    real_root = os.path.expanduser("~/.claude/projects")
    assert out_path.startswith(real_root + os.sep), out_path

    seen_uuids = set()
    seen_sids = set()
    line_count = 0
    forked_from_found = False
    block_type_counts = {}
    with open(out_path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            line_count += 1
            if "sessionId" in d:
                seen_sids.add(d["sessionId"])
            uid = d.get("uuid")
            if uid:
                seen_uuids.add(uid)
            pu = d.get("parentUuid")
            if pu is not None:
                assert pu in seen_uuids, f"line {line_count}: parentUuid {pu!r} unseen"
            if d.get("forkedFrom"):
                if d["forkedFrom"].get("source_session_id") == sid:
                    forked_from_found = True
            msg = d.get("message") or {}
            c = msg.get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict):
                        t = b.get("type")
                        block_type_counts[t] = block_type_counts.get(t, 0) + 1

    assert seen_sids == {new_sid}, f"sessionId mismatch: {seen_sids} vs {new_sid}"
    assert forked_from_found, "forkedFrom missing"
    print(f"  ✓ {line_count} JSONL lines, parentUuid chain consistent")
    print(f"  ✓ forkedFrom points back to hermes://{sid}")
    print(f"  ✓ content blocks: {block_type_counts}")
    print()
    print("To verify in Claude Code, run:")
    print(f"  cd {result['cwd']}")
    print(f"  claude --resume {new_sid}")
    print()
    print("To clean up:")
    print(f"  rm {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
