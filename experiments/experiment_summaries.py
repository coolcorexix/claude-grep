#!/usr/bin/env python3
"""Compare zero/low-LLM summarization techniques on Claude Code session JSONLs.

Techniques (all run on every session):
  baseline_title    - first user prompt (proxy for what `claude --resume` displays)
  tier1_structural  - slot-filled template from JSONL signals (intent + files + cmds + outcome)
  lead_3            - first 3 user prompts concatenated (newspaper baseline)
  textrank_2        - sumy LexRank top 2 sentences
  yake_5            - YAKE top 5 keywords (statistical, no model)
  rake_5            - RAKE top 5 keyphrases (statistical, no model)

Outputs experiments/experiment_summaries.csv.
"""
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# Sample sessions — diverse mix of projects + sizes, taken from the most recent
# JSONLs (avoiding multi-megabyte transcripts so the script runs in seconds).
SAMPLE = [
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-nemo-lab-nosync-ccfind/6ba6625d-83f7-46b0-8324-566315f09f5e.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-nemo-lab-nosync-mjolnir/721ea8ea-1e3a-4360-b456-5aad9ab4edc6.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham/50869c4b-0f1f-4bd5-92f4-ac8d5d3b27e9.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-nemo-lab-nosync/c738d029-598d-4574-8da9-722afb3fe44a.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-saleshood-workdir-nosync-saleshood-saleshood-os/134a614a-0963-42b2-bb36-47e307138ed3.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-saleshood-workdir-nosync-saleshood-webapp/e1ed3fef-c8f1-4b63-a810-cbebcd06873e.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-saleshood-workdir-nosync-saleshood-saleshood-os/cdca9c9b-9a97-4809-80a0-ebd2bdf69698.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-saleshood-workdir-nosync-saleshood-saleshood-os/e9fb28ae-75a5-4b44-8079-2c6f08caa0b9.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham--claude-worktrees-zen-moser-287161/f5503e02-debe-4b17-8a0e-f7eb5249e6ab.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-saleshood-workdir-nosync-saleshood-saleshood-os/40e40009-7072-42fb-9e54-230bc3baa7cc.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-saleshood-workdir-nosync-saleshood-sh-ui-toolkit/48c681a5-c8c2-44f7-8a08-1a821f91f47e.jsonl",
    "/Users/huyphatpham/.claude/projects//-Users-huyphatpham-Documents-saleshood-workdir-nosync-saleshood-saleshood-os/d36837ca-a9e8-4eb1-8fa9-6518c43ae50d.jsonl",
]

SYS_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
CMD_TAG_RE = re.compile(r"<command-(?:name|message|args)>.*?</command-(?:name|message|args)>", re.DOTALL)


def clean_user(text):
    """Strip system-reminder / command-tag noise from a user message."""
    text = SYS_RE.sub("", text)
    text = CMD_TAG_RE.sub("", text)
    return text.strip()


def extract_signals(path):
    user_msgs, asst_texts, tool_calls = [], [], []
    cwd = None
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if cwd is None and isinstance(e.get("cwd"), str):
                cwd = e["cwd"]
            t = e.get("type")
            msg = e.get("message") or {}
            content = msg.get("content")
            if t == "user" and msg.get("role") == "user":
                if isinstance(content, str):
                    cleaned = clean_user(content)
                    if cleaned:
                        user_msgs.append(cleaned)
                elif isinstance(content, list):
                    # skip if any block is a tool_result; otherwise gather text blocks
                    if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                        continue
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c.get("text", ""))
                    cleaned = clean_user("\n".join(parts))
                    if cleaned:
                        user_msgs.append(cleaned)
            elif t == "assistant" and isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ct = c.get("type")
                    if ct == "text":
                        s = (c.get("text") or "").strip()
                        if s:
                            asst_texts.append(s)
                    elif ct == "tool_use":
                        tool_calls.append((c.get("name", ""), c.get("input") or {}))
    return {
        "user_msgs": user_msgs,
        "asst_texts": asst_texts,
        "tool_calls": tool_calls,
        "cwd": cwd,
    }


def baseline_title(sig):
    if not sig["user_msgs"]:
        return ""
    return sig["user_msgs"][0].split("\n")[0][:160]


def lead_n(sig, n=3):
    return " | ".join(m.split("\n")[0][:120] for m in sig["user_msgs"][:n])


def tier1_structural(sig):
    intent = (sig["user_msgs"][0].split("\n")[0] if sig["user_msgs"] else "")[:120]
    edited, cmds = [], []
    for name, inp in sig["tool_calls"]:
        if name in ("Edit", "Write", "NotebookEdit") and isinstance(inp.get("file_path"), str):
            edited.append(os.path.basename(inp["file_path"]))
        elif name == "Bash" and isinstance(inp.get("command"), str):
            tok = inp["command"].strip().split()
            if tok:
                head = tok[0].lstrip("(")
                # Walk past `cd path &&` style preludes
                if head == "cd" and len(tok) >= 3 and tok[2] in ("&&",):
                    head = tok[3] if len(tok) > 3 else head
                if head and head not in cmds and len(head) < 30:
                    cmds.append(head)
    outcome = ""
    if sig["asst_texts"]:
        outcome = sig["asst_texts"][-1].split("\n")[0][:100]
    turns = len(sig["user_msgs"])
    n_tools = len(sig["tool_calls"])
    parts = [f"INTENT: {intent}"]
    if edited:
        top = Counter(edited).most_common(5)
        parts.append("EDITED: " + ", ".join(f"{n}×{c}" if c > 1 else n for n, c in top))
    if cmds:
        parts.append("CMDS: " + ", ".join(cmds[:6]))
    parts.append(f"STATS: {turns}t/{n_tools}tc")
    if outcome:
        parts.append("LAST: " + outcome)
    return " | ".join(parts)


def textrank_summary(sig, n=2):
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.summarizers.lex_rank import LexRankSummarizer

    text = "\n".join(sig["user_msgs"] + sig["asst_texts"])
    if not text.strip():
        return ""
    parser = PlaintextParser.from_string(text, Tokenizer("english"))
    sents = LexRankSummarizer()(parser.document, n)
    return " ".join(str(s) for s in sents)[:300]


def yake_keywords(sig, n=5):
    import yake

    text = "\n".join(sig["user_msgs"] + sig["asst_texts"])
    if not text.strip():
        return ""
    kw = yake.KeywordExtractor(lan="en", n=2, top=n)
    return ", ".join(k for k, _ in kw.extract_keywords(text))


def rake_keywords(sig, n=5):
    from rake_nltk import Rake

    r = Rake(max_length=4)
    text = "\n".join(sig["user_msgs"] + sig["asst_texts"])
    if not text.strip():
        return ""
    r.extract_keywords_from_text(text)
    return ", ".join(r.get_ranked_phrases()[:n])


def main():
    sessions = sys.argv[1:] or SAMPLE
    rows = []
    for p in sessions:
        sid = Path(p).stem
        try:
            sig = extract_signals(p)
        except Exception as e:
            print(f"✗ {sid}: extract failed: {e}", file=sys.stderr)
            continue
        proj_full = sig["cwd"] or os.path.basename(os.path.dirname(p))
        proj = os.path.basename(proj_full.rstrip("/")) or proj_full

        row = {
            "session_id": sid[:8],
            "project": proj,
            "size_kb": round(os.path.getsize(p) / 1024, 1),
            "user_turns": len(sig["user_msgs"]),
            "tool_calls": len(sig["tool_calls"]),
            "baseline_title": baseline_title(sig),
            "tier1_structural": tier1_structural(sig),
            "lead_3": lead_n(sig, 3),
        }
        for label, fn in (("textrank_2", textrank_summary),
                          ("yake_5", yake_keywords),
                          ("rake_5", rake_keywords)):
            try:
                row[label] = fn(sig)
            except Exception as e:
                row[label] = f"<ERROR: {type(e).__name__}: {e}>"
        rows.append(row)
        print(f"✓ {sid[:8]:8s} {proj:40s} {row['user_turns']:3d}t/{row['tool_calls']:4d}tc",
              file=sys.stderr)

    out = Path(__file__).parent / "experiment_summaries.csv"
    cols = ["session_id", "project", "size_kb", "user_turns", "tool_calls",
            "baseline_title", "tier1_structural", "lead_3",
            "textrank_2", "yake_5", "rake_5"]
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {out} ({len(rows)} rows)", file=sys.stderr)


if __name__ == "__main__":
    main()
