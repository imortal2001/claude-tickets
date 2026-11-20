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
3. **Hands it to review.** When a migration is done, the run moves the ticket to In Review in Jira, and the app
   posts it in the team review channel with a "ready for review" reply.
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
| `slack.*_channel_name` (optional) | the three channels' names, shown in messages (`my-reports` → "#my-reports"); without them the app says "the report channel" and so on |
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

## The buttons

Every action starts from the dashboard. A card only shows the buttons that make sense for the ticket's state.
Before a run starts, a confirm dialog shows the model and effort it will use; a change there applies to that run only.

What each run does comes from **your** `RUNBOOK.md` (setup step 7). The facts below are what the runbook this app
was built with tells each run to do, so you can see what to write in yours.

**Rules every run follows**
- It never stops to ask. Each decision takes the recommended option and is written into the PR record, so you or
  the reviewer can overturn it.
- It never merges into `main` and never force-pushes. It opens the staging PR but never merges it; you merge and deploy.
- It moves Jira only along the allowed transitions, and never to For Release or Closed.
- It never touches a branch that another live Claude session is using. It stashes your uncommitted changes
  and puts them back when it's done.
- A page that would need a **new API** is not built: it's reported instead, and the ticket stays OPEN.
- It doesn't post in team threads. The only team-channel posts are the app's, after a migration: the ticket's
  Jira link and the "ready for review" reply in its thread.

### Migrate →
**Shows on:** an OPEN ticket with no unfinished run.

1. **Investigates first.** It reads the ticket, the legacy source and the tracking sheet, and writes a brief.
2. **Decides go or no-go.** If the ticket needs a new API, it's a no-go: Jira stays OPEN, and the reason goes in the chat.
   On a go, Jira moves **OPEN → Dev In Progress**.
3. **Posts a plan report** in the ticket's chat: the kind of ticket (frontend page, middleware, API), the routes,
   the APIs, the legacy source, the decisions it took and what to watch out for. The table row becomes
   Waiting on = You, Next step = "Migrating…".
4. **Builds it** with the project's own migration command for that kind of ticket.
5. **Reviews it** with the project's reviewers and checks (visual comparison, tests, lint). It fixes what the
   review finds and reviews again, at most 3 rounds. Only a clean review moves on.
6. **Opens a draft PR** to `main`, then a **staging PR** with the same commits. It never merges either.
7. **Moves Jira to In Review.** If Jira's PR-link field is empty, it fills it with the main PR's link first.
8. **Reports** "<KEY> is migrated" with the routes or summary. The app then posts the ticket's bare Jira link in
   the team review channel (Slack's Jira app adds the ticket card, as when a teammate posts it), replies in that
   thread asking the reviewer to review (with the main and staging PR links), and the ticket moves to
   **Waiting for review**. If either post fails, the run's chat says so, and you can reply from the thread panel.

### Fix Now →
**Shows on:** a ticket with new reviewer feedback (it's under **Action needed**), or one that is Dev In Progress
with nothing waiting on you.

1. **Finds every unanswered feedback message** in the review thread, oldest first; there can be more than one round.
   It reads an attached report through a helper agent, item by item.
2. **Moves Jira In Review → Dev In Progress** right away.
3. **Works on the PR's branch.** Each "fix required" item gets reproduced, fixed, and covered by a test.
   Items marked "no change" or "approved" are left alone.
4. **Reviews it again** until it's clean, at most 3 rounds.
5. **Records the round** in the PR record and commits it as `[<KEY>] Review round <N>: …` (no force-push).
6. **Updates the staging PR.** It reuses the open one if there is one; otherwise it opens a new one. It never merges it.
7. **Moves Jira to In Review** and reports "<KEY> is fixed" with the staging PR. The table row becomes
   Waiting on = You, Next step = "merge + deploy, then reply in the thread".

You then merge and deploy staging and reply in the thread. The watch sees your reply and sets Waiting on back to the reviewer.

### Review →
**Shows on:** a ticket that is In Review, waiting for the reviewer.

- **Re-reviews the PR as it is now**, report only, in a separate worktree. It runs the same checks a migration's
  review uses. Full reports are saved under `reviews/<KEY>/` and never committed.
- **Changes nothing:** no code, git, Jira or Slack.
- **Clean:** the ticket keeps waiting on the reviewer.
- **Findings:** the app moves Jira back to Dev In Progress and sets Waiting on = You. The report ends with two options:
  answer **"fix them now"** in the chat to run a fix round, or **"leave it"**.

### Resume →
**Shows on:** a ticket whose last run was **stopped** or **failed** (you pressed Stop, the app closed, or there was an error).

- Continues the **same session**, so it keeps everything it already knew and did. It's told what was interrupted,
  re-runs whatever didn't finish, and ends with the normal final report.
- A run that ends early without its final report is resumed automatically, up to 2 times. Resume is only for runs that were cut off.

### Stop / Remove from queue
**Shows on:** a running ticket (**Stop**) or a queued one (**Remove from queue**).

- **Stop** ends the current turn; the session is kept, so **Resume** can continue it. A very long session is
  compacted on stop, so the next turn costs less.
- Notes you sent to a stopped run are kept and delivered when you resume it.

### Watch live / History
**Shows on:** a running ticket (**Watch live**) or one with an earlier run (**History**).

- Opens the session's transcript in the side panel: every step, tool call and result, live while it runs.

### Chat
**Shows on:** every ticket except an OPEN one that has never had a run.

- **While a run works:** your message is passed to the run as a note after its next step, without stopping it.
  The chat shows ✓ delivered when it arrives.
- **Otherwise:** it's a conversation about the ticket. Claude answers or does what you ask, following the same
  runbook rules, and doesn't post to Slack itself. Ready-made prompts: **Status summary** and **Explain the report**.

### Review thread
**Opens from:** the card's review line.

- Shows the ticket's team review thread with avatars and attached reports (HTML reports open in the app).
- The reply box posts **as you**, after a confirm. `:emoji:` names autocomplete, and `@<reviewer>` becomes a real mention.
  **Open in Slack** jumps to the thread.

### Models ▾
**In the header.**

- Sets the model and effort for each kind of run: **Migrate**, **Fix Now**, **Review** and **Watch** (the feedback
  watch's rare Claude question). Helper agents inside a run use a lighter model for routine reading.

### What happens without a button
- **New tickets** appear within a minute of being assigned to you in Jira.
- **Reviewer feedback** moves the ticket to **Action needed** within moments of being posted (Slack events,
  with a 5-minute check as a safety net).
- **The table** (your Slack List) follows Jira: new rows, status, PR state, deadline, who it's waiting on.
- **Report tab:** your day's work, counting only what you did, ready to paste.

Tabs: **Board** (tickets by status), **Table** (your Slack List) and **Report** (your day).

## Day to day

- **Restart** after changing `bridge.py`, only when nothing is running (a restart cuts off a run):
  ```bash
  launchctl kickstart -k gui/$(id -u)/local.claude-slack-bridge
  ```
- `dashboard.py`, `watch.py` and the dashboard files reload on their own. `workspace.json` is read once at start:
  restart the bridge after editing it. A missing key stops the bridge with a message naming it (`launchd.err.log`).
- Runtime files (`jobs.json`, `runlog.json`, logs, `addons/`, `run_settings.json`, …) stay out of git.

## Rules for changing the code

- Never hardcode an ID: add it to `workspace.json`, `workspace.example.json` and `workspace.py`.
- Never put a token in this folder; tokens stay in `~/.claude/secrets/`.
- CSS: one rule per selector. Edit the existing rule; don't add override blocks at the end.
