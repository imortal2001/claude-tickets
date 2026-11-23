"""Workspace settings: the Slack, Jira and GitHub IDs this bridge works against.

They live in workspace.json next to this file, which git does not track; workspace.example.json shows its shape.
Someone setting up their own copy fills workspace.json with their own workspace's IDs. Tokens never go here: they
stay in ~/.claude/secrets.

The file is read once, when the bridge starts: after editing it, restart the bridge (the live reload of
dashboard.py / watch.py reuses these values).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FILE = Path(__file__).resolve().parent / "workspace.json"

if not FILE.exists():
    raise SystemExit(f"Missing {FILE}: copy workspace.example.json to workspace.json and fill in your workspace's IDs.")
_c = json.loads(FILE.read_text(encoding="utf-8"))


def _get(path: str):
    """A required value, e.g. "slack.owner_user". A missing one stops the bridge with a message naming it."""
    node = _c
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise SystemExit(f'{FILE} has no "{path}": add it (workspace.example.json shows every key).')
        node = node[part]
    return node


def _channel_name(path: str, fallback: str) -> str:
    """An optional channel name for messages, shown as "#name"; without one, a neutral description."""
    name = str((_c.get("slack") or {}).get(path) or "").strip().lstrip("#")
    return f"#{name}" if name else fallback


SLACK_URL = f"https://{_get('slack.workspace')}.slack.com"
SLACK_URL_RE = re.escape(SLACK_URL)
OWNER = _get("slack.owner_user")                     # the only Slack user allowed to command the bridge
BOT_USER = _get("slack.bot_user")                    # the bridge's own Slack app user
REPORT_CHANNEL = _get("slack.report_channel")        # private channel where runs and chats live
REVIEW_CHANNEL = _get("slack.review_channel")        # team channel with the review threads
DAILY_CHANNEL = _get("slack.daily_report_channel")   # channel whose thread the daily report is pasted into
LIST_ID = _get("slack.ticket_list")                  # Slack List "My Jira tickets"
LIST_COLUMNS = _get("slack.ticket_list_columns")     # our column name -> the List's column id

REPORT_NAME = _channel_name("report_channel_name", "the report channel")
REVIEW_NAME = _channel_name("review_channel_name", "the review channel")
DAILY_NAME = _channel_name("daily_report_channel_name", "the daily report channel")

REVIEWER = _get("reviewer.name")                     # how the reviewer is called in messages ("Hello, @<name> -san")
REVIEWER_USER = _get("reviewer.slack_user")
REVIEWER_OPTION = _get("reviewer.list_option")       # the List's "Waiting on" option for the reviewer
HONORIFIC = str((_c.get("reviewer") or {}).get("honorific") or "")   # optional, e.g. "-san"; "" = just the name
REVIEWER_CALLED = f"{REVIEWER}{HONORIFIC}"            # in plain text: "Tanaka-san"


def reviewer_mention(at: str) -> str:
    """The reviewer addressed in a message: "<@U…>" or "@Name", with the honorific after a space ("<@U…> -san")."""
    return f"{at} {HONORIFIC}".rstrip()


JIRA_SITE = _get("jira.site").rstrip("/")
JIRA_CLOUD_ID = _get("jira.cloud_id")
JIRA_V2_PR_FIELD = _get("jira.v2_pr_field")          # custom field holding the main PR URL

GH_REPO = _get("github.repo")                        # owner/name
REPO = Path(_get("github.local_checkout")).expanduser()
REPO_NAME = REPO.name

# What the dashboard's script needs; nothing else leaves the process.
PUBLIC = {"jira": JIRA_SITE, "report": REPORT_CHANNEL, "review": REVIEW_CHANNEL, "reviewer": REVIEWER,
          "reviewerOption": REVIEWER_OPTION, "reviewerCalled": REVIEWER_CALLED, "reportName": REPORT_NAME, "reviewName": REVIEW_NAME, "dailyName": DAILY_NAME}


def fill(text: str) -> str:
    """Put the workspace's values into a prompt template's @@NAME@@ markers (the templates keep {…} for .format)."""
    for name, value in {"JIRA_CLOUD_ID": JIRA_CLOUD_ID, "LIST_ID": LIST_ID, "GH_REPO": GH_REPO,
                        "REPO_NAME": REPO_NAME, "REVIEWER": REVIEWER}.items():
        text = text.replace(f"@@{name}@@", value)
    return text
