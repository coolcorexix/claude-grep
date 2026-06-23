"""Tests for divergence detection — classify_match / _claude_live_path_uuids.

A Claude session is a tree: editing/re-sending a message (or two terminals on
one session) forks the parentUuid chain into an abandoned branch + a kept
branch. `claude --resume` only opens the newest tip, so a search hit on the
abandoned branch resumes you onto a *different* conversation. classify_match
tells us whether a matched message is on the live (resumable) path or stranded.

The synthetic test is deterministic and machine-independent. The real-data
section validates against the user's own transcripts and SKIPs without them.

Run: python3 tests/test_divergence.py   (or pytest)
"""
import importlib.util
import json
import os
import sys
import tempfile
import collections
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_ccfind():
    path = os.path.join(REPO_ROOT, "ccfind")
    loader = SourceFileLoader("ccfind_script", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


mod = load_ccfind()


def _line(uuid, parent, typ, text):
    return {"uuid": uuid, "parentUuid": parent, "type": typ,
            "message": {"role": typ, "content": text}}


def _write(lines):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for d in lines:
            f.write(json.dumps(d) + "\n")
    return p


# --------------------------------------------------------------------------
# Synthetic, deterministic: a session that forks at a1 into stale vs kept
# --------------------------------------------------------------------------
def test_synthetic_fork_classification():
    # u1 -> a1 -> { u2a (abandoned) , u2b (kept) -> a2b -> u3b(last) }
    lines = [
        _line("u1",  None, "user",      "start the task"),
        _line("a1",  "u1", "assistant", "ok, on it"),
        _line("u2a", "a1", "user",      "do X please"),          # ABANDONED edit
        _line("u2b", "a1", "user",      "actually do Y instead"),# KEPT (resent)
        _line("a2b", "u2b","assistant", "doing Y"),
        _line("u3b", "a2b","user",      "great, continue"),      # newest tip
    ]
    p = _write(lines)
    try:
        live = mod._claude_live_path_uuids(p)
        assert live == {"u1", "a1", "u2b", "a2b", "u3b"}, live
        assert "u2a" not in live

        assert mod.classify_match(p, "u2b") == "live-path"
        assert mod.classify_match(p, "u3b") == "live-path"   # the tip
        assert mod.classify_match(p, "u1")  == "live-path"   # shared ancestor
        assert mod.classify_match(p, "u2a") == "stale-branch"  # the bug case
        # safe fallbacks
        assert mod.classify_match(p, None) == "live-path"
        assert mod.classify_match(p, "does-not-exist") == "stale-branch"

        # match-by-query resolves the abandoned message → stale
        u = mod._claude_match_uuid(p, "do X please")
        assert u == "u2a"
        assert mod.classify_match(p, u) == "stale-branch"
        # and the kept one → live
        u2 = mod._claude_match_uuid(p, "do Y instead")
        assert u2 == "u2b" and mod.classify_match(p, u2) == "live-path"
    finally:
        os.unlink(p)


def test_synthetic_linear_all_live():
    lines = [
        _line("u1", None, "user", "hi"),
        _line("a1", "u1", "assistant", "hello"),
        _line("u2", "a1", "user", "bye"),
    ]
    p = _write(lines)
    try:
        for u in ("u1", "a1", "u2"):
            assert mod.classify_match(p, u) == "live-path"
    finally:
        os.unlink(p)


# --------------------------------------------------------------------------
# Real-data validation (skips cleanly if the user's transcripts aren't here)
# --------------------------------------------------------------------------
INJECT = ("<system-reminder", "<command-name", "<local-command",
          "<bash-input", "<bash-stdout", "caveat:")


def _human_text(d):
    if d.get("type") != "user" or d.get("isMeta"):
        return None
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None
        t = " ".join(b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    t = (t or "").strip()
    if not t or any(x in t[:30].lower() for x in INJECT):
        return None
    return t


def _all_claude_files():
    import glob
    home = os.path.expanduser("~")
    bases = ([os.environ.get("CLAUDE_CONFIG_DIR") or ""]
             + [os.path.join(home, ".claude")]
             + sorted(glob.glob(os.path.join(home, ".claude-*"))))
    files = []
    seen = set()
    for b in bases:
        proj = os.path.join(b, "projects")
        rp = os.path.realpath(proj)
        if rp in seen or not os.path.isdir(proj):
            continue
        seen.add(rp)
        for dp, _, fns in os.walk(proj):
            if "subagents" in dp:
                continue
            for fn in fns:
                if fn.endswith(".jsonl"):
                    files.append(os.path.join(dp, fn))
    return files


def _forks(path):
    """parents with >=2 human-typed children — the true divergence signature."""
    by = {}
    children = collections.defaultdict(list)
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                u = d.get("uuid")
                if not u:
                    continue
                by[u] = d
                children[d.get("parentUuid")].append(u)
    except OSError:
        return []
    out = []
    for p, kids in children.items():
        if not p:
            continue
        hk = [k for k in dict.fromkeys(kids) if _human_text(by.get(k, {}))]
        if len(hk) >= 2:
            out.append(hk)
    return out


def test_real_data_invariants():
    files = _all_claude_files()
    if not files:
        print("SKIP: no Claude transcripts on this machine")
        return
    divergent = [(f, _forks(f)) for f in files]
    divergent = [(f, fk) for f, fk in divergent if fk]
    if not divergent:
        print("SKIP: no divergent sessions found")
        return

    discriminated = False
    for f, forks in divergent:
        live = mod._claude_live_path_uuids(f)
        for hk in forks:
            live_kids = [k for k in hk if mod.classify_match(f, k, _live_cache=live) == "live-path"]
            # INVARIANT: a tree has one path to the tip → at most one kept child
            assert len(live_kids) <= 1, (os.path.basename(f), len(live_kids))
            if len(live_kids) == 1 and len(hk) - len(live_kids) >= 1:
                discriminated = True
    # across all real divergent sessions, the detector must actually split at
    # least one fork into kept vs abandoned (else it's trivially always-live)
    assert discriminated, "classify_match never distinguished a stale branch"
    print(f"OK real-data: {len(divergent)} divergent sessions, invariants hold")


# --------------------------------------------------------------------------
def test_field1_carries_match_uuid():
    row = {"source": "claude", "sid": "s1", "is_sub": False,
           "match_uuid": "abc-123", "row_path": "/p|x/with|pipes.jsonl"}
    dec = mod._decode_field1(mod._encode_field1(row))
    assert dec["match_uuid"] == "abc-123"
    assert dec["row_path"] == "/p|x/with|pipes.jsonl"  # pipes still safe
    # empty match_uuid round-trips to None
    row2 = dict(row); row2["match_uuid"] = None
    assert mod._decode_field1(mod._encode_field1(row2))["match_uuid"] is None


def test_search_to_classify_pipeline():
    """End-to-end: searching text that lives on a stale branch returns a row
    whose match_uuid classifies as stale-branch; the kept branch is live."""
    import shutil
    if not shutil.which("rg"):
        print("SKIP: ripgrep not installed")
        return
    home = tempfile.mkdtemp()
    old_home, old_cfg = mod.HOME, os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        mod.HOME = home
        cfg = os.path.join(home, ".claude")
        os.environ["CLAUDE_CONFIG_DIR"] = cfg
        d = os.path.join(cfg, "projects", "-tmp-proj")
        os.makedirs(d)
        cwd = "/tmp/proj"
        lines = [
            _line("u1", None, "user", "start") | {"cwd": cwd},
            _line("a1", "u1", "assistant", "ok") | {"cwd": cwd},
            _line("u2a", "a1", "user", "please WIDGETIZE the X thing") | {"cwd": cwd},
            _line("u2b", "a1", "user", "actually FROBNICATE the Y thing") | {"cwd": cwd},
            _line("a2b", "u2b", "assistant", "frobnicating") | {"cwd": cwd},
            _line("u3b", "a2b", "user", "keep going") | {"cwd": cwd},
        ]
        fp = os.path.join(d, "sid-div.jsonl")
        with open(fp, "w") as f:
            for x in lines:
                f.write(json.dumps(x) + "\n")

        # stale branch hit
        rows = mod.claude_find("WIDGETIZE")
        assert rows, "expected a search hit"
        r = rows[0]
        assert r["match_uuid"] == "u2a", r["match_uuid"]
        assert mod.classify_match(r["row_path"], r["match_uuid"]) == "stale-branch"

        # kept branch hit
        rows2 = mod.claude_find("FROBNICATE")
        r2 = rows2[0]
        assert r2["match_uuid"] == "u2b"
        assert mod.classify_match(r2["row_path"], r2["match_uuid"]) == "live-path"
    finally:
        mod.HOME = old_home
        if old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg
        shutil.rmtree(home, ignore_errors=True)


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
