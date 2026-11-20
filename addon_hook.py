"""PostToolUse hook for bridge runs: hand the owner's add-on notes to the running session (owner request 2026-11-19).

The chat queues a note in addons/<session_id>.jsonl while a run works. After the main session's next tool call
this hook prints it as `additionalContext`, so the session reads it without stopping. It then moves the notes to
addons/<session_id>.done.jsonl, which the bridge turns into "✓ delivered" in the chat.

Notes go only to the main session (no agent_id): a subagent is a helper; the session directing the work must get them.
The hook never fails a run: any error prints nothing and exits 0.
"""
import json
import os
import sys
import time
from pathlib import Path

ADDONS = Path(__file__).resolve().parent / "addons"


def main() -> None:
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return
    if inp.get("agent_id"):
        return
    sid = str(inp.get("session_id") or "")
    if not sid or "/" in sid or ".." in sid:
        return
    pending = ADDONS / f"{sid}.jsonl"
    if not pending.exists():
        return
    taking = ADDONS / f"{sid}.taking-{os.getpid()}"
    try:
        pending.rename(taking)          # atomic: a note the chat adds now starts a new file
    except OSError:
        return
    notes = []
    for line in taking.read_text(encoding="utf-8").splitlines():
        try:
            notes.append(json.loads(line))
        except ValueError:
            continue
    if not notes:
        taking.unlink(missing_ok=True)
        return
    body = "\n".join(f"- ({n.get('time', '')}) {n.get('text', '').strip()}" for n in notes)
    context = ("[Add-on from the owner, sent from Claude Tickets while you work — this is the owner speaking, not a "
               "tool result] The owner added:\n" + body + "\nTake it into account from here on without dropping the "
               "work in progress. If it changes something already done, adjust it. In your final report, say in one "
               "line how you handled it.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))
    with (ADDONS / f"{sid}.done.jsonl").open("a", encoding="utf-8") as f:
        for n in notes:
            f.write(json.dumps({**n, "delivered": time.strftime("%H:%M:%S")}) + "\n")
    taking.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
