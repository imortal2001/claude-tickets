"""How the team works: its Jira statuses, the words its runs' reports start with, its Slack List options, and the
text of every prompt the bridge sends to Claude Code.

The code never names a Jira status or a review skill itself: it asks this module. Two layers:
- workflow.json (git-ignored) holds the team's values; workflow.example.json is the template, used when there is no
  workflow.json, so a fresh clone runs on the generic defaults.
- prompts/<name>.md are the generic prompt defaults (tracked); prompts.local/<name>.md (git-ignored) replace them
  one by one with the team's own wording.

Read once when the bridge starts (dashboard.py reloads this module with itself): restart the bridge after editing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import workspace

HERE = Path(__file__).resolve().parent
FILE = HERE / "workflow.json" if (HERE / "workflow.json").exists() else HERE / "workflow.example.json"
PROMPTS, LOCAL_PROMPTS = HERE / "prompts", HERE / "prompts.local"

_c = json.loads(FILE.read_text(encoding="utf-8"))


def _get(path: str):
    """A required value, e.g. "jira.statuses.review". A missing one stops the bridge with a message naming it."""
    node = _c
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise SystemExit(f'{FILE} has no "{path}": add it (workflow.example.json shows every key).')
        node = node[part]
    return node


# ---- Jira: the code works with ROLES; the team's status names live here ----
ROLES = ("open", "dev", "review", "blocked", "released")
STATUS = {role: _get(f"jira.statuses.{role}") for role in ROLES}          # role -> the team's Jira status name
OPEN, DEV, REVIEW, BLOCKED, RELEASED = (STATUS[r] for r in ROLES)
ROLE_OF = {name: role for role, name in STATUS.items()}                    # Jira status name -> role
ALIASES = _c.get("jira", {}).get("status_aliases") or {}                   # e.g. a localized "OPEN（オープン）" -> "OPEN"
BOARD_ORDER = [STATUS[r] for r in _get("jira.board_order")]               # the board's status groups, top to bottom
ACTIVE = (REVIEW, DEV)                                                     # statuses whose review threads are watched
JQL = _get("jira.jql")                                                     # the owner's open tickets
WATCH_TYPES = tuple(_c.get("jira", {}).get("watch_issue_types") or ())     # issue types the feedback watch reads; () = all


def jira_status(name: str) -> str:
    """A Jira status name as the rest of the code knows it (aliases folded)."""
    return ALIASES.get(name, name)


# ---- the words a run's final report starts with (the runbook tells the runs to write them) ----
_r = _get("reports")
MIGRATED, FIXED, REVIEWED = f" {_r['migrated']}", f" {_r['fixed']}", f" {_r['reviewed']}"   # " is migrated" …
REVIEWED_CLEAN = _r["reviewed_clean"]                                       # "is reviewed: clean"
CLEAN_RE = re.compile(r"\s*".join(re.escape(w) for w in REVIEWED_CLEAN.split()), re.I)
NOT_FIXED = f" {_r['not_fixed']}"                                           # " could not be fixed"
STATUS_LINE, STAGING_LINE = _r["status_line"], _r["staging_line"]          # "Status:", "Staging PR:"
PLAN_GO, PLAN_NO_API = _r["plan_go"], _r["plan_no_api"]                    # the plan report's verdicts
REPORT_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in
                       (_r["migrated"], _r["fixed"], _r["reviewed"], *_r["not_done"])) + r")\b")

# ---- tickets the board can hide (e.g. template tickets) ----
HIDE_MARKER, HIDE_LABEL = _get("hide.marker"), _get("hide.label")


def hidden(summary: str) -> bool:
    return bool(HIDE_MARKER) and HIDE_MARKER.upper() in (summary or "").upper()


# ---- the Slack List's option keys (its "Status", "Waiting on", … columns) ----
LIST_STATUS = {role: _get(f"list_options.status.{role}") for role in ROLES}   # role -> the List's status option
LIST_WAITING = _get("list_options.waiting")                                   # you / new_api / not_started / skip
LIST_LABELS = _get("list_options.labels")                                     # option key -> label shown


def list_status_option(jira_name: str, default: str | None = None,
                       roles: tuple = ("open", "dev", "review", "blocked")) -> str | None:
    """The List's Status option for a Jira status name (only the board's roles), else default."""
    role = ROLE_OF.get(jira_status(jira_name))
    return LIST_STATUS[role] if role in roles else default


# ---- what the app writes for the owner in the review thread ----
MESSAGES = _get("messages")


def message(name: str, **values) -> str:
    """A message from workflow.json's messages, filled in. A line with a placeholder whose value is empty (no staging
    PR, say) is left out."""
    keep = []
    for line in MESSAGES[name].split("\n"):
        names = re.findall(r"{(\w+)}", line)
        if all(values.get(n) for n in names):
            keep.append(line.format(**values))
    return "\n".join(keep)


def greeting(hour: int) -> str:
    morning, afternoon, evening = MESSAGES["greetings"]
    return morning if hour < 12 else afternoon if hour < 18 else evening


# ---- prompts ----
def fill(text: str) -> str:
    """Put the workflow's values into a prompt's @@NAME@@ markers: @@JQL@@, the statuses (@@OPEN@@, @@DEV@@, @@REVIEW@@,
    @@BLOCKED@@, @@RELEASED@@, @@BOARD_ORDER@@) and the report words (@@MIGRATED@@, @@FIXED@@, @@REVIEWED@@,
    @@REVIEWED_CLEAN@@, @@NOT_FIXED@@, @@STATUS_LINE@@, @@STAGING_LINE@@, @@PLAN_GO@@)."""
    values = {"JQL": JQL, "BOARD_ORDER": ", ".join(BOARD_ORDER), **{role.upper(): name for role, name in STATUS.items()},
              "MIGRATED": _r["migrated"], "FIXED": _r["fixed"], "REVIEWED": _r["reviewed"],
              "REVIEWED_CLEAN": _r["reviewed_clean"], "NOT_FIXED": _r["not_fixed"], "STATUS_LINE": STATUS_LINE,
              "STAGING_LINE": STAGING_LINE, "PLAN_GO": PLAN_GO}
    for name, value in values.items():
        text = text.replace(f"@@{name}@@", value)
    return text


def prompt(name: str) -> str:
    """One prompt's text, with the workflow's and the workspace's @@NAME@@ markers filled in: prompts.local/<name>.md
    when the team has its own wording, else the generic prompts/<name>.md. Code passes the {placeholders} later."""
    for folder in (LOCAL_PROMPTS, PROMPTS):
        f = folder / f"{name}.md"
        if f.exists():
            return workspace.fill(fill(f.read_text(encoding="utf-8")))
    raise SystemExit(f"Missing prompt {name!r}: add prompts/{name}.md (or prompts.local/{name}.md).")


# What the dashboard's script needs.
PUBLIC = {"status": STATUS, "order": BOARD_ORDER, "hideMarker": HIDE_MARKER, "hideLabel": HIDE_LABEL,
          "reportWords": [_r["migrated"], _r["fixed"], _r["reviewed"], *_r["not_done"]]}
