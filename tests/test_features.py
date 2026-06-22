"""Unit tests for ccfind's core, deterministic feature logic.

These use synthetic fixtures (no dependence on your real ~/.claude history),
so they're reproducible anywhere. They cover the pure logic behind every
user-facing feature:

  * fuzzy separator matching        (rSideProject ↔ r/SideProject)
  * snippet extraction & highlight
  * Claude text extraction          (full vs --user-only, tool_result, isMeta,
                                      injected <system-reminder> stripping)
  * resumable session id / sub-agent detection
  * fzf row encode/decode round-trip
  * end-to-end search over a synthetic store (claude_find)
  * branch / fork creation          (forkedFrom, checkpoint boundary)
  * timestamp + path formatting helpers
  * Hermes message extraction
  * ANSI-aware width / right-alignment

Run with: python3 tests/test_features.py   (or: pytest tests/test_features.py)
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
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


# --------------------------------------------------------------------------
# fuzzy separator matching — the headline search behavior
# --------------------------------------------------------------------------
def test_sep_pattern_matches_across_separators():
    import re
    pat = mod._sep_pattern("rSideProject")
    assert re.search(pat, "cd ~/r/SideProject", re.IGNORECASE)
    assert re.search(pat, "the r-SideProject repo", re.IGNORECASE)
    assert re.search(pat, "rSideProject", re.IGNORECASE)
    # at most ONE separator between chars, so two separators must NOT match
    assert not re.search(pat, "r//SideProject", re.IGNORECASE)


def test_sep_pattern_escapes_regex_metachars():
    import re
    # a query containing regex metacharacters must be treated literally:
    # the '.' is a literal dot, NOT regex "any char".
    pat = mod._sep_pattern("a.b")
    assert re.search(pat, "a.b")            # literal match
    assert not re.search(pat, "axb")        # '.' did not match an arbitrary char
    assert not re.search(pat, "axyzb")


# --------------------------------------------------------------------------
# snippet extraction & highlight
# --------------------------------------------------------------------------
def test_first_text_match_returns_context_window():
    text = "alpha " * 40 + "NEEDLE" + " omega" * 40
    snip = mod.first_text_match(text, "NEEDLE")
    assert snip is not None and "NEEDLE" in snip
    assert snip.startswith("…") and snip.endswith("…")  # trimmed both sides


def test_first_text_match_none_when_absent():
    assert mod.first_text_match("hello world", "zzz") is None
    assert mod.first_text_match("", "q") is None


def test_highlight_wraps_match_in_color():
    out = mod.highlight("find the needle here", "needle")
    assert mod.YELLOW in out and mod.RESET in out
    # stripping ANSI restores the original text
    assert mod.ANSI_RE.sub("", out) == "find the needle here"


def test_raw_snippet_unescapes_jsonl_artifacts():
    raw = r'some text \"quoted\" and a \\path NEEDLE tail'
    snip = mod.raw_snippet(raw, "NEEDLE")
    assert "NEEDLE" in snip
    assert '\\"' not in snip and '"quoted"' in snip


# --------------------------------------------------------------------------
# Claude text extraction: full vs user-only
# --------------------------------------------------------------------------
def _user_entry(content, **kw):
    e = {"type": "user", "message": {"role": "user", "content": content}}
    e.update(kw)
    return e


def test_extract_text_includes_tool_result_and_assistant():
    asst = {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "assistant says hi"}]}}
    assert "assistant says hi" in mod._claude_extract_text(asst)
    tr = _user_entry([{"type": "tool_result",
                       "content": [{"type": "text", "text": "TOOL OUTPUT"}]}])
    assert "TOOL OUTPUT" in mod._claude_extract_text(tr)


def test_user_only_excludes_tool_result_and_meta():
    tr = _user_entry([{"type": "tool_result",
                       "content": [{"type": "text", "text": "TOOL OUTPUT"}]}])
    assert mod._claude_extract_user_text(tr) == ""          # tool output, not user
    meta = _user_entry("typed words", isMeta=True)
    assert mod._claude_extract_user_text(meta) == ""        # harness-injected
    asst = {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "ai words"}]}}
    assert mod._claude_extract_user_text(asst) == ""        # not a user entry


def test_user_only_keeps_typed_text_strips_injected_blocks():
    e = _user_entry(
        "real question\n<system-reminder>ignore me</system-reminder>\nmore")
    got = mod._claude_extract_user_text(e)
    assert "real question" in got and "more" in got
    assert "ignore me" not in got
    # string-form content is handled too
    assert mod._claude_extract_user_text(_user_entry("plain string")) == "plain string"


# --------------------------------------------------------------------------
# resumable id / sub-agent detection
# --------------------------------------------------------------------------
def test_claude_resumable_plain_and_subagent():
    sid, is_sub = mod.claude_resumable("/x/projects/-slug/abc123.jsonl")
    assert sid == "abc123" and is_sub is False
    # real sub-agent layout: projects/<slug>/<parent-uuid>/subagents/agent-*.jsonl
    # a sub-agent transcript isn't directly resumable, so we resume its PARENT.
    sid2, is_sub2 = mod.claude_resumable(
        "/x/projects/-slug/parent-uuid/subagents/agent-7.jsonl")
    assert is_sub2 is True and sid2 == "parent-uuid"


# --------------------------------------------------------------------------
# fzf row encode/decode round-trip
# --------------------------------------------------------------------------
def test_field1_round_trip_preserves_pipe_in_path():
    row = {"source": "claude", "sid": "s-1", "is_sub": True,
           "row_path": "/weird/path|with|pipes/x.jsonl"}
    dec = mod._decode_field1(mod._encode_field1(row))
    assert dec["source"] == "claude"
    assert dec["sid"] == "s-1"
    assert dec["is_sub"] is True
    assert dec["row_path"] == "/weird/path|with|pipes/x.jsonl"


# --------------------------------------------------------------------------
# end-to-end search over a synthetic store
# --------------------------------------------------------------------------
def _write_session(root_projects, slug, sid, entries):
    d = os.path.join(root_projects, slug)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, sid + ".jsonl")
    with open(fp, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return fp


def test_claude_find_end_to_end(monkeypatch_env=None):
    if not shutil.which("rg"):
        print("SKIP: ripgrep not installed")
        return
    home = tempfile.mkdtemp()
    old_home, old_cfg = mod.HOME, os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        mod.HOME = home
        cfg = os.path.join(home, ".claude")
        os.environ["CLAUDE_CONFIG_DIR"] = cfg
        projects = os.path.join(cfg, "projects")
        _write_session(projects, "-tmp-proj", "sid-user", [
            {"type": "user", "cwd": "/tmp/proj", "timestamp": "2026-06-01T00:00:00Z",
             "message": {"role": "user", "content": "please WIDGETIZE the thing"}},
        ])
        _write_session(projects, "-tmp-proj", "sid-ai", [
            {"type": "assistant", "cwd": "/tmp/proj", "timestamp": "2026-06-02T00:00:00Z",
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "I will WIDGETIZE it"}]}},
        ])

        # full search finds both the user phrase and the assistant phrase
        rows = mod.claude_find("WIDGETIZE")
        sids = {r["sid"] for r in rows}
        assert "sid-user" in sids and "sid-ai" in sids, sids

        # user-only search finds only the session where the USER typed it
        rows_u = mod.claude_find("WIDGETIZE", user_only=True)
        sids_u = {r["sid"] for r in rows_u}
        assert sids_u == {"sid-user"}, sids_u
    finally:
        mod.HOME = old_home
        if old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg
        shutil.rmtree(home, ignore_errors=True)


# --------------------------------------------------------------------------
# branch / fork creation
# --------------------------------------------------------------------------
def test_branch_claude_session_boundary_and_forkmarker():
    home = tempfile.mkdtemp()
    try:
        proj = os.path.join(home, "projects", "-slug")
        os.makedirs(proj)
        parent = os.path.join(proj, "parent.jsonl")
        entries = [
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "q1"}},
            {"type": "assistant", "uuid": "a1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "ans1"}]}},
            {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "q2"}},
            {"type": "assistant", "uuid": "a2",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "ans2"}]}},
        ]
        with open(parent, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        # branch at u1 → should include u1 + a1 but stop before u2
        new_sid, new_path = mod.branch_claude_session(parent, "parent", "u1")
        with open(new_path) as f:
            copied = [json.loads(ln) for ln in f if ln.strip()]
        uuids = [c["uuid"] for c in copied]
        assert uuids == ["u1", "a1"], uuids
        assert all(c["sessionId"] == new_sid for c in copied)
        assert all(c["forkedFrom"] == {"sessionId": "parent", "messageUuid": "u1"}
                   for c in copied)
        # branch file lives beside its parent → same account/config dir
        assert os.path.dirname(new_path) == os.path.dirname(parent)
    finally:
        shutil.rmtree(home, ignore_errors=True)


# --------------------------------------------------------------------------
# timestamp + path helpers
# --------------------------------------------------------------------------
def test_ts_key_handles_iso_epoch_and_garbage():
    iso = mod.ts_key("2026-06-01T00:00:00Z")
    ms = mod.ts_key(1_700_000_000_000)   # epoch millis
    sec = mod.ts_key(1_700_000_000)      # epoch seconds
    assert iso > 0 and ms > 0
    assert abs(ms - sec) < 1.0           # both normalize to ~same instant
    assert mod.ts_key("not a date") == 0.0
    assert mod.ts_key(None) == 0.0


def test_fmt_ts_and_abbrev():
    import re
    assert mod.fmt_ts("") == ""
    # rendered in local tz as "YY-MM-DD HH:MM" (2-digit year via %y)
    assert re.fullmatch(r"\d\d-\d\d-\d\d \d\d:\d\d",
                        mod.fmt_ts("2026-06-01T12:00:00Z"))
    home = mod.HOME
    assert mod.abbrev(os.path.join(home, "code/x")) == "~/code/x"
    assert mod.abbrev("/elsewhere/y") == "/elsewhere/y"
    assert mod.abbrev(None) == "?"


# --------------------------------------------------------------------------
# Hermes extraction (multi-agent search)
# --------------------------------------------------------------------------
def test_hermes_text_extraction_user_only():
    d = {"title": "Session Title", "messages": [
        {"role": "user", "content": "user line"},
        {"role": "assistant", "content": [{"type": "text", "text": "assistant line"}]},
    ]}
    full = mod._hermes_text_from_json(d, user_only=False)
    assert "user line" in full and "assistant line" in full and "Session Title" in full
    only = mod._hermes_text_from_json(d, user_only=True)
    assert "user line" in only
    assert "assistant line" not in only and "Session Title" not in only


# --------------------------------------------------------------------------
# ANSI-aware width / alignment
# --------------------------------------------------------------------------
def test_vlen_ignores_ansi_and_align_right_pads():
    colored = mod.YELLOW + "abc" + mod.RESET
    assert mod._vlen(colored) == 3
    out = mod.align_right("left", "12:00")
    assert out.endswith("12:00" + mod.RESET)
    assert "left" in out


# --------------------------------------------------------------------------
# standalone runner
# --------------------------------------------------------------------------
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
