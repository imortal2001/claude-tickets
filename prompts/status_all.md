

Report the owner's Jira tickets. Work in {repo}.
1. Jira (Atlassian connector, cloudId @@JIRA_CLOUD_ID@@): `@@JQL@@` — key, summary, status.
2. The ticket table (read only): `slack_read_list` @@LIST_ID@@ — use its Summary as the ticket name when it is more
   descriptive than Jira's, and its "Waiting on" / "Next step" as context.
3. Claude side — the app's record of runs (JSON, keyed by thread; each has key, mode, status = running / stopped /
   finished / ended_turn / failed / queued, and times):
{jobs}
   Running right now: {running}. Queued: {queued}.
4. Git, per ticket: local branch `<KEY>`: last commit time and subject, commits not pushed, uncommitted changes.
   Read only — no checkout; fetch is fine.
5. Open PR per ticket: `gh pr list --repo @@GH_REPO@@ --search "<KEY> in:title" --state open --json number,mergeable`.
PROGRESS = where the work stands right now, in plain words, e.g. "Claude working now (fix, 12 min)", "Stopped 14:20
during fix — 3 commits not pushed", "Finished 15:05 — PR #12 mergeable", "Waiting for @@REVIEWER@@'s review",
"Feedback not fixed yet", "Not started". Say plainly when a session stopped part-way and what is left.
End with exactly one fenced block tagged `tickets` holding a JSON array, one object per ticket, in this order of Jira
status: @@BOARD_ORDER@@, then any other:
[{{"key": "<KEY>", "name": "<ticket name, max 60 chars>", "status": "<Jira status>",
  "pr": "#12 (mergeable)" or "#13 (conflicts)" or "no PR", "progress": "<PROGRESS, max 160 chars>"}}]
