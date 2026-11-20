# Claude Tickets — Slack bridge and dashboard

Runs Claude Code on your Jira tickets from Slack, and shows them on a local dashboard.

- **Slack bridge** (`bridge.py`): type `/migrate NJ-12345` or `/fix NJ-12345` in Slack, and a headless
  `claude -p` session works the ticket on your Mac. One ticket runs at a time; the rest queue.
  It uses Socket Mode, so it needs no public URL.
- **Dashboard** (`dashboard.py` + `dashboard.html` / `.css` / `.js`): http://127.0.0.1:8790, served by the
  bridge. It shows your tickets by status, runs, review threads, a chat with Claude per ticket, a table
  view of your Slack List and a daily report. It only accepts connections from your own Mac.
- **Feedback watch** (`watch.py`): plain Python that checks the review threads for new reviewer feedback
  and keeps the Slack List in sync with Jira. It only asks Claude when a message is unclear.
- **Add-on hook** (`addon_hook.py`): passes notes you type in the chat to a run while it works.

It runs on **macOS** (launchd, Terminal) and was built for one person's workflow. It's not a general tool.

---

## What you need

| | |
|---|---|
| macOS | the bridge runs as a launchd job |
| Python 3.11+ | with `slack_bolt` (tested with 3.15, slack_bolt 1.30) |
| Claude Code CLI | at `~/.local/bin/claude`, signed in |
| GitHub CLI (`gh`) | signed in to an account that can read your project repo |
| A Slack app | you create it (steps below) |
| Jira Cloud | an API token for your account |
| Your project checkout | the repo the tickets are about |

Claude Code also needs the **Atlassian** and **Slack** connectors, because the prompts read Jira and the Slack List
through them.

---

## 1. Put the code in place

The code expects to live at `~/.claude/jira-automation/slack-bridge`:

```bash
mkdir -p ~/.claude/jira-automation
git clone https://github.com/<owner>/claude-tickets.git ~/.claude/jira-automation/slack-bridge
```

```bash
python3 -m venv ~/.venvs/slack-bridge
```

```bash
~/.venvs/slack-bridge/bin/pip install slack_bolt
```

## 2. Create the Slack app

At https://api.slack.com/apps → **Create New App** → From scratch.

1. **Socket Mode**: turn it on. Create an **app-level token** with `connections:write` (`xapp-…`).
2. **Slash Commands**: add `/migrate`, `/fix` and `/tickets`.
3. **Interactivity**: turn it on (the bridge has buttons). No request URL is needed with Socket Mode.
4. **OAuth & Permissions → Bot Token Scopes**: `chat:write`, `commands`, `channels:history`,
   `groups:history`, `users:read`, `files:read`, `lists:read`, `lists:write`.
5. **OAuth & Permissions → User Token Scopes** (the dashboard reads the review threads and replies as you):
   `channels:history`, `groups:history`, `chat:write`, `users:read`, `files:read`.
6. **Event Subscriptions**: turn them on. Under **Subscribe to events on behalf of users**, add
   `message.channels` and `message.groups`. New review-thread messages then reach the bridge at once.
7. **Install to Workspace**. Copy the **Bot User OAuth Token** (`xoxb-…`) and the **User OAuth Token** (`xoxp-…`).
8. Invite the bot to your private report channel (`/invite @YourApp`).

## 3. Save your tokens

Tokens live only in `~/.claude/secrets/`, one per file, never in this folder:

```bash
mkdir -p ~/.claude/secrets && chmod 700 ~/.claude/secrets
```

| File | Contains |
|---|---|
| `slack-app-token` | the app-level token (`xapp-…`) |
| `slack-bot-token` | the bot token (`xoxb-…`) |
| `slack-user-token` | your user token (`xoxp-…`) |
| `jira-email` | the email of your Atlassian account |
| `jira-token` | an Atlassian API token (https://id.atlassian.com/manage-profile/security/api-tokens) |

Write each one with your editor (not `echo`, which leaves the token in your shell history), then:

```bash
chmod 600 ~/.claude/secrets/*
```

## 4. Fill in your workspace

```bash
cp ~/.claude/jira-automation/slack-bridge/workspace.example.json ~/.claude/jira-automation/slack-bridge/workspace.json
```

Then edit `workspace.json`. Git ignores it, so your IDs never get committed.

| Key | Where to find it |
|---|---|
| `slack.workspace` | the part before `.slack.com` in your Slack URL |
| `slack.owner_user` | your Slack profile → ⋮ → **Copy member ID**. Only this user can command the bridge |
| `slack.bot_user` | the app's bot user ID (open the bot's profile → **Copy member ID**) |
| `slack.report_channel` | your private channel for runs: channel details → bottom of the **About** tab |
| `slack.review_channel` | the team channel where reviews happen |
| `slack.daily_report_channel` | the channel whose thread you paste the daily report into |
| `slack.ticket_list` | your Slack List of tickets: open it, the `F…` id is in its link |
| `slack.ticket_list_columns` | each column's `Col…` id: read them with `slackLists.items.list`. Keep `thread` as is |
| `reviewer.name` | the reviewer's name as you write it in messages (`Hello, @<name> -san`) |
| `reviewer.slack_user` | the reviewer's member ID |
| `reviewer.list_option` | the List's **Waiting on** option id for the reviewer |
| `jira.site` | e.g. `https://your-site.atlassian.net` |
| `jira.cloud_id` | `https://your-site.atlassian.net/_edge/tenant_info` → `cloudId` |
| `jira.v2_pr_field` | the custom field that holds the main PR link (`customfield_…`) |
| `github.repo` | `owner/name` of your project repo |
| `github.local_checkout` | where you cloned the project, e.g. `~/code/project` |

The List is expected to have these columns: ticket, summary, status, kind, pr, pr_state, deadline, waiting, next
(and the thread link).

## 5. Add the run instructions

The runs follow two files that are **not in this repo**, because they describe one team's process. Write your own:

- `~/.claude/jira-automation/RUNBOOK.md`: how a run migrates, fixes and reviews a ticket, and what it reports.
  Every prompt points the session at it.
- `~/.claude/jira-automation/feedback-watch-prompt.md`: what the feedback watch asks Claude when a reviewer
  message is unclear.

## 6. Start it with launchd

Create `~/Library/LaunchAgents/local.claude-slack-bridge.plist` (replace `YOU` with your macOS user name):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.claude-slack-bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.venvs/slack-bridge/bin/python</string>
    <string>/Users/YOU/.claude/jira-automation/slack-bridge/bridge.py</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/YOU/.claude/jira-automation/slack-bridge</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/Users/YOU/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/Users/YOU/.claude/jira-automation/slack-bridge/launchd.out.log</string>
  <key>StandardErrorPath</key><string>/Users/YOU/.claude/jira-automation/slack-bridge/launchd.err.log</string>
</dict>
</plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.claude-slack-bridge.plist
```

## 7. Check that it works

- `tail -f ~/.claude/jira-automation/slack-bridge/bridge.log` shows the bridge connecting.
- Open http://127.0.0.1:8790. Your tickets should appear within a minute.
- In Slack, `/migrate status` answers with what is running and queued.

---

## Using it

| In Slack | Does |
|---|---|
| `/migrate NJ-12345` | starts (or queues) a run on the ticket |
| `/fix NJ-12345 [link]` | fixes the reviewer's latest feedback (the attached report, or the link you give) |
| `/migrate status` | what is running and queued |
| `/migrate stop` | stops the running session's current turn |
| `/tickets` | a status summary of your open tickets |

The dashboard has the same buttons (Migrate, Fix now, Review, Stop, Resume), plus a per-ticket chat, the review
threads and a **Models** menu to pick the model and effort for each kind of run.

## Day to day

- **Restart** after changing `bridge.py` (only when nothing is running, or the run is cut off):
  `launchctl kickstart -k gui/$(id -u)/local.claude-slack-bridge`
- `dashboard.py`, `watch.py` and the dashboard files reload on their own; no restart needed.
- Runtime files (`jobs.json`, `runlog.json`, logs, `addons/`, `run_settings.json`, …) are git-ignored.

## Rules for changing the code

- Never hardcode an ID: add it to `workspace.json`, `workspace.example.json` and `workspace.py`.
- Never put a token in this folder; tokens stay in `~/.claude/secrets/`.
- CSS: one rule per selector. Edit the existing rule; don't add override blocks at the end.
