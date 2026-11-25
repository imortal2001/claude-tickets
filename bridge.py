"""The Claude Tickets bridge: the background service behind the dashboard.

It checks Jira for the owner's tickets, runs one Claude Code session at a time for the dashboard's buttons (Migrate,
Fix Now, Review, Resume, Chat; the rest queue; a chat's conflict fix may run alongside, in a second slot), listens to Slack over Socket Mode (outbound, no public URL) for the
review threads, and serves the dashboard (dashboard.py). The feedback watch and the Slack List sync are in watch.py.
Each session keeps all its detail; the app shows its chat and the final report. A reply in a run's Slack thread, when
Slack run reports are on, is passed to that session (claude --resume).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import workflow
import workspace
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

HOME = Path.home()
BASE = HOME / ".claude" / "jira-automation" / "slack-bridge"
STATE = BASE / "jobs.json"
LOG = BASE / "bridge.log"
SECRETS = HOME / ".claude" / "secrets"
REPO = workspace.REPO
CLAUDE = HOME / ".local" / "bin" / "claude"
# Model and effort of every headless run; the subagents it starts inherit them (agents use `model: inherit`).
# Default Opus 5.5 at high effort (owner request 2026-11-17); without the flags `claude -p` fell back to medium.
# The owner picks them in the dashboard header; run_settings.json holds the choice and is read at each launch.
RUN_SETTINGS = HOME / ".claude" / "jira-automation" / "slack-bridge" / "run_settings.json"
MODELS = {"claude-opus-5-5": "Opus 5.5", "claude-fable-5-2": "Fable 5.2", "claude-sonnet-5-5": "Sonnet 5.5"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_RUN = {"model": "claude-opus-5-5", "effort": "high"}
# The Agent tool takes an alias, not a model id.
MODEL_ALIAS = {"claude-opus-5-5": "opus", "claude-fable-5-2": "fable", "claude-sonnet-5-5": "sonnet"}


# The feedback watch reads threads and syncs the table: Sonnet is enough (owner request 2026-11-17).
DEFAULT_WATCH = {"model": "claude-sonnet-5-5", "effort": "high"}


def _pick(s: dict, default: dict = DEFAULT_RUN) -> dict:
    s = s if isinstance(s, dict) else {}
    return {"model": s.get("model") if s.get("model") in MODELS else default["model"],
            "effort": s.get("effort") if s.get("effort") in EFFORTS else default["effort"]}


def run_settings() -> dict:
    """{"model", "effort"} for migrate runs (and resume / chat), plus "fix" for Fix Now → runs and "review" for
    Review → runs and the reviewer subagents of other runs; each defaults to the migrate pick (owner 2026-11-17)."""
    try:
        s = json.loads(RUN_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    return {**_pick(s), "fix": _pick(s.get("fix") or s), "review": _pick(s.get("review") or s),
            "watch": _pick(s.get("watch"), DEFAULT_WATCH)}


def review_note(r: dict) -> str:
    return (f"\n\n[Reviewer model — owner choice] Every reviewer subagent you start (front-reviewer, middleware-reviewer, "
            f"sdd-verifier, or any independent / adversarial review agent) runs with model \"{MODEL_ALIAS[r['model']]}\" and "
            f"effort \"{r['effort']}\": pass both on the Agent call.")


def model_args(s: dict | None = None) -> list[str]:
    s = s or run_settings()
    return ["--model", s["model"], "--effort", s["effort"]]
RUNBOOK = HOME / ".claude" / "jira-automation" / "RUNBOOK.md"

OWNER = workspace.OWNER  # the only Slack user allowed to command the bridge
CHANNEL = workspace.REPORT_CHANNEL  # the report channel (private)
KEY_RE = re.compile(r"^NJ-\d{4,6}$")
SLACK_BLOCK_RE = re.compile(r"```slack\s*\n(.*?)```", re.S)
MAX_SLACK = 3800

logging.basicConfig(
    filename=LOG,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bridge")


def read_secret(name: str) -> str:
    return (SECRETS / name).read_text(encoding="utf-8").strip()


# ---------- state ----------

_state_lock = threading.Lock()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"threads": {}}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def update_thread(thread_ts: str, **fields) -> dict:
    with _state_lock:
        state = load_state()
        entry = state["threads"].setdefault(thread_ts, {})
        entry.update(fields)
        save_state(state)
        return dict(entry)


def get_thread(thread_ts: str) -> dict | None:
    with _state_lock:
        return load_state()["threads"].get(thread_ts)


# ---------- prompts ----------

START_PROMPT = workflow.prompt("start")

REPLY_PROMPT = workflow.prompt("reply")

RESUME_PROMPT = workflow.prompt("resume")


REVIEW_PROMPT = workflow.prompt("review")


CHAT_FORK_INTRO = workflow.prompt("chat_fork_intro")

CHAT_PROMPT = workflow.prompt("chat")


FIX_PROMPT = workflow.prompt("fix")



# ---------- worker ----------

jobs: "queue.Queue[dict]" = queue.Queue()
current: dict = {"proc": None, "key": None, "thread_ts": None, "since": None}
# A second run slot (owner decision 2026-11-25): a merge-only conflict fix ("Fix the conflict" / "What conflicts?"
# in the chat) starts at once while another ticket runs, instead of waiting behind it. It works in the ticket's own
# worktree (the runbook's rule E), so the two never share a checkout. Everything else still runs one at a time.
side: dict = {"proc": None, "key": None, "thread_ts": None, "since": None}
side_jobs: "queue.Queue[dict]" = queue.Queue()
_slot = threading.local()   # which slot the worker thread calling run_claude fills
waiting: list[str] = []  # labels of the queued jobs (the dashboard shows them)
cancelled: set[str] = set()  # queued labels removed from the dashboard; the worker skips them



HEADLESS_NOTE = workflow.prompt("headless_note")


AUTO_RESUME_MAX = 2
AUTO_RESUME_TEXT = workflow.prompt("auto_resume")


def mark_interrupted_runs() -> None:
    """At startup: a run recorded as running / queued was cut off when the bridge (the app) stopped — mark it stopped
    so the dashboard offers Resume → (owner request 2026-11-17)."""
    with _state_lock:
        state = load_state()
        hit = [ts for ts, v in state["threads"].items() if v.get("status") in ("running", "queued")]
        for ts in hit:
            state["threads"][ts].update(status="stopped", stop_reason="the bridge stopped (app closed or restarted)",
                                        stopped_at=time.strftime("%Y-%m-%d %H:%M"))
        if hit:
            save_state(state)
    for ts in hit:
        log.info("run %s was cut off by a bridge stop: marked stopped", ts)
        if is_local(ts):
            runlog_append(ts, "system", "⏹ Interrupted: the bridge stopped (app closed or restarted) during this run. Resume → continues it.")


# ---------- add-ons: the owner's notes to a running session (owner request 2026-11-19) ----------
# While a run works, the chat queues what the owner types in addons/<session_id>.jsonl; addon_hook.py (a PostToolUse
# hook every run gets through --settings) hands it to the main session after its next tool call, and writes
# addons/<session_id>.done.jsonl, which addon_watcher turns into "✓ delivered" in the chat. Notes still waiting when the
# run finishes are sent as an ordinary chat message (send_leftover_addons).
ADDONS = BASE / "addons"
ADDON_HOOK = BASE / "addon_hook.py"
HOOK_SETTINGS = json.dumps({"hooks": {"PostToolUse": [{"matcher": "", "hooks": [
    {"type": "command", "command": f"{sys.executable} {ADDON_HOOK}", "timeout": 10}]}]}})


def queue_addon(session_id: str, thread_ts: str, text: str) -> None:
    ADDONS.mkdir(exist_ok=True)
    with (ADDONS / f"{session_id}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"thread_ts": thread_ts, "text": text, "time": time.strftime("%H:%M")}) + "\n")


def addon_watcher() -> None:
    while True:
        try:
            for f in ADDONS.glob("*.done.jsonl"):
                notes = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
                f.unlink()
                for n in notes:
                    runlog_append(n["thread_ts"], "system", f"✓ Your add-on ({n.get('time', '')}) reached the session at "
                                                            f"{n.get('delivered', '')}; it's taking it into account now.")
                    log.info("add-on delivered (%s)", n["thread_ts"])
        except Exception:
            log.exception("add-on watcher")
        time.sleep(3)


def send_leftover_addons(key: str, thread_ts: str, session_id: str | None, q: "queue.Queue[dict] | None" = None) -> bool:
    """The run finished before its next tool call: send the waiting notes as a chat message instead."""
    f = ADDONS / f"{session_id}.jsonl" if session_id else None
    if not f or not f.exists():
        return False
    notes = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    f.unlink()
    if not notes:
        return False
    runlog_append(thread_ts, "system", "📎 The run finished before your add-on reached it — sending it as a chat message now.")
    update_thread(thread_ts, status="queued", status_before=(get_thread(thread_ts) or {}).get("status"))
    (q or jobs).put({"kind": "reply", "key": key, "thread_ts": thread_ts,
                     "text": "\n".join(n["text"] for n in notes)})
    return True


# ---------- keeping sessions small (owner request 2026-11-17) ----------
# Resuming NJ-101032's fix after 3 idle hours re-loaded its whole ~400k-token context into Claude's cache (the cache
# had expired) and then re-read it on every step. A large session is now compacted — summarised in place with
# Claude Code's own /compact, so the run continues in the same session — right after a Stop / failure (while it is
# still cached, so the summarising pass is cheap) and before a Resume after more than an hour.
COMPACT_OVER = 150_000          # tokens of context
COMPACT_IDLE = 3600             # seconds: past this Claude's prompt cache has expired
COMPACT_MODEL = ["--model", "claude-sonnet-5-5", "--effort", "medium"]   # the summarising pass only
COMPACT_FOCUS = workflow.prompt("compact_focus")


def session_size(session_id: str | None) -> tuple[int, float]:
    """(tokens of context at the session's last step, epoch of that step). After a /compact, its post-size."""
    if not session_id:
        return 0, 0.0
    f = next(iter(PROJECTS.glob(f"*/{session_id}.jsonl")), None)
    if not f:
        return 0, 0.0
    with f.open("rb") as fh:
        fh.seek(max(0, f.stat().st_size - 3_000_000))
        tail = fh.read().decode("utf-8", "replace").splitlines()[1:]
    for line in reversed(tail):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("subtype") == "compact_boundary":
            return int((o.get("compactMetadata") or {}).get("postTokens") or 0), f.stat().st_mtime
        u = (o.get("message") or {}).get("usage") if o.get("type") == "assistant" else None
        if u:
            n = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            return n, f.stat().st_mtime
    return 0, f.stat().st_mtime


def compact_session(client, key: str, thread_ts: str, session_id: str | None, why: str) -> bool:
    """Summarise a large session in place (/compact). Runs in the worker, so nothing else uses the session."""
    size, _ = session_size(session_id)
    if size < COMPACT_OVER:
        return False
    log.info("compacting %s (%s, %dk tokens): %s", key, session_id, size // 1000, why)
    cmd = [str(CLAUDE), "-p", f"/compact {COMPACT_FOCUS}", "--resume", session_id, *COMPACT_MODEL,
           "--permission-mode", "auto", "--output-format", "json"]
    env = dict(os.environ)
    env["PATH"] = f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    try:
        proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        if data.get("is_error"):
            raise RuntimeError(str(data.get("result"))[:200])
    except Exception as exc:
        log.warning("compacting %s failed: %s", key, exc)
        return False
    after, _ = session_size(session_id)
    log.info("compacted %s: %dk -> %dk tokens", key, size // 1000, after // 1000)
    post(client, thread_ts, f"🗜 {key}: compacted the session ({why}) — {size // 1000}k → {after // 1000}k tokens of "
                            "context, so the next step doesn't re-read all of it. Same session; nothing is lost but raw output.")
    return True


def run_claude(prompt: str, key: str, session_id: str | None, new_session_id: str | None = None,
               fork: bool = False) -> dict:
    cur = getattr(_slot, "cur", current)
    allset = run_settings()
    # A fix or review run — or a later turn in that run's chat — uses its own pick; other runs use the migrate pick.
    # Every run except a review passes the review pick on to the reviewer subagents it starts.
    tmode = (get_thread(cur.get("thread_ts") or "") or {}).get("mode")
    is_review = cur.get("kind") == "review" or tmode == "review"
    is_fix = not is_review and (cur.get("kind") == "fix" or tmode == "fix")
    ms = allset["review"] if is_review else allset["fix"] if is_fix else allset
    ms = cur.get("override") or ms   # this run only, chosen in the confirm dialog
    cmd = [str(CLAUDE), "-p", prompt + HEADLESS_NOTE + ("" if is_review else review_note(allset["review"])), *model_args(ms),
           "--settings", HOOK_SETTINGS, "--permission-mode", "auto", "--output-format", "json"]
    if session_id:
        cmd += ["--resume", session_id]
        if fork:   # a copy of that session under a new id; the original stays as it was
            cmd += ["--fork-session", "-n", key] + (["--session-id", new_session_id] if new_session_id else [])
    else:
        cmd += ["-n", key]
        if new_session_id:
            cmd += ["--session-id", new_session_id]
    env = dict(os.environ)
    env["PATH"] = f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    if cur.get("thread_ts"):   # the run's model and effort, shown on its card
        update_thread(cur["thread_ts"], model=ms["model"], effort=ms["effort"])
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    cur.update(proc=proc, since=time.time(), model=ms["model"], effort=ms["effort"])
    out, err = proc.communicate()
    cur.update(proc=None)
    if proc.returncode != 0 and not out.strip():
        log.error("claude exited %s for %s: %s", proc.returncode, key, err[-2000:])
        return {"ok": False, "stopped": proc.returncode in (-15, -9), "error": err[-500:]}
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        log.error("unparsable claude output for %s: %s", key, out[-2000:])
        return {"ok": False, "stopped": False, "error": "unparsable output"}
    if data.get("is_error"):
        log.error("claude reported an error for %s: %s", key, str(data.get("result"))[:500])
        return {"ok": False, "stopped": False, "error": str(data.get("result") or "error")[:300], "session_id": data.get("session_id")}
    return {"ok": True, "result": data.get("result", ""), "session_id": data.get("session_id")}


def slack_text_from(result: str) -> str | None:
    blocks = SLACK_BLOCK_RE.findall(result or "")
    if not blocks:
        return None
    text = blocks[-1].strip()
    return text if len(text) <= MAX_SLACK else text[: MAX_SLACK - 20] + "\n…(see the session)"


def local_prompt(prompt: str, thread_ts: str) -> str:
    """For a local run the session must not post its in-progress report to Slack (there is no thread)."""
    if not is_local(thread_ts):
        return prompt
    return re.sub(r"Post to Slack(?:\s+yourself)?\s+ONLY the in-progress report.*?never print the token\)\.",
                  "Do NOT post anything to Slack yourself (no in-progress report): the bridge records your reports "
                  "in the owner's Claude Tickets app.", prompt, count=1, flags=re.S)


def worker(client, q: "queue.Queue[dict] | None" = None, slot: dict | None = None) -> None:
    """Runs one queue's jobs, one at a time, in one run slot: the main queue, or the side queue."""
    q, slot = q or jobs, slot if slot is not None else current
    _slot.cur = slot
    while True:
        job = q.get()
        key, thread_ts = job["key"], job["thread_ts"]
        if job.get("label", key) in cancelled:
            cancelled.discard(job.get("label", key))
            update_thread(thread_ts, status="stopped", stopped_at=time.strftime("%Y-%m-%d %H:%M"))
            post(client, thread_ts, f"⏹ Removed from the queue (dashboard). Nothing ran for {key}.")
            q.task_done()
            continue
        if (job["kind"] in ("start", "fix", "review") or job.get("resume")) and job.get("label", key) in waiting:
            waiting.remove(job.get("label", key))
        slot.update(key=key, thread_ts=thread_ts, kind=job["kind"], override=clean_override(job.get("override")))
        try:
            entry = get_thread(thread_ts) or {}
            if job["kind"] in ("start", "fix", "review") and is_local(thread_ts):
                what = {"fix": "fixing review feedback", "review": "reviewing the PR (report only)"}.get(job["kind"], "migrating")
                # Migrate: say what the session is doing first, before its plan report (owner request 2026-11-17).
                line = (f"🔎 {key}: reviewing the ticket's scope — routes, lane and skill, APIs, legacy source · "
                        "the plan report follows") if job["kind"] == "start" else f"🛠 {key}: {what} · session started"
                runlog_append(thread_ts, "system", line)
            if job["kind"] == "start":
                set_head(client, thread_ts, f"🛠 *{key}* — migrating · session `{key}`")
                prompt = local_prompt(START_PROMPT.format(key=key, repo=REPO, runbook=RUNBOOK, channel=CHANNEL, thread_ts=thread_ts), thread_ts)
                sid = str(uuid.uuid4())
                update_thread(thread_ts, session_id=sid, session_name=key, status="running", last_run=time.strftime("%Y-%m-%d %H:%M"))
                res = run_claude(prompt, key, None, sid)
            elif job["kind"] == "fix":
                link_line = f"The owner pointed at this report / message: {job['link']}\n" if job.get("link") else "Use the newest reviewer feedback in the ticket's thread.\n"
                prompt = local_prompt(FIX_PROMPT.format(key=key, repo=REPO, runbook=RUNBOOK, link_line=link_line, channel=CHANNEL, thread_ts=thread_ts), thread_ts)
                sid = str(uuid.uuid4())
                update_thread(thread_ts, session_id=sid, session_name=f"{key}-fix", status="running", last_run=time.strftime("%Y-%m-%d %H:%M"))
                res = run_claude(prompt, f"{key}-fix", None, sid)
            elif job["kind"] == "review":
                prompt = local_prompt(REVIEW_PROMPT.format(key=key, repo=REPO, runbook=RUNBOOK), thread_ts)
                sid = str(uuid.uuid4())
                update_thread(thread_ts, session_id=sid, session_name=f"{key}-review", status="running", last_run=time.strftime("%Y-%m-%d %H:%M"))
                res = run_claude(prompt, f"{key}-review", None, sid)
            elif job["kind"] == "chat":
                sid = str(uuid.uuid4())
                # Continues the ticket's last run session itself (owner decision 2026-11-16: no fork); blank otherwise.
                src = job.get("fork_from")
                update_thread(thread_ts, session_id=src or sid, session_name=job.get("fork_name") if src else f"{key}-chat",
                              status="running", last_run=time.strftime("%Y-%m-%d %H:%M"))
                prompt = CHAT_PROMPT.format(key=key, repo=REPO, runbook=RUNBOOK, text=job["text"])
                if src:
                    prompt = CHAT_FORK_INTRO.format(run=job.get("fork_name") or "earlier", key=key) + prompt
                res = run_claude(prompt, f"{key}-chat", src, None if src else sid)
            else:
                update_thread(thread_ts, status="running", last_run=time.strftime("%Y-%m-%d %H:%M"))
                # a Resume → / auto-resume is not a message from the owner, so it is not read as a question
                prompt = (RESUME_PROMPT if job.get("resume") else REPLY_PROMPT).format(key=key, text=job["text"])
                if job.get("resume"):
                    _, last = session_size(entry.get("session_id"))
                    if last and time.time() - last > COMPACT_IDLE:   # the cache has expired: summarise before reloading
                        slot.update(kind="compact")
                        compact_session(client, key, thread_ts, entry.get("session_id"), "idle over an hour before Resume")
                        slot.update(kind=job["kind"])
                res = run_claude(prompt, key, entry.get("session_id"))
            if res.get("session_id"):
                update_thread(thread_ts, session_id=res["session_id"])
            if res.get("stopped"):
                update_thread(thread_ts, status="stopped", stopped_at=time.strftime("%Y-%m-%d %H:%M"))
            elif not res["ok"]:
                update_thread(thread_ts, status="failed")
            else:
                first = (slack_text_from(res.get("result", "")) or "").splitlines()[:1]
                done = bool(first) and any(w in first[0] for w in (workflow.MIGRATED, workflow.FIXED, workflow.REVIEWED))
                now = time.strftime("%Y-%m-%d %H:%M")
                if not done and job["kind"] == "reply" and entry.get("status_before") == "finished":
                    # A chat question on a run that had already finished (owner 2026-11-19): it stays finished, and it
                    # keeps its finish time — moving that forward made the board ask for the review reply again.
                    update_thread(thread_ts, status="finished", last_chat_at=now)
                else:
                    update_thread(thread_ts, status="finished" if done else "ended_turn", finished_at=now)
            if res.get("stopped"):
                post(client, thread_ts, f"⏹ Stopped. Reply here to continue {key}, or resume it in a terminal: `claude --resume {key}`")
            elif not res["ok"]:
                post(client, thread_ts, f"⚠️ The session for {key} failed ({res.get('error', 'unknown error')[:200]}). Check `{LOG}`.")
            if (res.get("stopped") or not res["ok"]):   # still cached now, so summarising is cheap; Resume starts small
                sid_now = res.get("session_id") or (get_thread(thread_ts) or {}).get("session_id")
                slot.update(kind="compact")
                compact_session(client, key, thread_ts, sid_now, "after Stop" if res.get("stopped") else "after a failure")
            else:
                text = slack_text_from(res["result"])
                # Auto-resume (owner request 2026-11-17): a migrate / fix / review run — or a resume of one — that ends
                # its turn without its final report ended early (e.g. waiting for a background agent that died with it).
                n = job.get("auto", 0)
                if not text and (job["kind"] in ("start", "fix", "review") or job.get("resume")):
                    if n < AUTO_RESUME_MAX:
                        update_thread(thread_ts, status="queued", auto_resumes=n + 1)
                        post(client, thread_ts, f"↻ {key} ended its turn before finishing — resuming automatically ({n + 1}/{AUTO_RESUME_MAX}).")
                        waiting.append(f"{key} (resume)")
                        q.put({"kind": "reply", "key": key, "thread_ts": thread_ts, "resume": True, "auto": n + 1,
                               "override": job.get("override"),
                               "label": f"{key} (resume)",
                               "text": AUTO_RESUME_TEXT.format(n=n + 1, max=AUTO_RESUME_MAX)})
                        log.info("auto-resume %s (%s) %d/%d", key, thread_ts, n + 1, AUTO_RESUME_MAX)
                        continue
                    post(client, thread_ts, f"⚠️ {key} ended its turn early {n + 1} times. Open the chat to see where it is, "
                                            "and reply there to continue.")
                    continue
                if send_leftover_addons(key, thread_ts, res.get("session_id") or (get_thread(thread_ts) or {}).get("session_id"), q):
                    log.info("add-on for %s sent as a chat message after the run", key)
                if not text and (entry.get("mode") or job["kind"]) == "chat":
                    raw = (res.get("result") or "").strip()   # a chat answers in plain text, not a slack block
                    text = raw if len(raw) <= MAX_SLACK else raw[: MAX_SLACK - 20] + "\n…(see the session)"
                if text:
                    post(client, thread_ts, text)
                    job_finished_hook(client, key=key, kind=job["kind"], mode=entry.get("mode") or job["kind"],
                                      text=text, thread_ts=thread_ts)
                    if job["kind"] == "start" or (entry.get("mode") is None and job["kind"] == "reply" and not entry.get("notice") and entry.get("key") == key and "migrated" in text.splitlines()[0]):
                        first = text.splitlines()[0]
                        head = f"✅ *{key}* — migrated (details in thread)" if workflow.MIGRATED in first else f"⚠️ *{key}* — not finished (see thread)"
                        set_head(client, thread_ts, head)
                    if (entry.get("mode") or job["kind"]) == "fix":
                        done = workflow.FIXED in text.splitlines()[0]
                        head = f"✅ *{key}* — feedback fixed (details in thread)" if done else f"⚠️ *{key}* — could not be fixed (see thread)"
                        link = entry.get("feedback_link")
                        set_head(client, thread_ts, head + (f"\n<{link}|{link}>" if link else ""), blocks=[])
                elif (entry.get("mode") or job["kind"]) == "fix":
                    set_head(client, thread_ts, f"✅ *{key}* — review round fixed · session `{key}-fix`")
                else:
                    post(client, thread_ts, f"ℹ️ {key}: the session ended its turn without a Slack update. Open it with `claude --resume {key}`.")
        except Exception:  # keep the worker alive
            log.exception("job failed: %s", job)
            post(client, thread_ts, f"⚠️ Bridge error while running {key}. Check `{LOG}`.")
        finally:
            slot.update(key=None, thread_ts=None, since=None, kind=None, model=None, effort=None, override=None)
            q.task_done()


def job_finished_hook(client, **kw) -> None:
    """After a run posts its final report: anything the dashboard module wants to do (it live-reloads, so this
    logic changes without restarting the bridge). Never lets an error reach the worker."""
    try:
        import dashboard
        dashboard._maybe_reload()
        fn = getattr(dashboard, "on_job_finished", None)
        if fn:
            threading.Thread(target=fn, kwargs=dict(kw, client=client), daemon=True).start()
    except Exception:
        log.exception("job-finished hook failed")


# ---------- where a run reports (owner decision 2026-11-16: the Claude Tickets app, not Slack) ----------

SETTINGS = HOME / ".claude/jira-automation/settings.json"
RUNLOG = BASE / "runlog.json"
_runlog_lock = threading.Lock()


def slack_run_reports() -> bool:
    """False (default): runs report into a local conversation the app shows. True: the old Slack threads."""
    try:
        return bool(json.loads(SETTINGS.read_text(encoding="utf-8")).get("slack_run_reports", False))
    except (OSError, json.JSONDecodeError):
        return False


def is_local(thread_ts: str) -> bool:
    return str(thread_ts).startswith("local-")


def runlog_read(thread_id: str) -> dict:
    with _runlog_lock:
        try:
            return json.loads(RUNLOG.read_text(encoding="utf-8")).get(thread_id) or {}
        except (OSError, json.JSONDecodeError):
            return {}


def _runlog_update(thread_id: str, fn) -> None:
    with _runlog_lock:
        try:
            data = json.loads(RUNLOG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        fn(data.setdefault(thread_id, {"head": "", "messages": []}))
        tmp = RUNLOG.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(RUNLOG)


def runlog_append(thread_id: str, who: str, text: str) -> None:
    _runlog_update(thread_id, lambda r: r["messages"].append({"t": time.time(), "who": who, "text": text}))


def set_head(client, thread_ts: str, text: str, **kw) -> None:
    """The run's one-line state (▶ / 🛠 / ✅ / ⚠️): the local log's head, or the Slack thread's top line."""
    if is_local(thread_ts):
        _runlog_update(thread_ts, lambda r: r.update(head=text))
        return
    try:
        client.chat_update(channel=CHANNEL, ts=thread_ts, text=text, **kw)
    except Exception:
        log.exception("chat.update failed")


def open_run_thread(client, key: str, head: str) -> str:
    """A new run's conversation: local (default) or a new top-level Slack line."""
    if not slack_run_reports():
        tid = f"local-{time.time():.6f}"
        _runlog_update(tid, lambda r: r.update(head=head, key=key))
        runlog_append(tid, "system", head)
        return tid
    return client.chat_postMessage(channel=CHANNEL, text=head)["ts"]


def post(client, thread_ts: str, text: str) -> None:
    if is_local(thread_ts):
        runlog_append(thread_ts, "claude", text)
        return
    try:
        client.chat_postMessage(channel=CHANNEL, thread_ts=thread_ts, text=text, unfurl_links=False)
    except Exception:
        log.exception("chat.postMessage failed")


PROJECTS = HOME / ".claude" / "projects"
LIVE_MINUTES = 15


def scan_live_sessions(minutes: int = LIVE_MINUTES) -> list[dict]:
    """Claude Code sessions (desktop app, terminal) in this repo or its worktrees with activity in the last
    `minutes`, other than the bridge's own. Read-only: title, folder, branch, last message."""
    now = time.time()
    with _state_lock:
        own = {v.get("session_id") for v in load_state()["threads"].values()}
    found = []
    for d in PROJECTS.glob(f"*{workspace.REPO_NAME}*"):
        for f in d.glob("*.jsonl"):
            try:
                age = (now - f.stat().st_mtime) / 60
                if age > minutes or f.stem in own:
                    continue
                size = f.stat().st_size
                with open(f, "rb") as fh:
                    fh.seek(max(0, size - 400_000))
                    lines = fh.read().decode("utf-8", "ignore").splitlines()[1:]
            except OSError:
                continue
            title = cwd = branch = last = None
            last_kind = last_stop = None
            for line in lines:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                t = o.get("type")
                if t == "custom-title":
                    title = o.get("customTitle") or title
                elif t == "agent-name" and not title:
                    title = o.get("agentName")
                cwd = o.get("cwd", cwd)
                branch = o.get("gitBranch", branch)
                if t in ("assistant", "user"):
                    content = (o.get("message") or {}).get("content")
                    injected = isinstance(content, str) and content.lstrip().startswith(("<task-notification>", "<system-reminder>"))
                    if not (t == "user" and (injected or o.get("isMeta"))):
                        last_kind = t
                        last_stop = (o.get("message") or {}).get("stop_reason") if t == "assistant" else None
                if t == "assistant":
                    for c in (o.get("message") or {}).get("content") or []:
                        if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                            last = c["text"]
            # The main checkout's branch is shared by every session working in it (and other sessions switch
            # it), so it says nothing about which ticket a session is on. Only a worktree's branch counts.
            if cwd and Path(cwd).resolve() == REPO.resolve():
                branch = None
            # working = mid-turn (a tool call or its result is the last thing); idle = the turn ended
            state = "idle" if (last_kind == "assistant" and last_stop == "end_turn") else "working"
            found.append({"session": f.stem, "age_min": round(age, 1), "state": state, "title": title or "(untitled)",
                          "cwd": cwd or "", "branch": branch or "", "last": (last or "").strip().replace("\n", " ")[:220]})
    return sorted(found, key=lambda x: x["age_min"])


def live_for(key: str) -> list[dict]:
    digits = key.split("-", 1)[1]
    pat = re.compile(rf"(?<!\d){digits}(?!\d)")
    return [s for s in scan_live_sessions()
            if pat.search(s["title"]) or s["branch"] == key or pat.search(s["cwd"])]



def busy_elsewhere(key: str) -> str | None:
    live = [x for x in live_for(key) if x.get("state") == "working"]
    if not live:
        return None
    s = live[0]
    return f"{key} is being worked on in another Claude session — “{s['title']}”, active {s['age_min']:.0f} min ago. Not starting a second one; stop that session first."




def jira_link(key: str) -> str:
    return f"<{JIRA_SITE}/browse/{key}|*{key}*>"



_sync_lock = threading.Lock()


def table_sync(client) -> bool:
    """When the Jira check sees a change: bring the Slack List in line with Jira (watch.table_sync). One at a time;
    skipped while the feedback watch holds runner.lock (it syncs the table itself)."""
    lock = HOME / ".claude/jira-automation/runner.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        log.info("table sync skipped: the hourly check is running")
        return False
    if not _sync_lock.acquire(blocking=False):
        log.info("table sync skipped: one is already running")
        return False
    try:
        t0 = time.time()
        log.info("table sync started")
        ok, text = _watch().sync_now()   # Python, no Claude (owner request 2026-11-17)
        log.info("table sync %s in %ss: %s", "finished" if ok else "FAILED", int(time.time() - t0), text[:200].replace("\n", " "))
        if ok and "no changes" not in text and "skipped" not in text:
            client.chat_postEphemeral(channel=CHANNEL, user=OWNER, text=f"📋 Table synced: {text}")
    except Exception:
        log.exception("table sync failed")
    finally:
        _sync_lock.release()
    return True


# ---------- Jira poller: new assignments within a minute ----------

JIRA_SITE = workspace.JIRA_SITE
JIRA_JQL = workflow.JQL
SEEN_FILE = BASE / "jira-seen.json"
POLL_SECONDS = 60


def jira_auth_header() -> str | None:
    """Basic auth from ~/.claude/secrets/jira-token (+ jira-email). None until the owner has saved a token."""
    import base64
    tok, mail = SECRETS / "jira-token", SECRETS / "jira-email"
    if not tok.exists() or not mail.exists():
        return None
    raw = f"{mail.read_text(encoding='utf-8').strip()}:{tok.read_text(encoding='utf-8').strip()}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def fetch_assigned(auth: str) -> dict[str, dict]:
    import urllib.request
    out: dict[str, dict] = {}
    token = None
    for _ in range(10):
        body = {"jql": JIRA_JQL, "fields": ["summary", "status"], "maxResults": 100}
        if token:
            body["nextPageToken"] = token
        req = urllib.request.Request(f"{JIRA_SITE}/rest/api/3/search/jql", data=json.dumps(body).encode(),
                                     headers={"Authorization": auth, "Content-Type": "application/json",
                                              "Accept": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        for it in data.get("issues") or []:
            f = it.get("fields") or {}
            status = workflow.jira_status((f.get("status") or {}).get("name") or "")
            out[it["key"]] = {"summary": f.get("summary") or "", "status": status}
        token = data.get("nextPageToken")
        if not token or data.get("isLast"):
            break
    return out


def notify_new_ticket(client, key: str, info: dict) -> None:
    summary = info.get("summary", "")
    text = f"🆕 New ticket assigned: {jira_link(key)} — {summary}\nJira status: {info.get('status') or '?'}"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if workflow.hidden(summary):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{workflow.HIDE_LABEL} ticket — the automation skips it."}]})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Start it from Claude Tickets."}]})
    client.chat_postMessage(channel=CHANNEL, text=f"New ticket assigned: {key} — {summary}", blocks=blocks, unfurl_links=False)


def jira_poller(client) -> None:
    told_no_token = False
    pending_sync = False
    while True:
        try:
            auth = jira_auth_header()
            if not auth:
                if not told_no_token:
                    log.info("jira poller idle: save ~/.claude/secrets/jira-token and jira-email to enable it")
                    told_no_token = True
            else:
                if told_no_token:
                    log.info("jira poller: token found, polling every %ss", POLL_SECONDS)
                    told_no_token = False
                cur = fetch_assigned(auth)
                seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else None
                if seen is None:
                    SEEN_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=1))
                    log.info("jira poller seeded with %d tickets", len(cur))
                elif not cur and len(seen) > 3:
                    log.warning("jira poller: empty result with %d known tickets; ignoring this poll", len(seen))
                else:
                    new = sorted(k for k in cur if k not in seen)
                    gone = sorted(k for k in seen if k not in cur)
                    changed = sorted(k for k in cur if k in seen and seen[k].get("status") != cur[k].get("status"))
                    for k in new:
                        try:
                            notify_new_ticket(client, k, cur[k])
                        except Exception:
                            log.exception("could not post the new-ticket notice for %s", k)
                    SEEN_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=1))
                    if new or gone or changed:
                        log.info("jira poller: new=%s gone=%s status_changed=%s", new, gone, changed)
                        pending_sync = True
                if pending_sync and table_sync(client):
                    pending_sync = False
        except Exception as exc:
            code = getattr(exc, "code", None)
            log.warning("jira poller: %s%s", type(exc).__name__, f" HTTP {code}" if code else "")
        time.sleep(POLL_SECONDS)


# ---------- hourly review-feedback watch (moved here from the desktop app, 2026-11-16) ----------

WATCH_STAMP = BASE / "watch-last-run.txt"
WATCH_EVERY = 3600
WATCH_CHECK_SECONDS = 300  # safety net only: review-channel events trigger the check instantly (owner 2026-11-18)


_watch_lock = threading.Lock()   # one reload at a time: board polls, events and the worker all call _watch()


def _watch():
    """watch.py — the feedback watch and table sync in plain Python (owner request 2026-11-17), bound to this module."""
    import importlib
    import dashboard
    import watch
    with _watch_lock:
        mtime = Path(watch.__file__).stat().st_mtime
        if getattr(watch, "_loaded_mtime", mtime) != mtime:   # edited since it was loaded: pick up the change, no restart
            watch = importlib.reload(watch)
        watch._loaded_mtime = mtime
        watch.bind(sys.modules[__name__], dashboard)
        return watch


def run_feedback_watch() -> tuple[bool, str]:
    # No Claude run any more: Python reads Jira, the threads and the List; Claude is asked only about a reviewer
    # message no pattern decides (one short call, prompts/feedback_check.md).
    return _watch().feedback_watch()


# ---------- review-channel events (owner request 2026-11-17) ----------
# The Claude Report app subscribes to message.channels / message.groups ON BEHALF OF THE OWNER (user events), so every
# message in the review channel reaches the bridge over Socket Mode the moment it is posted. The bot is not in
# the channel and posts nothing there. A message drops the board's cached copy of its thread and re-checks at once:
# the card moves, and a reviewer reply starts the feedback watch (dashboard.state → trigger_watch). Polling stays only
# as a safety net (WATCH_CHECK_SECONDS).
REVIEW_CHANNEL = workspace.REVIEW_CHANNEL
review_events = {"count": 0, "last": 0.0}


def review_channel_event(event: dict) -> None:
    msg = event.get("message") or {}   # edits carry the message inside
    thread_ts = event.get("thread_ts") or msg.get("thread_ts") or event.get("ts") or msg.get("ts")
    review_events.update(count=review_events["count"] + 1, last=time.time())
    who = "owner" if (event.get("user") or msg.get("user")) == OWNER else "someone else"
    log.info("review channel: message in thread %s from %s%s", thread_ts, who,
             f" ({event.get('subtype')})" if event.get("subtype") else "")
    try:
        import dashboard
        dashboard.mark_thread_stale(REVIEW_CHANNEL, thread_ts)
        if "browse/NJ-" in (event.get("text") or "") and not event.get("thread_ts"):   # a new ticket thread
            import watch
            watch._index["at"] = 0.0
    except Exception:
        log.exception("review channel event: cache drop failed")

    def check():
        time.sleep(1.5)   # let conversations.replies catch up with the new message
        try:
            import dashboard
            dashboard._maybe_reload()
            if getattr(dashboard, "b", None) is not None:
                dashboard.state()
        except Exception:
            log.exception("review channel event: re-check failed")
        if who == "owner" and event.get("subtype") not in ("message_deleted", "message_changed"):
            try:   # the owner answered: the ticket's row moves to Waiting on the reviewer (owner request 2026-11-18)
                log.info("review channel: %s", _watch().reply_sent(thread_ts))
            except Exception:
                log.exception("review channel event: table update failed")

    threading.Thread(target=check, daemon=True, name="review-event-check").start()


def feedback_watch_loop(client) -> None:
    failing, retry_at = False, 0.0
    while True:
        try:
            last = float(WATCH_STAMP.read_text()) if WATCH_STAMP.exists() else 0.0
            if time.localtime().tm_wday < 5 and time.time() - last >= WATCH_EVERY and time.time() >= retry_at:
                prev = WATCH_STAMP.read_text() if WATCH_STAMP.exists() else None
                WATCH_STAMP.write_text(str(time.time()))
                t0 = time.time()
                log.info("feedback watch started")
                ok, text = run_feedback_watch()
                log.info("feedback watch %s in %ss: %s", "finished" if ok else "FAILED", int(time.time() - t0),
                         text[:200].replace("\n", " "))
                if text == _watch().BUSY:   # it read nothing: replies since the last real run stay unchecked
                    if prev is not None:
                        WATCH_STAMP.write_text(prev)
                    retry_at = time.time() + 60   # and it is no success: the failure warning stays as it was
                else:
                    if not ok and not failing:
                        client.chat_postMessage(channel=CHANNEL, text=(
                            "⚠️ The hourly review-feedback watch failed, so new feedback may not be reported.\n"
                            f"`{text[:250]}`\nIt retries every hour; details in `{LOG}`."))
                    elif ok and failing:
                        client.chat_postMessage(channel=CHANNEL, text="✅ The hourly review-feedback watch works again.")
                    failing = not ok
        except Exception:
            log.exception("feedback watch loop error")
        # Every 10 seconds, every day, app open or closed (owner request 2026-11-17): a no-Claude look at the review
        # threads (dashboard.state, sharing the board's 10 s thread cache, so having the app open adds no reads). A
        # reviewer reply the watch has not seen starts the feedback watch right away (trigger_watch).
        try:
            import dashboard
            dashboard._maybe_reload()
            if getattr(dashboard, "b", None) is not None:
                dashboard.state()
        except Exception:
            log.exception("reviewer check failed")
        time.sleep(WATCH_CHECK_SECONDS)


DAILY_STAMP = BASE / "daily-report-last.txt"
DAILY_AT = (17, 40)   # local time, weekdays (owner decision 2026-11-16)


def daily_report_loop(client) -> None:
    """At 17:40 on weekdays: the day's report lines for the daily report channel, posted to the report channel.
    The content lives in dashboard.daily_post (live-reloaded); a late start the same evening still posts once."""
    while True:
        try:
            now = time.localtime()
            day = time.strftime("%Y-%m-%d", now)
            last = DAILY_STAMP.read_text().strip() if DAILY_STAMP.exists() else ""
            if now.tm_wday < 5 and (now.tm_hour, now.tm_min) >= DAILY_AT and last != day:
                DAILY_STAMP.write_text(day)
                import dashboard
                dashboard._maybe_reload()
                dashboard.daily_post(client, day)
        except Exception:
            log.exception("daily report failed")
        time.sleep(30)


def clean_override(o) -> dict | None:
    """A per-run model / effort from the confirm dialog (owner 2026-11-20), or None if it is not a valid pick."""
    if isinstance(o, dict) and o.get("model") in MODELS and o.get("effort") in EFFORTS:
        return {"model": o["model"], "effort": o["effort"]}
    return None


def start_migrate_job(client, key: str, thread_ts: str, override: dict | None = None) -> int:
    """Queue a migration (the dashboard's Migrate →) in an existing thread. Returns how many jobs are ahead."""
    position = len(waiting) + (1 if current["key"] else 0)
    update_thread(thread_ts, key=key, status="queued", started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    waiting.append(key)
    jobs.put({"kind": "start", "key": key, "thread_ts": thread_ts, "override": clean_override(override)})
    log.info("queued %s thread=%s", key, thread_ts)
    return position


def queue_fix(client, key: str, link: str, override: dict | None = None) -> int:
    """Queue a fix round (the dashboard's Fix Now →) in a new run thread. Returns how many jobs are ahead, or -1 when
    a fix for the ticket is already queued or running."""
    label = f"{key} (fix)"
    if label in waiting or (current.get("key") == key and current.get("kind") == "fix") or side.get("key") == key:
        log.info("fix for %s already queued/running; not duplicated", key)
        return -1
    position = len(waiting) + (1 if current["key"] else 0)
    head = f"🛠 *{key}* — fixing review feedback · {'started' if position == 0 else 'queued'} · session `{key}-fix`"
    thread_ts = open_run_thread(client, key, head)
    update_thread(thread_ts, key=key, mode="fix", status="queued", started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    waiting.append(label)
    jobs.put({"kind": "fix", "key": key, "label": label, "link": link, "thread_ts": thread_ts,
              "override": clean_override(override)})
    log.info("queued fix %s thread=%s", key, thread_ts)
    return position


# ---------- Slack app ----------

def build_app() -> App:
    app = App(token=read_secret("slack-bot-token"))


    @app.event("message")
    def on_message(event, client):
        if event.get("channel") == REVIEW_CHANNEL:   # a user event (owner 2026-11-17): the team review channel
            review_channel_event(event)
            return
        if event.get("channel") != CHANNEL or event.get("user") != OWNER or event.get("subtype"):
            return
        thread_ts = event.get("thread_ts")
        if not thread_ts or thread_ts == event.get("ts"):
            return
        entry = get_thread(thread_ts)
        if not entry:
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        jobs.put({"kind": "reply", "key": entry["key"], "thread_ts": thread_ts, "text": text})
        log.info("reply queued for %s", entry["key"])

    return app


def main() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    mark_interrupted_runs()
    app = build_app()
    threading.Thread(target=worker, args=(app.client,), daemon=True).start()
    threading.Thread(target=worker, args=(app.client, side_jobs, side), daemon=True, name="side-worker").start()
    threading.Thread(target=jira_poller, args=(app.client,), daemon=True).start()
    threading.Thread(target=feedback_watch_loop, args=(app.client,), daemon=True).start()
    threading.Thread(target=daily_report_loop, args=(app.client,), daemon=True).start()
    threading.Thread(target=addon_watcher, daemon=True, name="addon-watcher").start()
    try:
        import dashboard
        dashboard.start(sys.modules[__name__], app.client)
    except Exception:
        log.exception("dashboard did not start")
    log.info("bridge starting")
    SocketModeHandler(app, read_secret("slack-app-token")).start()


if __name__ == "__main__":
    main()
