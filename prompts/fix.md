Fix request from the owner (Claude Tickets) for Jira ticket {key}: fix the reviewer's latest feedback and finish
the round.
{link_line}Fix EVERY reviewer feedback message in the ticket's review thread that the owner has not answered yet (there may
be more than one round), not only the newest.

Work in {repo}. FIRST read {runbook} in full and follow its fix procedure: where the feedback is, how to fix each
item (with a test where one fits), the reviews to run again until clean, the PRs to update, and the Jira moves
(@@REVIEW@@ → @@DEV@@ when you start, back to @@REVIEW@@ when the round is finished and clean). Do NOT stop to ask:
take the recommended option and record it. Never merge into the main branch, never force-push, never reply in the
team's review threads yourself (put a draft reply in this session).

Keep the details in THIS session. Post to Slack yourself only what the runbook says (channel {channel}, thread_ts
{thread_ts}, bot token in ~/.claude/secrets/slack-bot-token; never print the token). When the round is finished,
end with exactly one fenced block tagged `slack`:
{key} @@FIXED@@
@@STATUS_LINE@@ @@DEV@@ → @@REVIEW@@
@@STAGING_LINE@@ <link, or "none needed — why">
Checks: <every review skill you ran, each with its result; "not run — <reason>" for one that did not run>
If something could not be finished, the block is one line: "{key} @@NOT_FIXED@@: <reason>".
