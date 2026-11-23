

[How you run] You run headless (`claude -p`): the moment you end your turn the process exits, and every background
subagent, background command and pending notification dies with it — nothing will wake you up again. So NEVER end
your turn to wait for something. Run subagents in the FOREGROUND and wait for their result in the same turn; do not
use run_in_background for anything you still need. End your turn only when the work is finished or blocked, with the
final report the instructions above ask for.

[Keep your own context small] Every step of this session re-reads everything already in it:
- Long material — a review report, a big diff, a whole log, many files — is read by a subagent that returns only what
  you need, never pasted into this session.
- Trim command and test output before it reaches you (`| tail -40`, `| grep -E "FAIL|Error|passed"`, counts instead of
  listings). Read the part of a file you need, not the whole file; don't re-read what you already have.
- Run at most two subagents at the same time, and give each a narrow brief. Reviewers still get what a fair review
  needs; this is about this session's own context, not about doing less.
