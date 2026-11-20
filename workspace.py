"""Workspace settings: the Slack, Jira and GitHub IDs this bridge works against.

They live in workspace.json next to this file, which git does not track; workspace.example.json shows its shape.
Someone setting up their own copy fills workspace.json with their own workspace's IDs. Tokens never go here: they
stay in ~/.claude/secrets.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FILE = Path(__file__).resolve().parent / "workspace.json"

if not FILE.exists():
    raise SystemExit(f"Missing {FILE}: copy workspace.example.json to workspace.json and fill in your workspace's IDs.")
_c = json.loads(FILE.read_text(encoding="utf-8"))
_slack, _jira, _gh, _rev = _c["slack"], _c["jira"], _c["github"], _c["reviewer"]

SLACK_URL = f"https://{_slack['workspace']}.slack.com"
SLACK_URL_RE = re.escape(SLACK_URL)
OWNER = _slack["owner_user"]                     # the only Slack user allowed to command the bridge
BOT_USER = _slack["bot_user"]                    # the bridge's own Slack app user
REPORT_CHANNEL = _slack["report_channel"]        # private channel where runs and chats live
REVIEW_CHANNEL = _slack["review_channel"]        # team channel with the review threads
DAILY_CHANNEL = _slack["daily_report_channel"]   # channel whose thread the daily report is pasted into
LIST_ID = _slack["ticket_list"]                  # Slack List "My Jira tickets"
LIST_COLUMNS = _slack["ticket_list_columns"]     # our column name -> the List's column id

REVIEWER = _rev["name"]                          # how the reviewer is called in messages ("Hello, @<name> -san")
REVIEWER_USER = _rev["slack_user"]
REVIEWER_OPTION = _rev["list_option"]            # the List's "Waiting on" option for the reviewer

JIRA_SITE = _jira["site"].rstrip("/")
JIRA_CLOUD_ID = _jira["cloud_id"]
JIRA_V2_PR_FIELD = _jira["v2_pr_field"]          # custom field holding the main PR URL

GH_REPO = _gh["repo"]                            # owner/name
REPO = Path(_gh["local_checkout"]).expanduser()
REPO_NAME = REPO.name

# What the dashboard's script needs; nothing else leaves the process.
PUBLIC = {"jira": JIRA_SITE, "report": REPORT_CHANNEL, "review": REVIEW_CHANNEL, "reviewer": REVIEWER,
          "reviewerOption": REVIEWER_OPTION}


def fill(text: str) -> str:
    """Put the workspace's values into a prompt template's @@NAME@@ markers (the templates keep {…} for .format)."""
    for name, value in {"JIRA_CLOUD_ID": JIRA_CLOUD_ID, "LIST_ID": LIST_ID, "GH_REPO": GH_REPO,
                        "REPO_NAME": REPO_NAME, "REVIEWER": REVIEWER}.items():
        text = text.replace(f"@@{name}@@", value)
    return text
