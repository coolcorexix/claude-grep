"""Ctrl-B branch must write where `claude --resume` will look.

Claude derives a project folder from cwd by replacing every non-alphanumeric
char with `-`. Older builds only replaced `/`, so a session created then lives
in a "stale" folder (e.g. `.nosync` kept as a dot) that today's Claude won't
find. branch_claude_session must therefore write the fork into
`<config-dir>/projects/<current-slug(cwd)>/`, not blindly next to the parent —
otherwise the branched conversation is findable by ccfind but not resumable.

Run: python3 tests/test_branch_resumable.py   (or pytest)
"""
import importlib.util
import json
import os
import sys
import tempfile
import shutil
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


def load_ccfind():
    path = os.path.join(REPO_ROOT, "ccfind")
    loader = SourceFileLoader("ccfind_script", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


mod = load_ccfind()


def test_slug_rule_matches_claude():
    cases = {
        "/Users/x/Documents/nemo-lab.nosync/ccfind":
            "-Users-x-Documents-nemo-lab-nosync-ccfind",
        "/Users/x/Documents/saleshood-workdir.nosync/saleshood/webapp":
            "-Users-x-Documents-saleshood-workdir-nosync-saleshood-webapp",
        "/a_b/c d/e.f":
            "-a-b-c-d-e-f",
    }
    for cwd, slug in cases.items():
        assert mod._claude_slug_for_cwd(cwd) == slug, cwd
    # adapter must use the identical rule (Ctrl-X port writes)
    from ccfind_core.adapters import claude as cc
    for cwd, slug in cases.items():
        assert cc.slug_for_cwd(cwd) == slug, ("adapter", cwd)


def _entry(uuid, parent, typ, text, cwd, sid):
    return {"uuid": uuid, "parentUuid": parent, "type": typ, "cwd": cwd,
            "sessionId": sid, "message": {"role": typ, "content": text}}


def test_branch_writes_to_resumable_folder():
    home = tempfile.mkdtemp()
    try:
        cfg = os.path.join(home, ".claude-test")
        cwd = "/Users/x/Documents/proj.nosync/app"          # note the dot
        dash = "-Users-x-Documents-proj-nosync-app"          # today's Claude rule
        dot = "-Users-x-Documents-proj.nosync-app"           # old (stale) rule
        psid = "parent-sid"
        # simulate an OLD parent stored in the STALE (dot) folder
        stale_dir = os.path.join(cfg, "projects", dot)
        os.makedirs(stale_dir)
        parent_path = os.path.join(stale_dir, psid + ".jsonl")
        entries = [
            _entry("u1", None, "user", "first", cwd, psid),
            _entry("a1", "u1", "assistant", "ok", cwd, psid),
            _entry("u2", "a1", "user", "BRANCH HERE", cwd, psid),  # checkpoint
            _entry("a2", "u2", "assistant", "doing it", cwd, psid),
            _entry("u3", "a2", "user", "next topic", cwd, psid),    # boundary
        ]
        with open(parent_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        new_sid, new_path = mod.branch_claude_session(parent_path, psid, "u2")

        # 1) written into the DASH (resumable) folder, NOT next to the parent
        assert os.path.dirname(new_path) == os.path.join(cfg, "projects", dash), new_path
        assert dot not in new_path
        assert os.path.isfile(new_path)

        # 2) the owning config dir resolves back to cfg (so resume retargets right)
        assert mod._claude_config_dir_for_path(new_path) == cfg

        # 3) branch content: u1,a1,u2,a2 (stops before u3), new sid, forkedFrom
        rows = [json.loads(l) for l in open(new_path) if l.strip()]
        assert [r["uuid"] for r in rows] == ["u1", "a1", "u2", "a2"]
        assert all(r["sessionId"] == new_sid for r in rows)
        assert all(r["forkedFrom"] == {"sessionId": psid, "messageUuid": "u2"}
                   for r in rows)

        # 4) the branch's own latest tip is live (resumable end state)
        assert mod.classify_match(new_path, "a2") == "live-path"
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_branch_fallback_when_no_cwd():
    # if entries carry no cwd, fall back to writing beside the parent (no crash)
    home = tempfile.mkdtemp()
    try:
        d = os.path.join(home, "projects", "-slug")
        os.makedirs(d)
        pp = os.path.join(d, "p.jsonl")
        entries = [
            {"uuid": "u1", "parentUuid": None, "type": "user",
             "sessionId": "p", "message": {"role": "user", "content": "hi"}},
            {"uuid": "a1", "parentUuid": "u1", "type": "assistant",
             "sessionId": "p", "message": {"role": "assistant", "content": "yo"}},
            {"uuid": "u2", "parentUuid": "a1", "type": "user",
             "sessionId": "p", "message": {"role": "user", "content": "more"}},
        ]
        with open(pp, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        new_sid, new_path = mod.branch_claude_session(pp, "p", "u1")
        assert os.path.dirname(new_path) == d  # fell back beside parent
        assert os.path.isfile(new_path)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
