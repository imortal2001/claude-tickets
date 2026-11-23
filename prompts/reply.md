The owner wrote to you about {key} (Claude Tickets chat):

{text}

Read it for what it is:
- A QUESTION ("did you …?", "why …?", "is it …?"): answer it from what this session actually did, with the evidence
  (the command or skill, its result, where its report is). Do not start new work to answer it. If the answer is that
  something the runbook requires was not done, say so plainly and end with "Option 1 (recommended): <do it now>" and
  "Option 2: leave it" — the owner chooses in the chat.
- An INSTRUCTION ("fix …", "run …", "go ahead", "Option 1", "fix them now"): do it, following the runbook as before.
As before, keep all detail in this session and end your turn with exactly one fenced `slack` block holding only the
status text (for a question: the short answer).