# Claude Tickets

A local app that works your Jira tickets with Claude Code. It picks up the open tickets assigned to you, lets you
start a run on any of them from a dashboard on your Mac, and follows each ticket through review: it notices when
the reviewer leaves feedback, lets you start the fix with one click, and keeps your ticket table up to date.

## What it does

1. **Picks up your tickets.** Every minute it asks Jira for your open tickets and shows them on the dashboard,
   grouped by status.
2. **Runs Claude Code on a ticket.** You click **Migrate**, **Fix now** or **Review**. A headless Claude Code
   session works the ticket in your project checkout, one ticket at a time, while you follow it in the ticket's
   chat. You can add notes while it works, stop it and resume it.
3. **Hands it to review.** When a migration is done, the run posts the ticket in the team review channel and
   moves it to In Review in Jira.
4. **Watches the review.** It reads the review threads. When the reviewer leaves feedback, the ticket moves to
   **Action needed** so you can start the fix. When you reply, it goes back to waiting on the reviewer.
5. **Keeps your table in sync.** Your Slack List of tickets gets the Jira status, PR, deadline, who it is waiting
   on and the next step.
6. **Reports your day.** A daily report of what you did, ready to paste.

## The parts

| Part | File | Job |
|---|---|---|
| Dashboard | `dashboard.py`, `dashboard.html`, `dashboard.css`, `dashboard.js` | the app you use, at http://127.0.0.1:8790. Only your own Mac can open it |
| Bridge | `bridge.py` | the background service: polls Jira, runs and queues the Claude Code sessions, listens to Slack, serves the dashboard |
| Feedback watch | `watch.py` | finds new reviewer feedback and keeps the Slack List in sync. Plain Python; it only asks Claude when a message is unclear |
| Add-on hook | `addon_hook.py` | passes the notes you type in the chat to a run while it works |
| Workspace settings | `workspace.py`, `workspace.json` | your Slack, Jira and GitHub IDs (`workspace.json` is yours and not in git) |

## The tools it uses

| Tool | What the app uses it for | What you set up |
|---|---|---|
| **macOS** | runs the bridge in the background (launchd) | a launchd job |
| **Python** | runs the bridge | Python 3.11+ with `slack_bolt` |
| **Claude Code** | does the work on each ticket | the CLI, signed in, with the Atlassian and Slack connectors |
| **Jira** | the source of your tickets; moves them between statuses | an API token |
| **GitHub** | shows each ticket's PRs and whether they can merge | the `gh` CLI, signed in |
| **Slack** | the review threads, your ticket table (a Slack List), avatars | a Slack app with a bot token and a user token |

**Your tokens never go in this folder.** They live in `~/.claude/secrets/`, one per file:

| File | Token | From |
|---|---|---|
| `jira-email` | your Atlassian account's email | Jira, step 4 |
| `jira-token` | Atlassian API token | Jira, step 4 |
| `slack-app-token` | app-level token (`xapp-…`) | Slack, step 5 |
| `slack-bot-token` | bot token (`xoxb-…`) | Slack, step 5 |
| `slack-user-token` | your user token (`xoxp-…`) | Slack, step 5 |

```bash
mkdir -p ~/.claude/secrets && chmod 700 ~/.claude/secrets
```

Write each file with an editor, not `echo` (which keeps the token in your shell history). When all five exist:

```bash
chmod 600 ~/.claude/secrets/*
```

---

## Setup

### 1. The code and Python

The code expects to live at `~/.claude/jira-automation/slack-bridge`:

```bash
mkdir -p ~/.claude/jira-automation
```

```bash
git clone https://github.com/<owner>/claude-tickets.git ~/.claude/jira-automation/slack-bridge
```

```bash
python3 -m venv ~/.venvs/slack-bridge
```

```bash
~/.venvs/slack-bridge/bin/pip install slack_bolt
```

### 2. Claude Code

The bridge starts `claude -p` sessions, so Claude Code must work on its own from the command line.

1. Install the CLI so it is at `~/.local/bin/claude` (the native installer puts it there):
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```
2. Run `claude` once and sign in.
3. Add the **Atlassian** and **Slack** connectors (MCP servers). The runs read Jira and your Slack List through them.
4. Runs use `--permission-mode auto`, so auto mode must be available on your account.
5. Open your project once with `claude` from its folder, so the project is trusted.

Check:

```bash
~/.local/bin/claude -p "say ok"
```

### 3. GitHub

The dashboard shows each ticket's main and staging PRs through the GitHub CLI, and the runs open PRs with it.

1. Install it: `brew install gh`.
2. Sign in with an account that can read your project repo: `gh auth login`.
3. Clone your project repo; its path goes into `workspace.json` as `github.local_checkout`.

Check:

```bash
gh pr list --repo <owner>/<repo> --limit 1
```

### 4. Jira

The bridge reads your open tickets (`assignee = currentUser()`), and the app moves tickets between statuses.

1. Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens.
2. Save it as `~/.claude/secrets/jira-token`, and your Atlassian email as `~/.claude/secrets/jira-email`.
3. Find your site's **cloud id**: open `https://<your-site>.atlassian.net/_edge/tenant_info` and copy `cloudId`.
4. Find the custom field that holds the main PR link (`customfield_…`, e.g. in an issue's JSON at
   `https://<your-site>.atlassian.net/rest/api/3/issue/<KEY>`). Jira may refuse In Review while it is empty.

### 5. Slack

Slack is where the review happens and where your ticket table lives. The bridge connects with **Socket Mode**
(an outgoing connection), so it needs no public URL.

**You need:**
- a **private channel** for the app's own messages (the report channel),
- the **team review channel** where the reviewer leaves feedback,
- a **Slack List** of your tickets with these columns: ticket, summary, status, kind, PR, PR state, deadline,
  waiting on, next step (plus the thread link).

**Create the app** at https://api.slack.com/apps → **Create New App** → From scratch:

1. **Socket Mode**: turn it on. Create an **app-level token** with `connections:write` → `slack-app-token`.
2. **Interactivity**: turn it on. No request URL is needed with Socket Mode.
3. **Bot Token Scopes** (OAuth & Permissions): `chat:write`, `commands`, `channels:history`, `groups:history`,
   `users:read`, `files:read`, `lists:read`, `lists:write`. The bot posts in the report channel and edits the List.
4. **User Token Scopes**: `channels:history`, `groups:history`, `chat:write`, `users:read`, `files:read`.
   The dashboard reads the review threads and posts your replies **as you**.
5. **Event Subscriptions**: turn them on. Under **Subscribe to events on behalf of users**, add
   `message.channels` and `message.groups`. A new message in a review thread then reaches the app at once.
6. **Slash Commands** (optional): `/migrate`, `/fix` and `/tickets` still work as a fallback to the dashboard.
7. **Install to Workspace**. Save the bot token (`xoxb-…`) as `slack-bot-token` and the user token (`xoxp-…`)
   as `slack-user-token`.
8. Invite the bot to your report channel: `/invite @YourApp`.

### 6. Your workspace IDs

```bash
cp ~/.claude/jira-automation/slack-bridge/workspace.example.json ~/.claude/jira-automation/slack-bridge/workspace.json
```

Fill it in. Git ignores it, so your IDs never get committed.

| Key | Where to find it |
|---|---|
| `slack.workspace` | the part before `.slack.com` in your Slack URL |
| `slack.owner_user` | your Slack profile → ⋮ → **Copy member ID**. Only this user can command the app |
| `slack.bot_user` | the app's bot user: open its profile → **Copy member ID** |
| `slack.report_channel` | your private channel: channel details → bottom of **About** |
| `slack.review_channel` | the team review channel, the same way |
| `slack.daily_report_channel` | the channel whose thread you paste the daily report into |
| `slack.ticket_list` | your Slack List: the `F…` id in its link |
| `slack.ticket_list_columns` | each column's `Col…` id (read them with `slackLists.items.list`). Keep `thread` as is |
| `reviewer.name` | the reviewer's name as you write it in messages (`Hello, @<name> -san`) |
| `reviewer.slack_user` | the reviewer's member ID |
| `reviewer.list_option` | the List's **Waiting on** option for the reviewer |
| `jira.site` | `https://<your-site>.atlassian.net` |
| `jira.cloud_id` | from step 4 |
| `jira.v2_pr_field` | from step 4 |
| `github.repo` | `owner/name` of your project repo |
| `github.local_checkout` | where you cloned the project, e.g. `~/code/project` |

### 7. Your run instructions

The runs follow two files that are **not in this repo**, because they describe one team's process. Write your own:

- `~/.claude/jira-automation/RUNBOOK.md`: how a run migrates, fixes and reviews a ticket, and what it reports.
  Every run is pointed at it.
- `~/.claude/jira-automation/feedback-watch-prompt.md`: what the watch asks Claude when a reviewer message is unclear.

### 8. Start it (macOS launchd)

Create `~/Library/LaunchAgents/local.claude-slack-bridge.plist`, replacing `YOU` with your macOS user name:

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

It starts at login and restarts itself if it stops.

### 9. Check that it works

- `tail -f ~/.claude/jira-automation/slack-bridge/bridge.log` shows the bridge connecting to Slack.
- Open http://127.0.0.1:8790. Your Jira tickets appear within a minute.
- The dashboard header shows the bridge, the Jira check and the feedback watch as healthy.

---

## Using it

Everything happens in the dashboard:

| Button | Does |
|---|---|
| **Migrate** | starts a run on the ticket (or queues it behind the running one) |
| **Fix now** | fixes the reviewer's latest feedback |
| **Review** | has Claude re-review the ticket's PR while it waits for the reviewer (report only, changes nothing) |
| **Stop** / **Resume** | stops the running session; resumes a stopped or failed one |
| **Chat** | talks to Claude about the ticket; while a run works, your message is passed to it as a note |
| **Thread** | opens the ticket's review thread; you can reply as yourself |
| **Models** | picks the model and effort for each kind of run |

Tabs: **Board** (tickets by status), **Table** (your Slack List) and **Report** (your day).

## Day to day

- **Restart** after changing `bridge.py`, only when nothing is running (a restart cuts off a run):
  ```bash
  launchctl kickstart -k gui/$(id -u)/local.claude-slack-bridge
  ```
- `dashboard.py`, `watch.py` and the dashboard files reload on their own.
- Runtime files (`jobs.json`, `runlog.json`, logs, `addons/`, `run_settings.json`, …) stay out of git.
- **Open in CLI** opens Terminal with an `njr <KEY>` command. `njr` is a personal shell helper and isn't in this
  repo; without it, that one button does nothing useful.

## Rules for changing the code

- Never hardcode an ID: add it to `workspace.json`, `workspace.example.json` and `workspace.py`.
- Never put a token in this folder; tokens stay in `~/.claude/secrets/`.
- CSS: one rule per selector. Edit the existing rule; don't add override blocks at the end.
