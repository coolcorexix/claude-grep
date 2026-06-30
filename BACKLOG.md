# ccfind backlog

Loose list of ideas to pick up later. Newest first.

---

## Restore disappeared Claude Desktop sessions

**Status:** investigated, not started · **Added:** 2026-06-25
**Refs:** issue [#26452](https://github.com/anthropics/claude-code/issues/26452) (Code tab), issue [#69663](https://github.com/anthropics/claude-code/issues/69663) (Cowork)

Big pain point for non-technical Desktop users: sessions vanish from the sidebar
after logout / restart / subscription-status change, even though the data is
still on disk. Goal is **restore** (make them visible/recoverable again), not
resume. Could ship as its own tool, not necessarily inside ccfind.

There are **two separate stores** (verified on this machine 2026-06-24):

| | Code tab (#26452) | Cowork / local-agent-mode (#69663) |
|---|---|---|
| Transcript | `~/.claude/projects/<slug>/<cliSessionId>.jsonl` — ccfind already reads | `…/Claude/local-agent-mode-sessions/<org>/<acct>/local_<uuid>/audit.jsonl` |
| Sidebar index | `…/Claude/claude-code-sessions/<org>/<acct>/local_*.json` (thin pointer w/ `cliSessionId`) | `local-agent-mode-sessions/<org>/<acct>/local_<uuid>.json` + **IndexedDB** |
| macOS data root | `~/Library/Application Support/Claude/` | same |

Common trigger for both: re-login / account-UUID change / subscription change
mints a fresh `<org>/<acct>` folder (or rebuilds the index) and the sidebar
points at an empty/incomplete set. Underlying transcripts are untouched.

### Feasibility verdict
- **Code tab — restore likely feasible.** Sidebar appears to read filesystem
  pointers. Restore = synthesize/copy `local_*.json` (schema known; small).
  On this machine: **4 sidebar pointers vs 97 real JSONL** → 93 hidden.
  **Open test:** write one synthetic pointer for an orphan, quit + relaunch
  Desktop, confirm the row appears. Converts "likely" → "proven."
- **Cowork — native re-surface NOT worth it.** Reporter of #69663 confirmed the
  Recents list is driven by **IndexedDB (LevelDB)**, not the filesystem `.json`.
  Repairing the `.json` won't resurface it; rebuilding LevelDB = opaque,
  version-fragile treadmill. **But content recovery IS feasible** —
  `.json` + `audit.jsonl` + `outputs/` are all plaintext and intact. Realistic
  Cowork play = export / viewer, not sidebar restore.

### Caveats
- `local_*.json` schema is undocumented & Anthropic-owned → can change between
  Desktop versions. Keep any writer defensive + backup-first + opt-in flag.
- Desktop can flush in-memory cache over external edits → must quit app fully
  before writing.
- Never touch `~/.claude/projects/`.

### Suggested first step
Code-tab `--dry-run` reporter: show the gap (e.g. "93 sessions hidden from the
Desktop sidebar") and exactly what it *would* write, before writing anything.
