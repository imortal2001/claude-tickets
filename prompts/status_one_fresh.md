

Report the status of Jira ticket {key}. Work in {repo}.
- Jira now (Atlassian connector, cloudId @@JIRA_CLOUD_ID@@): status, summary.
- PRs: `gh pr list --repo @@GH_REPO@@ --search "{key} in:title" --state all --json number,state,mergeable,baseRefName,headRefName`.
- Local git: does branch {key} exist, its last commit, ahead/behind origin.
- The app's record for this ticket: {record}
End with exactly one fenced block tagged `slack`, at most 1,500 characters:
*{key}* — <summary>
Jira: <status>
Claude: <running / stopped at … / finished / no session>
PRs: <each PR: number, base branch, state>
Branch: <last commit, pushed or not>
