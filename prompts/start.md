Migrate request from the owner (Claude Tickets) for Jira ticket {key}.

Work in {repo}. FIRST read {runbook} in full and follow its procedure for a migration: how to investigate the ticket,
the go / no-go decision, how to build it, which reviews and checks to run (for a change to what a page renders: every
review skill the runbook names, whatever kind of ticket this is), the PRs to open, and when to move Jira.
If the ticket is @@REVIEW@@ or has unanswered reviewer feedback in its review thread, follow the runbook's fix procedure instead.

The owner chose this ticket: do NOT stop to ask. Take the recommended option for every decision and record it (the
question, the choice, the reason) where the runbook says. Never merge into the main branch, never force-push, and
move Jira only as the runbook allows.

Right after the go / no-go (either outcome), post the plan report the runbook describes to the owner's Claude Tickets
app (POST http://127.0.0.1:8790/api/note with header X-Dashboard: 1 and JSON {{"key": "{key}", "text": "<report>"}}).
Its first line ends with "@@PLAN_GO@@" or "no-go: <reason>".

Keep the investigation, your reasoning and all detail in THIS session. Post to Slack yourself only what the runbook
says (progress updates go to channel {channel}, thread_ts {thread_ts}, bot token in ~/.claude/secrets/slack-bot-token;
never print the token). When the work is finished, end with exactly one fenced block tagged `slack` whose FIRST line
is "{key} @@MIGRATED@@" and whose next line is "@@STATUS_LINE@@ @@DEV@@ → @@REVIEW@@", holding only the final
report the runbook specifies, including a "Checks:" line naming every review skill you ran and its result (or
"not run — <reason>"). If something could not be finished, the block is one line naming the blocker.
Slack mrkdwn, at most about 2,500 characters.
