# claude-grep

> **Search your Claude Code conversation history** — find any past session by what was *said*, then jump straight back into it. `grep` + `fzf` for everything Claude Code has ever told you.

`claude-grep` is a tiny CLI (run with the `ccfind` command) that searches the text of every Claude Code conversation stored under `~/.claude/projects/`, and lets you resume any of them with a single keypress.

## Demo

![claude-grep — live content search over your Claude Code conversation transcripts](demo.png)

## Why

- `claude --resume` only matches a session's **title** (its first prompt) — not what was actually discussed *inside* it.
- Your history is also **fragmented per working directory**, so each `pwd` shows a different partial list and older work is hard to find and continue.

`claude-grep` searches the full transcript body across **every** project, so you can find a conversation by something that came up halfway through it — and drop right back into it.

## Install

### Homebrew (recommended)

```sh
brew install coolcorexix/tap/claude-grep
```

Then run `ccfind`. (You'll also need Anthropic's `claude` CLI on your `PATH`.)

### Nix

```sh
nix run github:coolcorexix/claude-grep
```

Or `nix profile install github:coolcorexix/claude-grep`, or add it to your flake. (`fzf`, `ripgrep`, and Python are bundled in.)

### Manual

Requirements: [`fzf`](https://github.com/junegunn/fzf) ≥ 0.38, [`ripgrep`](https://github.com/BurntSushi/ripgrep), Python 3.8+, and the `claude` CLI on your `PATH`.

```sh
# macOS dependencies
brew install fzf ripgrep

git clone https://github.com/coolcorexix/claude-grep
ln -sf "$PWD/claude-grep/ccfind" ~/.local/bin/ccfind   # ensure ~/.local/bin is on PATH
```

Then run `ccfind`. (Or just copy the `ccfind` script anywhere on your `PATH`.)

## Usage

```sh
ccfind
```

- **Type** a phrase — results refresh live as you type.
- **↑/↓** move · **Enter** resumes the selected conversation · **Esc** quits.
- Each row shows the matched snippet (phrase highlighted), the conversation's working directory (dimmed), and the message timestamp (right-aligned). The preview pane shows the session id, directory, and time.

## How it works

- **Fast:** `ripgrep` narrows thousands of transcripts to candidates in milliseconds; only those files are parsed in Python for clean, highlighted snippets. No index, nothing running in the background.
- **Searches real content:** your prompts, Claude's replies, and tool results — matches *inside* a tool call (e.g. a shell command) still surface via a raw fallback.
- **Sub-agent aware:** sub-agent transcripts (`<project>/<PARENT-UUID>/subagents/agent-*.jsonl`) can't be resumed directly, so `claude-grep` resolves them to their **parent** session and resumes that — using the parent's working directory, so it works even if the sub-agent ran in a now-deleted git worktree.
- **Deduped:** one row per resumable conversation, newest first.

## FAQ

**How do I search my Claude Code conversation history?**
Run `claude-grep` (the `ccfind` command) and type any phrase. It searches across every transcript in `~/.claude/projects/` and lets you resume the matching session.

**Can I search past Claude Code sessions by content instead of just the title?**
Yes — that's the whole point. `claude --resume` only matches the session title / first prompt; `claude-grep` searches the entire transcript body.

**How do I find and reopen an old Claude Code conversation?**
Search for something you remember discussing, select the result, and press `Enter`. `claude-grep` `cd`s into that project and runs `claude --resume` on the correct session.

**Where does Claude Code store its conversations?**
As JSONL transcripts under `~/.claude/projects/<project>/<session-id>.jsonl` (sub-agent sessions live in a `subagents/` subfolder). `claude-grep` reads these directly — no export or setup needed.

**Does it work with sub-agent sessions?**
Yes. Sub-agent transcripts aren't resumable on their own, so `claude-grep` automatically resolves them to the resumable parent session.

## claude-grep vs. `claude --resume`

|                                              | `claude --resume`            | **claude-grep**            |
| -------------------------------------------- | ---------------------------- | -------------------------- |
| Matches on                                   | session title / first prompt | **full transcript content** |
| Scope                                        | current project only         | **all projects at once**   |
| Find by something said mid-conversation      | ✗                            | ✓                          |
| Resumes sub-agent sessions                   | ✗                            | ✓ (via parent)             |
| Live, as-you-type search                     | ✗                            | ✓                          |

## Limitations

- The right-aligned timestamp is sized to the terminal width at search time; resizing the terminal corrects on the next keystroke.
- Results are capped at the 80 most-recent matches per search to stay responsive.

## License

MIT — see [LICENSE](LICENSE).
