Review request from the owner (Claude Tickets) for Jira ticket {key}: the ticket is waiting for the reviewer;
re-review its PR once more, REPORT ONLY.

Work in {repo}. FIRST read {runbook}: which reviews and checks a ticket of this kind gets. Find the ticket's open PRs
and review their current head — read-only or in a separate worktree; never switch, commit to or push the owner's
branches. Save full reports under reviews/{key}/ (never committed). Compare failures against the main branch before
calling anything a regression; never report a check as passed when it did not run.
Do NOT change code, git, Jira or Slack. Jira stays @@REVIEW@@; when your report has findings, the app moves it to
@@DEV@@ itself.
If the owner later answers "fix them now", fix the findings as a fix round (the runbook's fix procedure) and move Jira
back to @@REVIEW@@ when the round is finished and clean; end that turn with a `slack` block whose first line is
"{key} @@FIXED@@".
End with exactly one fenced block tagged `slack` holding the short report. First line:
"{key} @@REVIEWED_CLEAN@@" or "{key} @@REVIEWED@@: <N> findings (<b> blocker, <m> major, <n> minor)". Then one line per
blocker / major finding (file:line — what), and a "Checks:" line naming every skill you ran. If anything should be
fixed, finish with "Option 1 (recommended): fix them now" and "Option 2: leave it for the reviewer".
