"""Multi-account (multi CLAUDE_CONFIG_DIR) discovery and resume routing.

People who run several Claude logins on one machine give each its own config
dir (~/.claude, ~/.claude-work, ...). ccfind must search them all and, on
resume, point `claude` at the account that actually owns the transcript --
otherwise resume fails with "conversation ID does not exist".

Run with: python3 tests/test_multi_account.py   (or under pytest)
"""
import importlib.util
import os
import sys
import tempfile
from importlib.machinery import SourceFileLoader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_ccfind():
    # ccfind has no .py extension, so name the loader explicitly.
    path = os.path.join(REPO_ROOT, "ccfind")
    loader = SourceFileLoader("ccfind_script", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _mk_session(home, account, slug, sid):
    d = os.path.join(home, account, "projects", slug)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, sid + ".jsonl")
    with open(fp, "w") as f:
        f.write('{"type":"user","cwd":"/tmp/x","sessionId":"%s"}\n' % sid)
    return fp


def test_roots_and_resume():
    mod = load_ccfind()
    with tempfile.TemporaryDirectory() as home:
        mod.HOME = home  # claude_roots() reads module-level HOME
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

        p_default = _mk_session(home, ".claude", "-tmp-x", "aaaa")
        p_work = _mk_session(home, ".claude-work", "-tmp-x", "bbbb")
        p_pers = _mk_session(home, ".claude-personal", "-tmp-x", "cccc")

        roots = mod.claude_roots()
        # all three accounts discovered
        assert len(roots) == 3, roots
        accounts = {os.path.basename(os.path.dirname(r)) for r in roots}
        assert accounts == {".claude", ".claude-work", ".claude-personal"}, accounts

        # config-dir derivation walks up to the parent of `projects`
        assert mod._claude_config_dir_for_path(p_work) == \
            os.path.join(home, ".claude-work"), "config dir for work session"

        # resume of a non-active account points CLAUDE_CONFIG_DIR at it
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(home, ".claude")
        cwd, argv = mod.claude_resume_argv(
            {"sid": "cccc", "row_path": p_pers, "is_sub": False})
        assert argv == ["claude", "--resume", "cccc"], argv
        assert os.environ["CLAUDE_CONFIG_DIR"] == \
            os.path.join(home, ".claude-personal"), \
            "resume must retarget the owning account"

        # explicit CLAUDE_CONFIG_DIR is included even if non-standard / first
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(home, ".claude-work")
        roots2 = mod.claude_roots()
        assert roots2[0] == p_work.rsplit("/-tmp-x/", 1)[0], roots2

    os.environ.pop("CLAUDE_CONFIG_DIR", None)
    print("OK: multi-account discovery + resume routing")


if __name__ == "__main__":
    test_roots_and_resume()
    sys.exit(0)
