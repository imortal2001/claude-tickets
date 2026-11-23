#!/bin/bash
# Claude Tickets installer for macOS. Safe to run again: it never overwrites your settings, tokens or launchd job.
#
#   ./install.sh               set up everything, then start the bridge if your settings and tokens are in place
#   ./install.sh --no-start    set up everything, but don't start the bridge
#   ./install.sh --no-app      skip building the Claude Tickets window app
#
# What it does: checks the tools it needs, makes the Python environment, copies the example settings, makes the
# tokens folder, writes the launchd job, builds Claude Tickets.app into ~/Applications, and lists what is left
# for you to fill in.
set -e

START=1; APP=1
for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    --no-app) APP=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"
WANT="$HOME/.claude/jira-automation/slack-bridge"
VENV="$HOME/.venvs/slack-bridge"
SECRETS="$HOME/.claude/secrets"
PLIST="$HOME/Library/LaunchAgents/local.claude-slack-bridge.plist"
todo=()
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

echo "Claude Tickets — install"

# 1. Where it lives: the code reads its runbook and state next to this folder, so the clone must sit here.
if [ "$HERE" != "$WANT" ]; then
  echo "  This folder must be $WANT (it is $HERE)."
  echo "  Move it there, e.g.:  mkdir -p ~/.claude/jira-automation && mv \"$HERE\" \"$WANT\""
  exit 1
fi
ok "folder: $WANT"

# 2. Tools.
[ "$(uname)" = "Darwin" ] || { echo "  macOS only."; exit 1; }
command -v python3 >/dev/null || { echo "  python3 not found: install it (https://www.python.org or brew install python)"; exit 1; }
if [ -x "$HOME/.local/bin/claude" ]; then ok "Claude Code: ~/.local/bin/claude"
else warn "Claude Code not found at ~/.local/bin/claude"; todo+=("Install Claude Code: curl -fsSL https://claude.ai/install.sh | bash, then run 'claude' once to sign in"); fi
if command -v gh >/dev/null; then ok "GitHub CLI: $(command -v gh)"
else warn "GitHub CLI (gh) not found"; todo+=("Install the GitHub CLI and sign in: brew install gh && gh auth login"); fi

# 3. Python environment.
if [ ! -x "$VENV/bin/python" ]; then python3 -m venv "$VENV"; fi
"$VENV/bin/python" -c "import slack_bolt" 2>/dev/null || "$VENV/bin/pip" install --quiet --disable-pip-version-check slack_bolt
ok "Python environment: $VENV (slack_bolt)"

# 4. Settings: copied from the examples once, then yours.
for f in workspace workflow; do
  if [ -f "$HERE/$f.json" ]; then ok "$f.json (yours, kept)"
  else cp "$HERE/$f.example.json" "$HERE/$f.json"; warn "$f.json created from the example"
    if [ "$f" = workspace ]; then todo+=("Fill in workspace.json: your Slack, Jira and GitHub IDs and the reviewer (README step 6)")
    else todo+=("Edit workflow.json: your Jira status names, report words, Slack List options (README, 'Set it up for your team')"); fi
  fi
done
mkdir -p "$HERE/prompts.local"
ok "prompts.local/ (your own wording for any prompt; empty = the defaults in prompts/)"

# 5. Run instructions next to the code.
for f in RUNBOOK.md feedback-watch-prompt.md; do
  if [ -f "$HOME/.claude/jira-automation/$f" ]; then ok "$f"
  else warn "$f missing"; todo+=("Write ~/.claude/jira-automation/$f (README, 'Set it up for your team')"); fi
done

# 6. Tokens.
mkdir -p "$SECRETS" && chmod 700 "$SECRETS"
missing=()
for f in slack-app-token slack-bot-token slack-user-token jira-email jira-token; do
  [ -s "$SECRETS/$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -eq 0 ]; then chmod 600 "$SECRETS"/*; ok "tokens: all 5 in ~/.claude/secrets"
else warn "tokens missing: ${missing[*]}"; todo+=("Save these in ~/.claude/secrets/ (one per file, with an editor): ${missing[*]}"); fi

# 7. The launchd job that keeps the bridge running.
if [ -f "$PLIST" ]; then ok "launchd job (kept): $PLIST"
else
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.claude-slack-bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$HERE/bridge.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$HERE/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$HERE/launchd.err.log</string>
</dict>
</plist>
PLISTEOF
  ok "launchd job written: $PLIST"
fi

# 8. The window app.
if [ $APP -eq 1 ]; then
  if "$HERE/app/build.sh" >/dev/null 2>&1; then ok "Claude Tickets.app built in ~/Applications"
  else warn "the window app did not build (needs Xcode's command line tools: xcode-select --install)"
    todo+=("Build the window app: xcode-select --install, then app/build.sh (or use http://127.0.0.1:8790 in a browser)"); fi
fi

# 9. Start, or say what is left.
echo
if [ ${#todo[@]} -gt 0 ]; then
  echo "Left for you:"
  for t in "${todo[@]}"; do echo "  • $t"; done
  echo
  echo "Then run ./install.sh again: it starts the bridge once everything is in place."
elif [ $START -eq 0 ]; then
  echo "Everything is in place. Start it with: launchctl bootstrap gui/$(id -u) $PLIST"
elif launchctl print "gui/$(id -u)/local.claude-slack-bridge" >/dev/null 2>&1; then
  echo "The bridge is already running (restart it with: launchctl kickstart -k gui/$(id -u)/local.claude-slack-bridge)."
else
  launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "Started. Open Claude Tickets (or http://127.0.0.1:8790)."
fi
