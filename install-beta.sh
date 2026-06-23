#!/usr/bin/env bash
# One-line installer for the ccfind BETA channel.
#
#   curl -fsSL https://raw.githubusercontent.com/coolcorexix/claude-grep/multi-account-support/install-beta.sh | bash
#
# Installs a `ccfind-beta` command alongside (not replacing) any existing
# `ccfind`, so you can try in-progress features — multi-account search,
# resume-the-branch-you-searched-for, multi-word search — without touching your
# stable install. Re-run the same command to update. Uninstall:
#   rm -rf ~/.local/share/ccfind-beta ~/.local/bin/ccfind-beta
set -euo pipefail

REPO="${CCFIND_BETA_REPO:-https://github.com/coolcorexix/claude-grep}"
BRANCH="${CCFIND_BETA_BRANCH:-multi-account-support}"
DEST="${CCFIND_BETA_DIR:-$HOME/.local/share/ccfind-beta}"
BIN="${CCFIND_BIN:-$HOME/.local/bin}"

say() { printf '%s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { say "✗ ccfind-beta needs '$1' on PATH"; exit 1; }; }

say "Installing ccfind-beta ($BRANCH)…"
need git
need python3
command -v rg  >/dev/null 2>&1 || say "  note: ripgrep (rg) not found — needed for search:  brew install ripgrep"
command -v fzf >/dev/null 2>&1 || say "  note: fzf not found — needed for the UI:           brew install fzf"

rm -rf "$DEST"
git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$DEST"
mkdir -p "$BIN"
chmod +x "$DEST/ccfind"
ln -sf "$DEST/ccfind" "$BIN/ccfind-beta"

say "✓ installed: $BIN/ccfind-beta -> $DEST/ccfind"
case ":$PATH:" in
  *":$BIN:"*) say "✓ $BIN is on your PATH — run:  ccfind-beta" ;;
  *) say "⚠ $BIN is not on your PATH. Add it, then run ccfind-beta:";
     say "    echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.zshrc && exec zsh" ;;
esac
say "  update later: re-run this same command."
