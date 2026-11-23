

The owner asks where this session stands on {key}. Do NOT continue the work — answer only.
Report: the steps you finished, the step you were on when you stopped (or finished), what is left, commits made (pushed
or not), uncommitted changes, the PRs, and the ticket's CURRENT Jira status — check Jira now (Atlassian connector,
cloudId @@JIRA_CLOUD_ID@@).
End with exactly one fenced block tagged `slack`, at most 1,500 characters:
*{key}* — Jira: <status now>
Claude: <stopped / finished> at <step>
Done: <short>
Left: <short>
PRs: <each PR: number, base branch, state>
Uncommitted / unpushed: <yes — what / no>
