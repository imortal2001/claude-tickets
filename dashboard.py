"""Claude Tickets dashboard — served by the Slack bridge on 127.0.0.1 only.

`start(bridge_module, slack_client)` runs a small HTTP server in a thread:
  GET  /                    the page (dashboard.html next to this file; its dashboard.css and dashboard.js too)
  GET  /api/state           tickets, runs, PRs, feedback, health
  GET  /api/log?session=ID&offset=N   readable lines of a session transcript from byte offset N
  POST /api/action          {"action": "migrate|fix|review|stop|resume", "key": "NJ-…"}
  POST /api/settings        {"model", "effort"} (migrate) / {"fix": {…}} / {"review": {…}}  model and effort of the next runs
  POST /api/note            {"key": "NJ-…", "text": "…"}  the running session's plan report into its run chat
POSTs need the X-Dashboard header (a custom header forces a CORS preflight, which this server never
grants), and every request must name this host — so other web pages cannot drive it.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
import urllib.request
import urllib.error
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import workspace as W

# The bridge live-reloads this module; reload its settings with it, so the module and its settings never come from
# different versions (watch.py shares this same module object).
importlib.reload(W)

PORT = 8790
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
HERE = Path(__file__).resolve().parent
KEY_RE = re.compile(r"^NJ-\d{4,6}$")
SID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

b = None        # the bridge module
client = None   # Slack WebClient
_cache: dict = {}
_cache_lock = threading.Lock()


def tsnum(ts) -> float:
    """A run id as a time: Slack thread ts, or a local conversation id ("local-<epoch>")."""
    try:
        return float(str(ts).replace("local-", ""))
    except ValueError:
        return 0.0


_inflight: dict = {}   # one lock per cache entry: only one caller fetches it, the others wait for that answer


def cached(name: str, ttl: float, fn):
    """fn() at most once per ttl seconds, single-flight: concurrent callers (the app's polls, the bridge's check, an
    event re-check) share one fetch instead of each calling Slack (code review 2026-11-20). If the fetch fails
    (Slack throttling, a network blip) and an earlier value exists, keep showing it and try again after the ttl
    (at least 30 s)."""
    with _cache_lock:
        hit = _cache.get(name)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        lock = _inflight.setdefault(name, threading.Lock())
    with lock:
        with _cache_lock:   # another caller may have refreshed it while this one waited
            hit = _cache.get(name)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        try:
            value = fn()
            if isinstance(value, dict) and value.get("error") and hit:   # e.g. "ratelimited": keep the good copy
                raise RuntimeError(value["error"])
        except Exception as exc:
            if not hit:
                raise
            if b is not None:
                b.log.warning("%s: read failed (%s); keeping the last copy for 30 s", name, str(exc)[:80])
            with _cache_lock:
                _cache[name] = (time.time() + max(0, 30 - ttl), hit[1])
            return hit[1]
        with _cache_lock:
            _cache[name] = (time.time(), value)
        return value


# ---------- data ----------

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def fetch_prs() -> dict:
    """Open PRs whose title names a ticket: key -> {main: {...}, stg: [...]}."""
    out: dict = {}
    try:
        raw = subprocess.run(
            ["gh", "pr", "list", "--repo", W.GH_REPO, "--state", "open", "--limit", "400",
             "--json", "number,title,headRefName,mergeable,baseRefName,url,isDraft,updatedAt"],
            capture_output=True, text=True, timeout=60).stdout
        prs = json.loads(raw or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return out
    for pr in prs:
        found = {f"NJ-{n}" for n in re.findall(r"(?i)nj[-_ ]?(\d{5,6})", pr.get("title", "") + " " + pr.get("headRefName", ""))}
        for key in found:
            slot = out.setdefault(key, {"main": None, "stg": []})
            item = {"number": pr["number"], "url": pr["url"], "mergeable": pr.get("mergeable"),
                    "draft": pr.get("isDraft"), "base": pr.get("baseRefName")}
            if pr.get("baseRefName") == "main":
                if not slot["main"] or pr["number"] > slot["main"]["number"]:
                    slot["main"] = item
            else:
                slot["stg"].append(item)
    return out


def fetch_next_steps() -> dict:
    """Ticket table 'Next step' and 'Waiting on' text per key (best effort, via the bot's lists:read)."""
    out: dict = {}
    # A failed read raises, so cached() keeps the last good copy. Returning {} here (as before 2026-11-19) cached an
    # empty table for 2 minutes: five tickets lost their thread link and showed "reply to the reviewer" again.
    res = client.api_call("slackLists.items.list", json={"list_id": LIST_ID, "limit": 200})
    if not res.get("ok"):
        raise RuntimeError(f"slackLists.items.list: {res.get('error')}")
    for item in res.get("items") or []:
        fields = {f.get("key") or f.get("column_id"): f for f in item.get("fields") or []}
        ticket = json.dumps(fields.get(COLS["ticket"]) or {})
        m = re.search(r"NJ-\d{4,6}", ticket)
        if not m:
            continue
        def plain(col: str) -> str:
            f = fields.get(col) or {}
            text = f.get("text") or ""
            if not text and f.get("rich_text"):
                text = "".join(re.findall(r'"text":\s*"((?:[^"\\]|\\.)*)"', json.dumps(f["rich_text"], ensure_ascii=False)))
                text = text.encode().decode("unicode_escape", "ignore") if "\\u" in text else text
            return text.strip()
        link = re.search(r"archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})", json.dumps(fields.get("thread") or {}).replace("\\/", "/"))
        out[m.group(0)] = {"next": plain(COLS["next"]), "summary": plain(COLS["summary"]),
                           "thread": {"channel": link.group(1), "ts": f"{link.group(2)}.{link.group(3)}"} if link else None}
    return out


def _tail_records(session_id: str, size: int = 300_000) -> list[dict]:
    """The last records of a session's transcript (about the last 300 KB), oldest first; [] when there is none."""
    f = transcript_path(session_id)
    if not f:
        return []
    try:
        end = f.stat().st_size
        with f.open("rb") as fh:
            fh.seek(max(0, end - size))
            lines = fh.read().decode("utf-8", "ignore").splitlines()[1 if end > size else 0:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _blocks(rec: dict) -> list[dict]:
    content = (rec.get("message") or {}).get("content")
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []


def last_activity(session_id: str, records: list[dict] | None = None) -> str:
    for d in reversed(records if records is not None else _tail_records(session_id)):
        if d.get("type") != "assistant":
            continue
        for c in reversed(_blocks(d)):
            if c.get("type") == "text" and c.get("text", "").strip():
                return c["text"].strip().replace("\n", " ")[:220]
            if c.get("type") == "tool_use":
                i = c.get("input") or {}
                return "→ " + str(i.get("description") or c.get("name"))[:200]
    return ""


def run_waiting(session_id: str, records: list[dict] | None = None) -> dict:
    """What a running session is waiting on (owner request 2026-11-20: a long wait looked like a stuck chat).
    calls: its tool calls with no result yet, oldest first, each {tool, what, since}; helpers_at: when its helper
    agents last wrote (only while it waits on one); notes: the owner's add-on notes still queued for it. A note
    reaches the session only after its current call ends, so this is also when the chat's question gets read."""
    recs = records if records is not None else _tail_records(session_id)
    done = {c.get("tool_use_id") for r in recs for c in _blocks(r) if c.get("type") == "tool_result"}
    calls = []
    for r in recs:
        if r.get("type") != "assistant":
            continue
        for c in _blocks(r):
            if c.get("type") == "tool_use" and c.get("id") not in done:
                i = c.get("input") or {}
                try:
                    since = datetime.fromisoformat(str(r.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    since = None
                calls.append({"tool": c.get("name"), "since": since,
                              "what": str(i.get("description") or i.get("subagent_type") or c.get("name"))[:120]})
    helpers_at = None
    f = transcript_path(session_id)
    if f and any(c["tool"] in ("Agent", "Task") for c in calls):
        times = [s.stat().st_mtime for s in (f.parent / session_id / "subagents").glob("*.jsonl")]
        helpers_at = max(times) if times else None
    queued = getattr(b, "ADDONS", None)
    notes = 0
    if queued and (queued / f"{session_id}.jsonl").exists():
        notes = sum(1 for line in (queued / f"{session_id}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    return {"calls": calls, "helpers_at": helpers_at, "notes": notes} if calls or notes else {}


def transcript_path(session_id: str) -> Path | None:
    if not SID_RE.match(session_id or ""):
        return None
    for d in (b.HOME / ".claude" / "projects").glob(f"*{W.REPO_NAME}*"):
        f = d / f"{session_id}.jsonl"
        if f.exists():
            return f
    return None


def watch_health() -> dict:
    stamp = b.WATCH_STAMP
    last = float(stamp.read_text()) if stamp.exists() else 0
    line = ""
    try:
        for l in reversed((b.HOME / ".claude/jira-automation/activity.log").read_text().splitlines()):
            if "feedback-watch" in l:
                line = l
                break
    except OSError:
        pass
    return {"last_run": last, "last_line": line[:220]}


# Slack now pushes every review-channel message to the bridge (user events, owner 2026-11-17), which drops that
# thread's cached copy at once — so the timed re-read is only a safety net. 10 s re-reads of 10 threads hit Slack's
# rate limit (429s on 2026-11-18 09:19–09:35).
THREAD_TTL_ACTIVE = 300
THREAD_TTL_OTHER = 300


def thread_cached(thread: dict, ttl: float) -> dict:
    """A review thread through the cache. A failed read with no earlier copy (e.g. Slack's 429 right after an event
    marked it stale) returns an error dict instead of failing the whole board request."""
    try:
        return cached(f"thread:{thread['channel']}:{thread['ts']}", ttl, lambda: read_thread(thread["channel"], thread["ts"]))
    except Exception as exc:
        return {"error": type(exc).__name__}


def mark_thread_stale(channel: str, ts: str) -> None:
    """A new message in this thread (Slack event): re-read it on the next look, but keep the old copy as a fallback."""
    with _cache_lock:
        hit = _cache.get(f"thread:{channel}:{ts}")
        if hit:
            _cache[f"thread:{channel}:{ts}"] = (0.0, hit[1])


def owner_replied_since(thread: dict | None, epoch: float, ttl: float = THREAD_TTL_OTHER) -> bool | None:
    """Has the owner posted in the ticket's review thread after `epoch`? None when the thread can't be read."""
    if not thread:
        return None
    data = thread_cached(thread, ttl)
    if data.get("error"):
        return None
    return any(m.get("mine") and float(m["ts"]) > epoch for m in data.get("messages") or [])


def review_turn(thread: dict | None, ttl: float = THREAD_TTL_OTHER) -> dict | None:
    """Whose turn it is in the review thread: when the newest message is the owner's, the reviewer's."""
    if not thread:
        return None
    data = thread_cached(thread, ttl)
    msgs = data.get("messages") or []
    if data.get("error") or len(msgs) < 2:
        return None
    last = msgs[-1]
    return {"owner_last": bool(last.get("mine")), "ts": last["ts"], "time": last.get("time")}


def run_settings_view() -> dict | None:
    """The model and effort the next run uses, and the choices (owner request 2026-11-17). None until the bridge has them."""
    if not hasattr(b, "run_settings"):
        return None
    return {**b.run_settings(), "models": b.MODELS, "efforts": list(b.EFFORTS)}


def set_run_settings(body: dict) -> tuple[bool, str]:
    """body: any of {"model", "effort"} (migrate), {"fix": {…}}, {"review": {…}}; what is left out stays."""
    if not hasattr(b, "run_settings"):
        return False, "Restart the bridge first"
    cur = b.run_settings()
    new = {"model": body.get("model") or cur["model"], "effort": body.get("effort") or cur["effort"]}
    for k in ("fix", "review", "watch"):
        if k not in cur:   # a bridge from before this pick existed
            continue
        part = body.get(k) or {}
        new[k] = {"model": part.get("model") or cur[k]["model"], "effort": part.get("effort") or cur[k]["effort"]}
    for x in (new, new["fix"], new["review"], *([new["watch"]] if "watch" in new else [])):
        if x["model"] not in b.MODELS or x["effort"] not in b.EFFORTS:
            return False, "Unknown model or effort"
    tmp = b.RUN_SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(new) + "\n", encoding="utf-8")
    tmp.replace(b.RUN_SETTINGS)
    b.log.info("run settings -> %s", new)
    which = next((k for k in ("fix", "review", "watch") if k in body), None)
    pick = new[which] if which else new
    return True, f"{ {'fix': 'Fix Now', 'review': 'Review', 'watch': 'Feedback watch'}.get(which, 'Migrate') }: {b.MODELS[pick['model']]} · {pick['effort']}"


def _watch_state() -> dict:
    # Kept on the bridge module so it survives this file's live reloads.
    return b.__dict__.setdefault("_dash_watch", {"running": False, "keys": set(), "next_ok": 0.0})


def trigger_watch(keys: list[str]) -> None:
    """A reviewer reply the watch has not seen starts the feedback watch now, not at the top of the hour (owner
    request 2026-11-17). One at a time; none while the hourly run is going; a failed run backs off 10 minutes and
    puts the watch stamp back, so the reply still counts as not checked."""
    w = _watch_state()
    if w["running"] or time.time() < w["next_ok"]:
        return
    if subprocess.run(["pgrep", "-f", "[f]eedback-watch"], capture_output=True).returncode == 0:
        return
    w.update(running=True, keys=set(keys), next_ok=time.time() + 120)

    def go():
        prev = b.WATCH_STAMP.read_text() if b.WATCH_STAMP.exists() else None
        b.WATCH_STAMP.write_text(str(time.time()))
        ok, text = False, ""
        try:
            b.log.info("feedback watch started (new reviewer reply on %s)", ", ".join(sorted(keys)))
            ok, text = b.run_feedback_watch()
            b.log.info("feedback watch %s: %s", "finished" if ok else "FAILED", text[:200].replace("\n", " "))
        except Exception:
            b.log.exception("triggered feedback watch failed")
        finally:
            if not ok:
                if prev is not None:
                    b.WATCH_STAMP.write_text(prev)
                w["next_ok"] = time.time() + 600
            w.update(running=False, keys=set())

    threading.Thread(target=go, daemon=True, name="feedback-watch-now").start()


def run_stats(days: int = 7) -> dict:
    """Claude runs that finished in the last `days` days, for the Report's stat cards (owner 2026-11-20)."""
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    with b._state_lock:
        threads = list(b.load_state()["threads"].values())
    runs = [v for v in threads if (v.get("mode") or "migrate") in ("migrate", "fix", "review")
            and (v.get("finished_at") or v.get("stopped_at") or v.get("last_run") or "")[:10] >= since
            and v.get("status") in ("finished", "ended_turn", "stopped", "failed")]
    by = {}
    for v in runs:
        m = v.get("mode") or "migrate"
        by[m] = by.get(m, 0) + 1
    return {"days": days, "runs": len(runs), "finished": sum(1 for v in runs if v.get("status") == "finished"), "by_mode": by}


def state() -> dict:
    seen = read_json(b.SEEN_FILE, {})
    jobs_state = b.load_state()
    auto = read_json(b.HOME / ".claude/jira-automation/state.json", {}).get("tickets", {})
    try:   # when the hourly feedback watch last started; a reviewer message after it is not checked yet
        watch_at = float(b.WATCH_STAMP.read_text())
    except (OSError, ValueError, AttributeError):
        watch_at = time.time()
    prs = cached("prs", 120, fetch_prs)
    try:
        steps = cached("steps", 120, fetch_next_steps)
    except Exception:   # no good copy yet either: carry on without the table's text
        steps = {}
    live = cached("live", 10, b.scan_live_sessions)
    waiting = list(b.waiting)
    cur = dict(b.current)

    runs_by_key: dict = {}
    for ts, v in jobs_state.get("threads", {}).items():
        if v.get("key"):
            runs_by_key.setdefault(v["key"], []).append(dict(v, thread_ts=ts))
    tickets = []
    fresh = []   # tickets with a reviewer reply the watch has not seen
    try:   # the watch owns the "which ticket starts this thread" rule; the board asks it (code review 11-20)
        thread_owners_of = b._watch().thread_owners if hasattr(b, "_watch") else (lambda ts: [])
    except Exception:
        b.log.warning("thread owners unavailable this refresh", exc_info=True)
        thread_owners_of = lambda ts: []
    w = _watch_state()
    for key, info in seen.items():
        runs = sorted(runs_by_key.get(key, []), key=lambda r: tsnum(r["thread_ts"]), reverse=True)
        running = cur.get("key") == key
        # A chat (Chat →) is not a migrate / fix run: it never sets the card's run state, except while it is the
        # job running now. Chat → is its own conversation with Claude, never a run's thread (owner, 2026-11-16).
        latest = (next((r for r in runs if r["thread_ts"] == cur.get("thread_ts")), None) if running else None) \
            or next((r for r in runs if r.get("mode") != "chat"), None)
        # Chat → opens the last session's conversation with its messages (owner, 2026-11-16): the newest
        # conversation of any kind — a New chat, or the last migrate / fix run (replies continue that session).
        chat_ts = runs[0]["thread_ts"] if runs else None
        queued = key in waiting or f"{key} (fix)" in waiting or f"{key} (review)" in waiting or f"{key} (resume)" in waiting
        digits = key.split("-", 1)[1]
        elsewhere = [s for s in live if re.search(rf"(?<!\d){digits}(?!\d)", (s.get("title") or "") + " " + (s.get("cwd") or ""))
                     or (s.get("branch") == key)]
        fb = auto.get(key) or {}
        fb_ts = fb.get("feedback_ts")
        # The hourly watch records whether the newest reviewer feedback is still unanswered; a /fix started
        # after that feedback also counts as handling it.
        fb_open = bool(fb.get("feedback_open")) and not any(
            r.get("mode") == "fix" and fb_ts and tsnum(r["thread_ts"]) > tsnum(fb_ts) for r in runs)
        # A reviewer message newer than the last watch run counts as feedback straight away, marked "not checked yet";
        # the next hourly watch confirms or clears it (owner request 2026-11-17).
        rthread = (steps.get(key) or {}).get("thread") or (auto.get(key) or {}).get("review_thread")
        # Another ticket's thread (its Jira link starts it) is shown as related, never used as this ticket's own
        # review thread for feedback, waits or replies (owner 2026-11-20: NJ-102761 had NJ-102617's thread).
        related = None
        owners = thread_owners_of(rthread["ts"]) if rthread else []
        if owners and key not in owners:
            related = {**rthread, "owner": owners[0]}
            rthread = None
        ttl = THREAD_TTL_ACTIVE if info.get("status") in ("In Review", "Dev In Progress") else THREAD_TTL_OTHER
        rturn = review_turn(rthread, ttl)
        checking = w["running"] and key in w["keys"]   # the watch started for this reply is still reading it
        unchecked = bool(
            not fb_open and rturn and not rturn["owner_last"] and info.get("status") in ("In Review", "Dev In Progress")
            and (tsnum(rturn["ts"]) > watch_at or checking) and tsnum(rturn["ts"]) > tsnum(fb_ts or "0")
            and not running and not queued
            and not any(r.get("mode") == "fix" and tsnum(r["thread_ts"]) > tsnum(rturn["ts"]) for r in runs))
        fb_open = fb_open or unchecked
        if unchecked and not checking:
            fresh.append(key)
        live_sid = latest["session_id"] if (latest and latest.get("session_id") and running) else None
        records = _tail_records(live_sid) if live_sid else []
        activity = last_activity(live_sid, records) if live_sid else ""
        waiting = run_waiting(live_sid, records) if live_sid else {}
        # Finished fix / migration with no reply from the owner in the review thread since -> the reply is still due.
        reply = None
        work = next((r for r in runs if r.get("mode") not in ("chat", "review")), None)   # reviews / chats need no reply
        if work and work.get("status") == "finished" and not running and not queued:
            try:
                done_at = datetime.strptime(work.get("finished_at") or "", "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                done_at = None
            if done_at:
                rt = rthread
                # No thread known: "not replied" only when the ticket has no thread anywhere. If the table could not be
                # read this time, say nothing rather than move the card (2026-11-19).
                replied = owner_replied_since(rt, done_at, ttl) if rt else (None if not steps else False)
                # A run the owner said needs no reply (e.g. only a merge conflict was fixed): state.json
                # tickets.<KEY>.reply_waived_session names that run's session; a later run asks again.
                waived = fb.get("reply_waived_session") and fb.get("reply_waived_session") == work.get("session_id")
                if replied is False and not waived:
                    reply = {"needed": True, "finished": work.get("finished_at"),
                             "mode": "fix" if work.get("mode") == "fix" else "migrate", "has_thread": bool(rt)}
        tickets.append({
            "key": key, "summary": (steps.get(key) or {}).get("summary") or info.get("summary", ""),
            "jira_summary": info.get("summary", ""), "status": info.get("status", ""),
            "pr": prs.get(key, {}), "next_step": (steps.get(key) or {}).get("next", ""),
            "review_thread": rthread,
            "related_thread": related,
            "run": {
                "state": "running" if running else ("queued" if queued else (latest or {}).get("status")),
                "mode": (cur.get("kind") if running else (latest or {}).get("mode")) or ("migrate" if latest else None),
                "since": cur.get("since") if running else None,
                "session_id": (latest or {}).get("session_id"),
                "session_name": (latest or {}).get("session_name"),
                "thread": f"{W.SLACK_URL}/archives/{b.CHANNEL}/p{latest['thread_ts'].replace('.', '')}"
                          if latest and not is_local(latest["thread_ts"]) else None,
                "thread_ts": latest["thread_ts"] if latest else None,
                "when": (latest or {}).get("finished_at") or (latest or {}).get("last_run"),
                "activity": activity,
                "waiting": waiting,
                "model": (cur.get("model") if running else None) or (latest or {}).get("model"),
                "effort": (cur.get("effort") if running else None) or (latest or {}).get("effort"),
            },
            "elsewhere": [{"title": s.get("title"), "state": s.get("state"), "age_min": s.get("age_min"),
                           "last": s.get("last") or ""} for s in elsewhere],
            "reply": reply,
            "chat_ts": chat_ts,
            # Every conversation of the ticket, newest first — the chat panel's tabs.
            "convs": [{"ts": r["thread_ts"], "mode": r.get("mode") or "migrate",
                       "state": "running" if running and r["thread_ts"] == cur.get("thread_ts") else r.get("status"),
                       "session": r.get("session_name") or key,
                       "when": r.get("finished_at") or r.get("last_run") or (r.get("started") or "")[:16].replace("T", " ")}
                      for r in runs[:8]],
            "review_turn": rturn,
            "feedback": {"open": fb_open, "ts": fb_ts, "unchecked": unchecked,
                         "unchecked_time": rturn.get("time") if unchecked else None,
                         "checking": bool(unchecked and checking),
                         "link": f"{W.SLACK_URL}/archives/{REVIEW_CHANNEL}/p{fb_ts.replace('.', '')}" if fb_ts else None},
        })
    if fresh:
        trigger_watch(fresh)
    seen_age = time.time() - b.SEEN_FILE.stat().st_mtime if b.SEEN_FILE.exists() else None
    return {
        "now": time.time(),
        "cfg": W.PUBLIC,
        "tickets": tickets,
        "running": {"key": cur.get("key"), "kind": cur.get("kind"), "since": cur.get("since"),
                    "model": cur.get("model"), "effort": cur.get("effort")},
        "run_settings": run_settings_view(),
        "stats": cached("run-stats", 60, run_stats),
        "queued": waiting,
        "health": {"jira_poll_age": seen_age, "watch": watch_health(), "bridge_up": True},
    }


# ---------- transcript -> readable lines ----------

def log_lines(path: Path, offset: int) -> tuple[list[dict], int]:
    out = []
    with path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read(2_000_000)
    end = data.rfind(b"\n")
    if end < 0:
        return out, offset
    chunk, new_offset = data[: end + 1], offset + end + 1
    for raw in chunk.decode("utf-8", "ignore").splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        ts = d.get("timestamp", "")
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
        except ValueError:
            when = ""
        content = (d.get("message") or {}).get("content")
        if d.get("type") == "assistant" and isinstance(content, list):
            for c in content:
                if c.get("type") == "text" and c.get("text", "").strip():
                    out.append({"t": when, "kind": "say", "text": c["text"].strip()})
                elif c.get("type") == "tool_use":
                    i = c.get("input") or {}
                    what = i.get("description") or i.get("file_path") or i.get("pattern") or i.get("subagent_type") or ""
                    out.append({"t": when, "kind": "tool", "text": f"{c.get('name')}: {str(what)[:160]}"})
        elif d.get("type") == "user" and d.get("entrypoint") == "sdk-cli":
            text = content if isinstance(content, str) else ""
            if text.strip() and not text.lstrip().startswith("<"):
                out.append({"t": when, "kind": "prompt", "text": text.strip()[:500]})
    return out, new_offset


# ---------- actions (same paths as the Slack commands) ----------

WAITING = {"you": "you", W.REVIEWER.lower(): W.REVIEWER_OPTION, "new api": "new_api_(blocked)", "not started": "not_started", "skip": "skip"}


def table_update(key: str, waiting: str | None = None, next_text: str | None = None) -> str:
    """Set a ticket's Waiting on (a WAITING value) and / or Next step in the Slack List, with the bot."""
    res = client.api_call("slackLists.items.list", json={"list_id": LIST_ID, "limit": 200})
    row = next((it for it in res.get("items") or [] if re.search(rf"\b{re.escape(key)}\b", json.dumps(
        [f for f in it.get("fields") or [] if f.get("key") == COLS["ticket"]]))), None)
    if not row:
        return "no row"
    ids = {f.get("key"): f.get("column_id") or f.get("key") for f in row.get("fields") or []}
    cells = []
    if waiting:
        cells.append({"row_id": row["id"], "column_id": ids.get(COLS["waiting"], COLS["waiting"]), "select": [waiting]})
    if next_text:
        cells.append({"row_id": row["id"], "column_id": ids.get(COLS["next"], COLS["next"]), "rich_text": [
            {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": next_text[:900]}]}]}]})
    if not cells:
        return "nothing to change"
    r = client.api_call("slackLists.items.update", json={"list_id": LIST_ID, "cells": cells})
    _cache.pop("table", None)
    return "updated" if r.get("ok") else f"failed ({r.get('error')})"


def plan_to_table(key: str, text: str) -> None:
    """The plan report's verdict goes into the ticket table (owner request 2026-11-17): a go → Waiting on You,
    a missing API → New API (blocked); the report's *Next:* line becomes the Next step."""
    first = text.splitlines()[0] if text else ""
    if "plan*" not in first and " plan" not in first:
        return
    nxt = next((re.sub(r"^\*?Next:\*?\s*", "", l).strip() for l in text.splitlines() if re.match(r"^\*?Next:", l.strip())), "")
    if re.search(r"no-go:\s*needs a new API", first, re.I):
        waiting = WAITING["new api"]
    elif re.search(r"—\s*go\b", first):
        waiting = WAITING["you"]
    else:
        return
    try:
        what = table_update(key, waiting, f"{MIGRATING_NEXT} — {nxt}" if waiting == "you" and nxt else nxt or None)
    except Exception as exc:
        what = f"failed ({type(exc).__name__})"
    b.log.info("plan report of %s → table %s", key, what)


def fix_next_step(text: str) -> str | None:
    """A finished fix's Next step for the ticket table, from its final report (owner request 2026-11-17)."""
    lines = (text or "").strip().splitlines()
    if not lines:
        return None
    day = time.strftime("%m-%d")
    if " could not be fixed" in lines[0]:
        return f"Fix stopped {day}: " + lines[0].split(" could not be fixed", 1)[1].lstrip(": ").strip()
    if " is fixed" not in lines[0]:
        return None
    status = next((l for l in lines if l.startswith("Status:")), "")
    stg = next((l for l in lines if l.startswith("Staging PR:")), "")
    jira = "In Review" if re.search(r"→\s*In Review\s*$", status) else (status.replace("Status:", "Jira").strip() or "Jira unchanged")
    m = re.search(r"/pull/(\d+)", stg)
    if m:
        return f"Fixed {day} · {jira} · stg PR #{m.group(1)} ready — merge + deploy, then reply in the thread"
    return f"Fixed {day} · {jira} · no staging PR needed — reply in the thread"


def post_note(key: str, text: str) -> tuple[bool, str]:
    """A running session's mid-run report (the /migrate plan report, owner request 2026-11-17) into its own run
    conversation: only the ticket that is running right now, so a session cannot write into another ticket's chat."""
    text = (text or "").strip()
    if not KEY_RE.match(key or "") or not text:
        return False, "Need a ticket key and text."
    if b.current.get("key") != key or not b.current.get("thread_ts"):
        return False, f"{key} is not running now."
    if len(text) > 6000:
        text = text[:5980] + "\n…(see the session)"
    b.post(client, b.current["thread_ts"], text)
    b.log.info("note from the %s session posted to %s", key, b.current["thread_ts"])
    threading.Thread(target=plan_to_table, args=(key, text), daemon=True).start()
    return True, "Posted to the run's chat."


def act(action: str, key: str, override: dict | None = None) -> tuple:
    """override: this run's model / effort from the confirm dialog (owner 2026-11-20); None = the Models ▾ pick."""
    if not KEY_RE.match(key or ""):
        return False, "Not a ticket key."
    override = b.clean_override(override) if hasattr(b, "clean_override") else None
    if action == "migrate":
        if key in b.waiting or b.current.get("key") == key:
            return False, f"{key} is already queued or running."
        busy = b.busy_elsewhere(key)
        if busy:
            return False, busy
        position = len(b.waiting) + (1 if b.current["key"] else 0)
        head = f"▶ *{key}* — {'started' if position == 0 else 'queued'} · session `{key}`"
        if hasattr(b, "open_run_thread"):
            tid = b.open_run_thread(client, key, head)
        else:
            tid = client.chat_postMessage(channel=b.CHANNEL, text=head)["ts"]
        b.start_migrate_job(client, key, tid, **({"override": override} if override else {}))
        # the run's thread goes back so the app opens its chat right away (owner request 2026-11-17)
        return True, f"{key}: {'starting' if position == 0 else f'queued ({position} ahead)'}.", {"ts": tid}
    if action == "fix":
        busy = b.busy_elsewhere(key)
        if busy:
            return False, busy
        position = b.queue_fix(client, key, "", **({"override": override} if override else {}))
        if position < 0:
            return False, f"Already fixing {key}."
        # queue_fix does not hand back its thread: it is this ticket's newest queued fix thread
        with b._state_lock:
            threads = b.load_state()["threads"]
        fixes = [ts for ts, v in threads.items() if v.get("key") == key and v.get("mode") == "fix" and v.get("status") == "queued"]
        extra = {"ts": max(fixes, key=tsnum)} if fixes else {}
        return True, f"{key} (fix): {'starting' if position == 0 else f'queued ({position} ahead)'}.", extra
    if action == "review":
        busy = b.busy_elsewhere(key)
        if busy:
            return False, busy
        label = f"{key} (review)"
        if label in b.waiting or b.current.get("key") == key:
            return False, f"{key} is already queued or running."
        position = len(b.waiting) + (1 if b.current["key"] else 0)
        head = f"🔍 *{key}* — reviewing the PR (report only) · {'started' if position == 0 else 'queued'} · session `{key}-review`"
        tid = b.open_run_thread(client, key, head)
        b.update_thread(tid, key=key, mode="review", status="queued", started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        b.waiting.append(label)
        b.jobs.put({"kind": "review", "key": key, "label": label, "thread_ts": tid, "override": override})
        b.log.info("dashboard review queued for %s (%s)", key, tid)
        return True, f"{key} (review): {'starting' if position == 0 else f'queued ({position} ahead)'}.", {"ts": tid}
    if action == "stop":
        if b.current.get("key") == key:
            proc = b.current.get("proc")
            if proc and proc.poll() is None:
                proc.terminate()
                return True, f"Stopping {key}…"
        for label in (key, f"{key} (fix)", f"{key} (review)", f"{key} (resume)"):
            if label in b.waiting:
                b.cancelled.add(label)
                b.waiting.remove(label)
                return True, f"Removed {label} from the queue."
        return False, f"{key} is not running or queued."
    if action == "resume":
        if b.current.get("key") == key or any(l in b.waiting for l in (key, f"{key} (fix)", f"{key} (review)", f"{key} (resume)")):
            return False, f"{key} is already queued or running."
        with b._state_lock:
            threads = b.load_state()["threads"]
        mine = sorted(((ts, v) for ts, v in threads.items() if v.get("key") == key), key=lambda x: tsnum(x[0]))
        if not mine or not mine[-1][1].get("session_id"):
            return False, f"No Slack-started session for {key} to resume."
        ts, rec = mine[-1]
        if rec.get("status") not in ("stopped", "failed", "ended_turn"):
            return False, f"{key}'s last run is {rec.get('status')}; nothing to resume."
        busy = b.busy_elsewhere(key)
        if busy:
            return False, busy
        why = ("Your last turn ENDED before the work was finished — you run headless, so ending the turn ended the "
               "process and killed every background subagent or command you were waiting for (their work may be "
               "missing or half-written: check git status and the files before relying on it)."
               if rec.get("status") == "ended_turn" else
               "You were interrupted (stopped, failed, or the bridge restarted), so any command, test run or subagent "
               "that was still running was stopped.")
        b.update_thread(ts, status="queued")
        b.waiting.append(f"{key} (resume)")
        b.jobs.put({"kind": "reply", "key": key, "thread_ts": ts, "resume": True, "label": f"{key} (resume)", "override": override, "text": (
            why + " Continue the task from where you stopped — re-run whatever did not finish, running subagents in "
            "the foreground this time — and finish it as the runbook says, ending with the final report.")})
        b.post(client, ts, f"▶ Resuming {key} where it stopped (from the dashboard).")
        return True, f"Resuming {key}…", {"ts": ts}
    return False, "Unknown action."


# ---------- Slack threads and files (read only) ----------

REVIEW_CHANNEL = W.REVIEW_CHANNEL   # the review channel — read with the owner's user token
TS_RE = re.compile(r"^\d{10}\.\d{6}$")
FILE_RE = re.compile(r"^F[A-Z0-9]{6,}$")
_names: dict = {}


def token_for(channel: str) -> str | None:
    """the report channel: the bot. The team review channel: the owner's user token (the bot is not in it)."""
    name = "slack-user-token" if channel == REVIEW_CHANNEL else "slack-bot-token"
    f = b.SECRETS / name
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


def slack_get(token: str, method: str, **params) -> dict:
    """A Slack Web API GET. A cut-off or failed read is retried twice (a 327 KB history page came back cut off
    at 74 KB on 2026-11-17 and failed the feedback watch)."""
    import http.client
    from urllib.parse import urlencode
    for attempt in range(3):
        req = urllib.request.Request(f"https://slack.com/api/{method}?{urlencode(params)}",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt * 2)


_photos: dict = {}


def user_name(token: str, uid: str) -> str:
    if uid not in _names:
        try:  # the bot has no users:read; the owner's user token does
            ut = b.SECRETS / "slack-user-token"
            u = slack_get(ut.read_text(encoding="utf-8").strip() if ut.exists() else token, "users.info", user=uid).get("user") or {}
            prof = u.get("profile") or {}
            _names[uid] = prof.get("display_name") or u.get("real_name") or uid
            # Their Slack profile photo for the thread / chat avatars (owner 2026-11-20); "" = initials instead.
            _photos[uid] = prof.get("image_72") or prof.get("image_48") or ""
        except Exception:
            _names[uid] = uid
    return _names[uid]


def user_photo(uid: str | None) -> str:
    if not uid:
        return ""
    if uid not in _photos:
        token = token_for(REVIEW_CHANNEL) or ""
        user_name(token, uid)
    return _photos.get(uid, "")


LOCAL_RE = re.compile(r"^local-\d{10}\.\d{6}$")


def is_local(ts: str) -> bool:
    return bool(LOCAL_RE.match(ts or ""))


def read_local(ts: str) -> dict:
    """A run's conversation kept by the bridge instead of a Slack thread."""
    rec = b.runlog_read(ts) if hasattr(b, "runlog_read") else {}
    names = {"claude": "Claude", "system": "Claude Tickets", "you": "You"}
    me = user_photo(b.OWNER)
    msgs = [{"ts": f"{m['t']:.6f}", "who": names.get(m.get("who"), "Claude"), "text": m.get("text", ""),
             "mine": m.get("who") == "you", "bot": m.get("who") != "you", "photo": me if m.get("who") == "you" else "",
             "time": datetime.fromtimestamp(m["t"]).strftime("%m-%d %H:%M"), "files": []}
            for m in rec.get("messages") or []]
    return {"channel": b.CHANNEL, "ts": ts, "messages": msgs, "link": None, "local": True}


def read_thread(channel: str, ts: str) -> dict:
    if channel == b.CHANNEL and (ts.startswith(("new:", "fresh:")) or (is_local(ts) and (b.get_thread(ts) or {}).get("mode") == "chat")):
        return chat_view(ts)
    if channel == b.CHANNEL and is_local(ts):
        return read_local(ts)
    if channel not in (REVIEW_CHANNEL, b.CHANNEL) or not TS_RE.match(ts):
        return {"error": "That thread is not one the dashboard reads."}
    token = token_for(channel)
    if not token:
        return {"error": "needs_user_token"}
    msgs, cursor = [], None
    for _ in range(5):
        params = {"channel": channel, "ts": ts, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = slack_get(token, "conversations.replies", **params)
        if not r.get("ok"):
            return {"error": r.get("error", "slack_error")}
        msgs += r.get("messages") or []
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    out = []
    for m in msgs:
        uid = m.get("user")
        bp = m.get("bot_profile") or {}
        if bp.get("name") and (not uid or bp.get("user_id") == uid):
            who = bp["name"]                      # the bot itself
        elif uid:
            who = (m.get("user_profile") or {}).get("display_name") or user_name(token, uid)  # a person (also "via" an app)
        else:
            who = m.get("username") or "?"
        text = m.get("text") or ""
        for uid2 in set(re.findall(r"<@(U[A-Z0-9]+)>", text)):
            text = text.replace(f"<@{uid2}>", f"<@{uid2}|{user_name(token, uid2)}>")
        photo = ((bp.get("icons") or {}).get("image_72") or (bp.get("icons") or {}).get("image_48") or "") if bp.get("name") and (not uid or bp.get("user_id") == uid) \
            else user_photo(uid)
        out.append({"ts": m["ts"], "who": who, "text": text, "mine": uid == b.OWNER, "photo": photo,
                    "bot": bool(bp.get("name")) and (not uid or bp.get("user_id") == uid),
                    "time": datetime.fromtimestamp(float(m["ts"])).strftime("%m-%d %H:%M"),
                    "files": [{"id": f["id"], "name": f.get("name") or f.get("title"), "type": f.get("filetype"),
                               "size": f.get("size")} for f in m.get("files") or [] if f.get("id")]})
    return {"channel": channel, "ts": ts, "messages": out,
            "link": f"{W.SLACK_URL}/archives/{channel}/p{ts.replace('.', '')}"}


def fetch_file(fid: str) -> tuple[int, bytes, str, dict]:
    if not FILE_RE.match(fid or ""):
        return 400, b"bad file id", "text/plain", {}
    for channel in (REVIEW_CHANNEL, b.CHANNEL):
        token = token_for(channel)
        if not token:
            continue
        info = slack_get(token, "files.info", file=fid)
        f = info.get("file") or {}
        shares = json.dumps(f.get("shares") or {}) + json.dumps(f.get("channels") or []) + json.dumps(f.get("groups") or [])
        if not info.get("ok") or (REVIEW_CHANNEL not in shares and b.CHANNEL not in shares):
            continue
        url = f.get("url_private_download") or f.get("url_private")
        if not url:   # deleted / tombstoned / external file
            return 404, b"this file is no longer available in Slack", "text/plain", {}
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
        mime = f.get("mimetype") or "application/octet-stream"
        # Slack stores the reviewers' <KEY>.html reports as text/plain, so they showed as source code; trust the
        # file type / name instead, as Slack's own preview does (owner 2026-11-20).
        if (f.get("filetype") == "html" or (f.get("name") or "").lower().endswith((".html", ".htm"))) and mime.startswith("text/"):
            mime = "text/html"
        extra = {}
        if mime.startswith("text/html"):
            # The report's own scripts may run (language toggles), but it gets a unique origin: it cannot
            # call this dashboard's API.
            extra["Content-Security-Policy"] = "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox"
            mime = "text/html; charset=utf-8"
        elif not (mime.startswith("image/") or mime == "application/pdf" or mime.startswith("text/")):
            extra["Content-Disposition"] = f'attachment; filename="{(f.get("name") or fid).replace(chr(34), "")}"'
        return 200, body, mime, extra
    return 404, b"file not found in the ticket channels", "text/plain", {}


def last_run(key: str) -> dict | None:
    """The ticket's newest migrate / fix run (not a chat)."""
    with b._state_lock:
        threads = b.load_state()["threads"]
    runs = [dict(v, thread_ts=k) for k, v in threads.items() if v.get("key") == key and v.get("mode") != "chat"]
    return max(runs, key=lambda r: tsnum(r["thread_ts"])) if runs else None


def run_report(run: dict | None) -> dict | None:
    """That run's final report (its last message from Claude), shown pinned on top of a chat."""
    if not run:
        return None
    ts = run["thread_ts"]
    def fetch():
        msgs = (read_local(ts) if is_local(ts) else read_thread(b.CHANNEL, ts)).get("messages") or []
        mine = [m for m in msgs if m.get("bot") and m.get("who") != "Claude Tickets"
                and not m["text"].startswith(("🛠", "▶", "⏹", "ℹ️", "🗜", "📎", "✓"))]
        # The run's final report, not a later chat answer (owner 2026-11-19): the last message that reads as one.
        final = next((m for m in reversed(mine) if re.search(
            r"\b(is (migrated|fixed|reviewed)|could not be (fixed|migrated)|no-go)\b", (m["text"].splitlines() or [""])[0])), None)
        last = final or (mine[-1] if mine else None)
        return last["text"] if last else None
    text = cached(f"report:{ts}", 60, fetch)
    if not text:
        return None
    return {"session": run.get("session_name") or run.get("key"), "status": run.get("status"),
            "when": run.get("finished_at") or run.get("last_run") or "", "text": text}


def chat_view(ts: str) -> dict:
    """An empty chat (new:KEY forks the last run, fresh:KEY starts blank) or an existing chat, plus the pinned
    report of the run it continues."""
    if ts.startswith(("new:", "fresh:")):
        kind, key = ts.split(":", 1)
        run = None if kind == "fresh" else last_run(key.upper())
        fork = {"session": run.get("session_name") or run["key"], "when": run.get("finished_at") or run.get("last_run") or ""} \
            if run and run.get("session_id") else None
        return {"messages": [], "link": None, "empty": kind, "fork": fork}
    return read_local(ts)


def start_chat(key: str, text: str, fresh: bool = False) -> tuple:
    """Chat → on a ticket with no chat yet (or New chat): the first message opens a local conversation and
    continues the ticket's last run session itself (owner decision: no fork), or starts a blank <KEY>-chat
    with New chat. Later messages go through the normal reply path."""
    text = (text or "").strip()
    if not text:
        return False, "Write a message."
    if key not in read_json(b.SEEN_FILE, {}):
        return False, f"{key} is not one of your tickets."
    busy = b.current.get("key")
    head = f"💬 *{key}* — chat · session `{key}-chat`"
    tid = f"local-{time.time():.6f}"
    b._runlog_update(tid, lambda r: r.update(head=head, key=key))
    b.runlog_append(tid, "system", head)
    b.runlog_append(tid, "you", text)
    run = None if fresh else last_run(key)
    fork = run if run and run.get("session_id") else None
    b.update_thread(tid, key=key, mode="chat", status="queued", started=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    forked_from_ts=fork["thread_ts"] if fork else None,
                    forked_from=fork["session_id"] if fork else None)
    b.jobs.put({"kind": "chat", "key": key, "thread_ts": tid, "text": text,
                "fork_from": fork["session_id"] if fork else None,
                "fork_name": (fork.get("session_name") or key) if fork else None})
    b.log.info("dashboard chat started for %s (%s)", key, tid)
    return True, (f"Queued — Claude starts after {busy}." if busy else "Sent to Claude."), {"ts": tid}


def post_reply(channel: str, ts: str, text: str) -> tuple[bool, str]:
    """Post the owner's own reply into a ticket thread, as the owner (user token). In the report channel the
    bridge then sees an owner reply and continues that ticket's Claude session, as with a Slack reply."""
    if channel == b.CHANNEL and ts.startswith(("new:", "fresh:")):
        kind, key = ts.split(":", 1)
        return start_chat(key.upper(), text, fresh=kind == "fresh")
    if channel == b.CHANNEL and is_local(ts):
        text = (text or "").strip()
        if not text:
            return False, "Write a reply."
        entry = b.get_thread(ts) or {}
        if not entry.get("key"):
            return False, "This conversation has no ticket run behind it."
        if b.current.get("thread_ts") == ts:
            # An add-on while the run works (owner request 2026-11-19): handed to the session after its next step.
            if not hasattr(b, "queue_addon") or not entry.get("session_id"):
                return False, "Claude is still working on this run; reply when it has finished its turn."
            b.queue_addon(entry["session_id"], ts, text)
            b.runlog_append(ts, "you", text)
            b.runlog_append(ts, "system", "📎 Add-on queued — it reaches the session after its next step "
                                          "(or right after the run, if it finishes first).")
            b.log.info("add-on queued for %s (%s)", entry["key"], ts)
            return True, "Add-on queued — Claude gets it after its next step."
        b.runlog_append(ts, "you", text)
        b.update_thread(ts, status="queued", status_before=entry.get("status"))
        b.jobs.put({"kind": "reply", "key": entry["key"], "thread_ts": ts, "text": text})
        b.log.info("dashboard reply queued for %s (local conversation)", entry["key"])
        return True, "Sent to Claude."
    if channel not in (REVIEW_CHANNEL, b.CHANNEL) or not TS_RE.match(ts or ""):
        return False, "That thread is not one the dashboard posts to."
    text = (text or "").strip().replace(f"@{W.REVIEWER}", f"<@{LEAD}>")
    if not text or len(text) > 4000:
        return False, "Write a reply (up to 4,000 characters)."
    f = b.SECRETS / "slack-user-token"
    if not f.exists():
        return False, "Replies need your Slack user token (with chat:write)."
    from urllib.parse import urlencode
    req = urllib.request.Request("https://slack.com/api/chat.postMessage",
                                 data=urlencode({"channel": channel, "thread_ts": ts, "text": text}).encode(),
                                 headers={"Authorization": f"Bearer {f.read_text(encoding='utf-8').strip()}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        r = json.loads(resp.read())
    if not r.get("ok"):
        return False, f"Slack said: {r.get('error')}" + (" — add the chat:write user scope and reinstall." if r.get("error") == "missing_scope" else "")
    b.log.info("dashboard reply posted in %s thread %s", "review" if channel == REVIEW_CHANNEL else "run", ts)
    return True, "Reply sent."


# ---------- after a /migrate: the review thread's parent message (owner decision 2026-11-16) ----------

LEAD = W.REVIEWER_USER   # the reviewer
AUTO_STATE = None       # set lazily: ~/.claude/jira-automation/state.json


def _auto_state_path() -> Path:
    return b.HOME / ".claude/jira-automation/state.json"


def remember_review_thread(key: str, ts: str) -> None:
    with b._state_lock:
        p = _auto_state_path()
        st = read_json(p, {"tickets": {}})
        st.setdefault("tickets", {}).setdefault(key, {})["review_thread"] = {"channel": REVIEW_CHANNEL, "ts": ts}
        p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def find_review_thread(key: str) -> str | None:
    """The ticket's parent message in the review channel, if one exists (recorded, or in recent history)."""
    rec = (read_json(_auto_state_path(), {}).get("tickets", {}).get(key) or {}).get("review_thread")
    if rec:
        return rec["ts"]
    token = token_for(REVIEW_CHANNEL)
    if not token:
        return None
    cursor = None
    for _ in range(8):  # up to ~1,600 recent messages
        params = {"channel": REVIEW_CHANNEL, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = slack_get(token, "conversations.history", **params)
        for m in r.get("messages") or []:
            if f"browse/{key}" in (m.get("text") or "") and m.get("thread_ts", m["ts"]) == m["ts"]:
                return m["ts"]
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not r.get("ok") or not cursor:
            break
    return None


def post_review_parent(client, key: str, run_thread_ts: str | None = None) -> str | None:
    existing = find_review_thread(key)
    if existing:
        remember_review_thread(key, existing)
        b.log.info("review thread for %s already exists (%s); nothing posted", key, existing)
        return existing
    f = b.SECRETS / "slack-user-token"
    if not f.exists():
        b.log.warning("no user token: could not post the review parent for %s", key)
        return None
    # The team's format (owner request 2026-11-20): the bare Jira link, nothing else, unfurled so the Jira Cloud app
    # shows the ticket card under it — as the other developers post it. A labelled <url|summary> link got no card.
    from urllib.parse import urlencode
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=urlencode({
        "channel": REVIEW_CHANNEL, "text": f"{W.JIRA_SITE}/browse/{key}",
        "unfurl_links": "true"}).encode(), headers={"Authorization": f"Bearer {f.read_text(encoding='utf-8').strip()}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        r = json.loads(resp.read())
    if not r.get("ok"):
        b.log.warning("review parent for %s not posted: %s", key, r.get("error"))
        return None
    ts = r["ts"]
    remember_review_thread(key, ts)
    b.log.info("posted the review parent for %s in %s (%s)", key, W.REVIEW_NAME, ts)
    if run_thread_ts:
        # The ready-for-review reply follows on its own (post_ready_reply), so this only says where the thread is.
        b.post(client, run_thread_ts, f"🧵 Posted {key} in {W.REVIEW_NAME}: "
                                      f"<{W.SLACK_URL}/archives/{REVIEW_CHANNEL}/p{ts.replace('.', '')}|{key}>.")
    return ts


def jira_call(method: str, path: str, body: dict | None = None) -> dict:
    auth = b.jira_auth_header()
    if not auth:
        raise RuntimeError("no Jira token")
    req = urllib.request.Request(f"{b.JIRA_SITE}/rest/api/3/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": auth, "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


def jira_move(key: str, to: str, only_from: str | None = None, dry_run: bool = False) -> str:
    """Move a ticket to status `to` (by the transition that leads there). Returns what happened, in words."""
    now = jira_call("GET", f"issue/{key}?fields=status")["fields"]["status"]["name"]
    if now.lower() == to.lower():
        return f"already {now}"
    if only_from and now.lower() != only_from.lower():
        return f"left as {now} (not {only_from})"
    trans = jira_call("GET", f"issue/{key}/transitions").get("transitions") or []
    t = next((t for t in trans if (t.get("to") or {}).get("name", "").lower() == to.lower()), None)
    if not t:
        return f"no transition from {now} to {to}"
    if dry_run:
        return f"would move {now} → {to} (transition {t['id']} {t.get('name')})"
    jira_call("POST", f"issue/{key}/transitions", {"transition": {"id": t["id"]}})
    return f"moved {now} → {to}"


# The Next step a migration's plan report writes; the watch replaces it once the ticket is sent for review.
MIGRATING_NEXT = "Migrating (Claude Tickets)"
PR_URL_RE = re.compile(r"https://github\.com/" + re.escape(W.GH_REPO) + r"/pull/\d+")


def _pr_url(rec: dict, name: str) -> str | None:
    """A PR's URL from a state.json ticket record. Runs write it several ways: {"pr": {"url": …}}, {"pr": {"number": …}},
    or {"pr": 2518, "pr_url": …} (NJ-102794, 2026-11-20: the bare number crashed the ready-for-review reply).
    The runbook's empty shape {"number": 0, "url": ""} means "no PR", never /pull/0."""
    v = rec.get(name)
    if isinstance(v, dict):
        if v.get("url"):
            return v["url"]
        v = v.get("number")
    if rec.get(f"{name}_url"):
        return rec[f"{name}_url"]
    if isinstance(v, str) and v.strip().lstrip("#").isdigit():
        v = int(v.strip().lstrip("#"))
    return f"https://github.com/{W.GH_REPO}/pull/{v}" if type(v) is int and v > 0 else None


def ready_reply_text(key: str, report: str = "") -> str | None:
    """The owner's "ready for review" reply (owner wording, 2026-11-17). None without a main PR."""
    rec = read_json(_auto_state_path(), {}).get("tickets", {}).get(key) or {}
    main, stg = _pr_url(rec, "pr"), _pr_url(rec, "stg_pr")
    for line in (report or "").splitlines():   # the run's report names them too: "Main PR: …", "Staging PR: …"
        m = PR_URL_RE.search(line)
        if m and not main and re.match(r"\s*main\b", line, re.I):
            main = m.group(0)
        if m and not stg and re.match(r"\s*(staging|stg)\b", line, re.I):
            stg = m.group(0)
    if not main:
        return None
    # "Hello, @<reviewer> -san" — mentions the reviewer (owner request 2026-11-17).
    return "\n".join([f"Hello, <@{LEAD}> -san", "The ticket is done migrating, and it's ready for review :man-bowing:",
                      f"main: {main}"] + ([f"stg: {stg}"] if stg else []))


def post_ready_reply(client, key: str, review_ts: str, report: str = "", run_thread_ts: str | None = None) -> str | None:
    """After a migration: reply in the ticket's review thread, as the owner, that it is ready for review with the main and
    staging PRs (owner request 2026-11-17; replaces the manual reply after a migration). Once per ticket."""
    rec = read_json(_auto_state_path(), {}).get("tickets", {}).get(key) or {}
    if rec.get("ready_reply_ts"):
        return rec["ready_reply_ts"]
    text = ready_reply_text(key, report)
    if not text:
        b.log.warning("ready-for-review reply for %s not posted: no main PR found", key)
        return None
    f = b.SECRETS / "slack-user-token"
    if not f.exists():
        b.log.warning("no user token: could not post the ready-for-review reply for %s", key)
        return None
    from urllib.parse import urlencode
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=urlencode({
        "channel": REVIEW_CHANNEL, "thread_ts": review_ts, "text": text, "unfurl_links": "false"}).encode(),
        headers={"Authorization": f"Bearer {f.read_text(encoding='utf-8').strip()}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        r = json.loads(resp.read())
    if not r.get("ok"):
        b.log.warning("ready-for-review reply for %s not posted: %s", key, r.get("error"))
        return None
    with b._state_lock:
        p = _auto_state_path()
        st = read_json(p, {"tickets": {}})
        st.setdefault("tickets", {}).setdefault(key, {})["ready_reply_ts"] = r["ts"]
        p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache.clear()
    b.log.info("posted the ready-for-review reply for %s in its review thread (%s)", key, r["ts"])
    if run_thread_ts:
        b.post(client, run_thread_ts, f"🙇 Replied \"ready for review\" in the review thread: "
                                      f"<{W.SLACK_URL}/archives/{REVIEW_CHANNEL}/p{r['ts'].replace('.', '')}?thread_ts={review_ts}&cid={REVIEW_CHANNEL}|{key}>")
    return r["ts"]


def on_job_finished(client=None, key: str = "", kind: str = "", mode: str = "", text: str = "", thread_ts: str = "", **_):
    first = (text or "").splitlines()[0] if text else ""
    # A migrate thread's later turns (Resume →, auto-resume, a reply) reach here as mode "reply" — the thread has no mode
    # of its own — so they count too (NJ-101597 finished through Resume → and its parent was skipped, 2026-11-17).
    if mode in ("start", "migrate", "reply", None, "") and kind in ("start", "reply") and " is migrated" in first and KEY_RE.match(key):
        # A failure here says so in the run's chat (NJ-102794, 2026-11-20: the reply crashed and only launchd.err.log knew).
        try:
            review_ts = post_review_parent(client, key, thread_ts)
            if not review_ts:
                b.post(client, thread_ts, "⚠️ Could not post the review thread's parent (bridge.log says why). "
                                          "Post the ticket's Jira link in the review channel yourself.")
            elif not post_ready_reply(client, key, review_ts, text, thread_ts):
                b.post(client, thread_ts, "⚠️ Could not post the \"ready for review\" reply (no main PR found, or Slack refused; "
                                          "bridge.log says why). Reply in the review thread from Claude Tickets.")
        except Exception as exc:
            b.log.exception("ready-for-review step failed for %s", key)
            b.post(client, thread_ts, f"⚠️ The ready-for-review step failed ({type(exc).__name__}). "
                                      "Reply in the review thread from Claude Tickets.")
    # A finished /fix round updates the ticket table's Waiting on + Next step (owner request 2026-11-17).
    # ("fix them now" answered in a Review → chat runs a fix round in that review conversation, mode "review")
    if mode in ("fix", "review") and KEY_RE.match(key or ""):
        nxt = fix_next_step(text)
        if nxt:
            try:
                what = table_update(key, WAITING["you"], nxt)
            except Exception as exc:
                what = f"failed ({type(exc).__name__})"
            b.log.info("fix of %s finished → table %s", key, what)
    # Review → (owner decision 2026-11-16): Jira stays In Review; when the review has findings, it goes back to
    # Dev In Progress so the board shows there is work again.
    what = ""
    if kind == "review" and KEY_RE.match(key) and " is reviewed" in first and not re.search(r"is reviewed:\s*clean", first, re.I):
        try:
            what = jira_move(key, "Dev In Progress", only_from="In Review")
        except Exception as exc:
            b.log.exception("jira move after review failed for %s", key)
            what = f"could not be changed ({type(exc).__name__}) — move it by hand"
        b.log.info("review of %s had findings: Jira %s", key, what)
        b.post(client, thread_ts, f"Jira: {what} — the review found something to fix.")
    # A finished review updates the ticket table too (owner request 2026-11-17).
    if kind == "review" and KEY_RE.match(key or "") and " is reviewed" in first:
        day = time.strftime("%m-%d")
        if re.search(r"is reviewed:\s*clean", first, re.I):
            waiting, nxt = W.REVIEWER_OPTION, f"Reviewed {day}: clean — still In Review, waiting for the reviewer"
        else:
            found = first.split(" is reviewed:", 1)[-1].strip() or "findings"
            jira = " · Jira back to Dev In Progress" if what.startswith("moved") or what.startswith("already Dev") else ""
            waiting, nxt = WAITING["you"], f"Reviewed {day}: {found}{jira} — answer \"fix them now\" or \"leave it\" in the chat"
        try:
            res = table_update(key, waiting, nxt)
        except Exception as exc:
            res = f"failed ({type(exc).__name__})"
        b.log.info("review of %s finished → table %s", key, res)


def reply_template(key: str) -> dict:
    """The owner's 'ready for review' / 'please check again' reply, with the current main + staging PR links."""
    try:
        raw = subprocess.run(["gh", "pr", "list", "--repo", W.GH_REPO, "--state", "all", "--limit", "30",
                              "--search", f"{key} in:title", "--json", "number,url,baseRefName,createdAt,state"],
                             capture_output=True, text=True, timeout=40).stdout
        prs = json.loads(raw or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        prs = []
    main = sorted([p for p in prs if p["baseRefName"] == "main" and p["state"] != "CLOSED"], key=lambda p: p["number"])
    stg = sorted([p for p in prs if p["baseRefName"] != "main" and p["state"] != "CLOSED"], key=lambda p: p["number"])
    hour = datetime.now().hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    with b._state_lock:
        recs = [v for v in b.load_state()["threads"].values() if v.get("key") == key]
    fixed = any(v.get("mode") == "fix" for v in recs)
    body = "I fixed the feedback, please check it again :man-bowing:" if fixed else "This ticket is ready for review :man-bowing:"
    lines = [f"{greet}, @{W.REVIEWER} -san", body]
    if main:
        lines.append(f"main: {main[-1]['url']}")
    if stg:
        lines.append(f"stg: {stg[-1]['url']}")
    return {"text": "\n".join(lines)}


# ---------- the Claude Report bot's Slack profile picture (chat avatar) ----------

BOT_USER = W.BOT_USER   # Claude Report
AVATAR_FILE = None


def bot_avatar() -> bytes:
    """The bot's current Slack avatar, cached for a day; the app icon if Slack can't be reached."""
    f = b.BASE / "bot-avatar.png"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return f.read_bytes()
    try:
        ut = b.SECRETS / "slack-user-token"
        token = ut.read_text(encoding="utf-8").strip() if ut.exists() else b.read_secret("slack-bot-token")
        p = (slack_get(token, "users.info", user=BOT_USER).get("user") or {}).get("profile") or {}
        url = p.get("image_192") or p.get("image_512") or p.get("image_72")
        if url and url.startswith("https://avatars.slack-edge.com/"):
            with urllib.request.urlopen(url, timeout=20) as resp:
                f.write_bytes(resp.read())
            return f.read_bytes()
    except Exception:
        b.log.exception("could not fetch the bot avatar")
    return f.read_bytes() if f.exists() else (b.HOME / ".claude/jira-automation/claude-report-icon.png").read_bytes()


# ---------- the ticket table (Slack List "My Jira tickets"), read only ----------

LIST_ID = W.LIST_ID
COLS = W.LIST_COLUMNS
LABELS = {"open": "OPEN", "dev_in_progress": "Dev In Progress", "in_review": "In Review", "blocked": "Blocked",
          "for_release": "For Release", "frontend": "Frontend", "middleware": "Middleware", "api": "API",
          "legacy": "Legacy", "next_js": "Next.js", "nextjs": "Next.js", "no_pr": "No PR", "mergeable": "Mergeable",
          "conflicts": "Conflicts", "checking": "Checking", "you": "You", W.REVIEWER_OPTION: W.REVIEWER,
          "new_api_blocked": "New API (blocked)", "not_started": "Not started", "skip": "Skip"}


def _rich(field: dict) -> tuple[str, str]:
    """(plain text, first link url) of a rich-text cell."""
    raw = field.get("rich_text")
    if raw is None and isinstance(field.get("value"), str) and field["value"].startswith("["):
        try:
            raw = json.loads(field["value"])
        except json.JSONDecodeError:
            raw = None
    if not raw:
        return (field.get("text") or (field.get("value") if isinstance(field.get("value"), str) else "") or "").strip(), ""
    parts, url = [], ""
    def walk(node):
        nonlocal url
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            if node.get("type") in ("text", "link"):
                parts.append(node.get("text") or node.get("url") or "")
                if node.get("type") == "link" and not url:
                    url = node.get("url") or ""
            walk(node.get("elements") or [])
    walk(raw)
    return "".join(parts).strip(), url


def _select(field: dict) -> str:
    v = (field.get("select") or [field.get("value")] or [""])[0] or ""
    if v.startswith("new_api"):
        return "New API (blocked)"
    return LABELS.get(v, v.replace("_", " ").title() if v else "")


def fetch_table() -> dict:
    try:
        res = client.api_call("slackLists.items.list", json={"list_id": LIST_ID, "limit": 200})
    except Exception as exc:
        return {"error": type(exc).__name__, "rows": []}
    rows = []
    for item in res.get("items") or []:
        f = {x.get("key"): x for x in item.get("fields") or []}
        ticket, ticket_url = _rich(f.get(COLS["ticket"]) or {})
        pr, pr_url = _rich(f.get(COLS["pr"]) or {})
        thread_text, thread_url = _rich(f.get(COLS["thread"]) or {})
        m = re.search(r"archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})", thread_url or "")
        dl = f.get(COLS["deadline"]) or {}
        rows.append({
            "key": (re.search(r"NJ-\d+", ticket) or re.search(r"NJ-\d+", ticket_url or "") or [None])[0] or ticket,
            "ticket_url": ticket_url, "summary": _rich(f.get(COLS["summary"]) or {})[0],
            "status": _select(f.get(COLS["status"]) or {}), "kind": _select(f.get(COLS["kind"]) or {}),
            "pr": pr, "pr_url": pr_url, "pr_state": _select(f.get(COLS["pr_state"]) or {}),
            "deadline": ((dl.get("date") or [None])[0] if isinstance(dl.get("date"), list) else dl.get("value")) or "",
            "waiting": _select(f.get(COLS["waiting"]) or {}), "next": _rich(f.get(COLS["next"]) or {})[0],
            "thread_text": thread_text, "thread_url": thread_url,
            "thread": {"channel": m.group(1), "ts": f"{m.group(2)}.{m.group(3)}"} if m else None,
        })
    return {"rows": rows, "fetched": time.time()}


# ---------- people for @mentions in the reply box ----------

def fetch_people() -> dict:
    """Workspace members (users.list via the owner's token), the review channel's recent voices first."""
    ut = b.SECRETS / "slack-user-token"
    if not ut.exists():
        return {"people": [], "error": "needs_user_token"}
    token = ut.read_text(encoding="utf-8").strip()
    people, cursor = {}, None
    for _ in range(10):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = slack_get(token, "users.list", **params)
        if not r.get("ok"):
            return {"people": [], "error": r.get("error", "slack_error")}
        for u in r.get("members") or []:
            if u.get("deleted") or u.get("is_bot") or u.get("id") == "USLACKBOT":
                continue
            pr = u.get("profile") or {}
            people[u["id"]] = {"id": u["id"], "name": pr.get("display_name") or pr.get("real_name") or u.get("name"),
                               "real": pr.get("real_name") or "", "avatar": pr.get("image_32") or pr.get("image_24") or "",
                               "rank": 0}
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    # who speaks (or is mentioned) in the review channel lately ranks first
    try:
        h = slack_get(token, "conversations.history", channel=REVIEW_CHANNEL, limit=200)
        for m in h.get("messages") or []:
            for uid in [m.get("user")] + re.findall(r"<@(U[A-Z0-9]+)>", m.get("text") or ""):
                if uid in people:
                    people[uid]["rank"] += 1
    except Exception:
        pass
    me = b.OWNER
    ordered = sorted((p for p in people.values() if p["id"] != me), key=lambda p: (-p["rank"], (p["name"] or "").lower()))
    return {"people": ordered}


# ---------- live reload: edits to this file apply without restarting the bridge ----------

_MTIME = Path(__file__).stat().st_mtime
_reload_lock = threading.Lock()


def _maybe_reload() -> None:
    mod = sys.modules.get(__name__)
    try:
        current = Path(__file__).stat().st_mtime
    except OSError:
        return
    if mod is None or current == _MTIME:
        return
    with _reload_lock:
        if Path(__file__).stat().st_mtime == mod._MTIME:
            return
        try:
            compile(Path(__file__).read_text(encoding="utf-8"), __file__, "exec")
        except SyntaxError:
            b.log.exception("dashboard.py has a syntax error; keeping the running version")
            mod._MTIME = current
            return
        saved_b, saved_client = mod.b, mod.client
        importlib.reload(mod)
        mod.b, mod.client = saved_b, saved_client
        saved_b.log.info("dashboard reloaded")


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep bridge.log clean
        pass

    def _ok_host(self) -> bool:
        return self.headers.get("Host", "") in HOSTS

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def do_GET(self):
        _maybe_reload()
        return handle_get(self)

    def do_POST(self):
        _maybe_reload()
        return handle_post(self)


def handle_get(self):
        if not self._ok_host():
            return self._send(403, b"forbidden", "text/plain")
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        try:
            if path == "/":
                return self._send(200, (HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
            if path in ("/dashboard.css", "/dashboard.js"):   # the page's stylesheet and script, read per request
                kind = "text/css" if path.endswith(".css") else "text/javascript"
                return self._send(200, (HERE / path[1:]).read_bytes(), f"{kind}; charset=utf-8")
            if path == "/api/people":
                return self._json(200, cached("people", 6 * 3600, fetch_people))
            if path == "/api/today":
                day = params.get("date") or time.strftime("%Y-%m-%d")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    return self._json(400, {"error": "bad date"})
                return self._json(200, cached(f"today:{day}", 60, lambda: today_report(day)))
            if path == "/api/table":
                return self._json(200, cached("table", 30, fetch_table))
            if path == "/appicon.png":  # the desktop app's own icon
                f = b.HOME / ".claude/jira-automation/tickets-app/AppIcon-1024.png"
                return self._send(200, f.read_bytes(), "image/png")
            if path == "/avatar.png":
                return self._send(200, bot_avatar(), "image/png")
            if path == "/emoji.json":   # Slack's emoji names (iamcal emoji-data 15.1.2), for :shortcode: suggestions
                return self._send(200, (HERE / "emoji.json").read_bytes(), "application/json; charset=utf-8")
            if path == "/icon.png":
                return self._send(200, (b.HOME / ".claude/jira-automation/claude-report-icon.png").read_bytes(), "image/png")
            if path == "/api/state":
                return self._json(200, state())
            if path == "/api/template":
                key = params.get("key", "")
                return self._json(200, reply_template(key) if KEY_RE.match(key) else {"text": ""})
            if path == "/api/thread":
                return self._json(200, read_thread(params.get("channel", ""), params.get("ts", "")))
            if path == "/api/file":
                code, body, ctype, extra = fetch_file(params.get("id", ""))
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/log":
                f = transcript_path(params.get("session", ""))
                if not f:
                    return self._json(404, {"error": "no such session"})
                lines, off = log_lines(f, int(params.get("offset", "0") or 0))
                return self._json(200, {"lines": lines, "offset": off})
        except Exception as exc:
            b.log.exception("dashboard GET %s failed", path)
            return self._json(500, {"error": type(exc).__name__})
        return self._send(404, b"not found", "text/plain")

def handle_post(self):
        if not self._ok_host() or self.headers.get("X-Dashboard") != "1":
            return self._send(403, b"forbidden", "text/plain")
        if self.path == "/api/reply":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
                res = post_reply(body.get("channel", ""), body.get("ts", ""), body.get("text", ""))
                ok, msg = res[0], res[1]
                return self._json(200 if ok else 409, {"ok": ok, "message": msg, **(res[2] if len(res) > 2 else {})})
            except Exception as exc:
                b.log.exception("dashboard reply failed")
                return self._json(500, {"ok": False, "message": type(exc).__name__})
        if self.path == "/api/note":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
                ok, msg = post_note((body.get("key") or "").upper(), body.get("text") or "")
                return self._json(200 if ok else 409, {"ok": ok, "message": msg})
            except Exception as exc:
                b.log.exception("dashboard note failed")
                return self._json(500, {"ok": False, "message": type(exc).__name__})
        if self.path == "/api/settings":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
                ok, msg = set_run_settings(body if isinstance(body, dict) else {})
                return self._json(200 if ok else 409, {"ok": ok, "message": msg, "run_settings": run_settings_view()})
            except Exception as exc:
                b.log.exception("dashboard settings failed")
                return self._json(500, {"ok": False, "message": type(exc).__name__})
        if self.path != "/api/action":
            return self._send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)) or b"{}")
            ov = {"model": body.get("model"), "effort": body.get("effort")} if body.get("model") else None
            res = act(body.get("action", ""), (body.get("key") or "").upper(), ov)
            ok, msg = res[0], res[1]
            b.log.info("dashboard action %s %s -> %s", body.get("action"), body.get("key"), msg)
            return self._json(200 if ok else 409, {"ok": ok, "message": msg, **(res[2] if len(res) > 2 else {})})
        except Exception as exc:
            b.log.exception("dashboard action failed")
            return self._json(500, {"ok": False, "message": type(exc).__name__})


# ---------- "what we did today" (owner request 2026-11-16: Today page + Slack post at 17:40 on weekdays) ----------

DAILY_STAMP_NAME = "daily-report-last.txt"
PR_KEY_RE = re.compile(r"NJ[-_ ]?(\d{5,6})", re.I)


def _local(iso: str) -> datetime | None:
    """An ISO time from Jira / GitHub as local time."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        try:
            return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone()
        except ValueError:
            return None


def _jira_events(day: str, add, errors: list) -> dict:
    """Status changes and new assignments on the day, from each touched ticket's changelog."""
    from urllib.parse import urlencode
    summaries = {}
    try:
        me = cached("jira-me", 86400, lambda: jira_call("GET", "myself")).get("accountId")
        q = urlencode({"jql": f'assignee = currentUser() AND updated >= "{day}" ORDER BY key',
                       "fields": "summary,status", "maxResults": 50})
        issues = jira_call("GET", f"search/jql?{q}").get("issues") or []
    except Exception as exc:
        errors.append(f"Jira: {type(exc).__name__}")
        return summaries
    for iss in issues:
        key = iss["key"]
        summaries[key] = (iss.get("fields") or {}).get("summary", "")
        try:
            total = jira_call("GET", f"issue/{key}/changelog?maxResults=1").get("total", 0)
            hist = jira_call("GET", f"issue/{key}/changelog?maxResults=100&startAt={max(0, total - 100)}").get("values") or []
        except Exception as exc:
            errors.append(f"Jira {key}: {type(exc).__name__}")
            continue
        for h in hist:
            when = _local(h.get("created", ""))
            if not when or when.strftime("%Y-%m-%d") != day:
                continue
            who = (h.get("author") or {})
            by = "" if who.get("accountId") == me else f" (by {who.get('displayName', '?')})"
            for it in h.get("items") or []:
                if it.get("field") == "status":
                    add(key, when.strftime("%H:%M"), "🔁", f"Jira {it.get('fromString')} → {it.get('toString')}{by}", "jira")
                elif it.get("field") == "assignee" and it.get("to") == me:
                    add(key, when.strftime("%H:%M"), "🆕", f"Assigned to you{by}", "assigned")
    return summaries


def _pr_events(day: str, add, errors: list) -> None:
    try:
        raw = subprocess.run(["gh", "pr", "list", "--repo", W.GH_REPO, "--author", "@me", "--state", "all",
                              "--limit", "60", "--search", f"updated:>={day}",
                              "--json", "number,title,url,createdAt,mergedAt,baseRefName,headRefName"],
                             capture_output=True, text=True, timeout=60).stdout
        prs = json.loads(raw or "[]")
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        errors.append(f"GitHub: {type(exc).__name__}")
        return
    for pr in prs:
        m = PR_KEY_RE.search(pr.get("title", "")) or PR_KEY_RE.search(pr.get("headRefName", ""))
        if not m:
            continue
        key = f"NJ-{m.group(1)}"
        kind = "main" if pr.get("baseRefName") == "main" else pr.get("baseRefName", "")
        link = f"<{pr['url']}|#{pr['number']}>"
        for field, icon, verb in (("createdAt", "🔀", "opened"), ("mergedAt", "🟣", "merged")):
            when = _local(pr.get(field) or "")
            if when and when.strftime("%Y-%m-%d") == day:
                add(key, when.strftime("%H:%M"), icon, f"PR {link} {verb} ({kind})", "pr")


def today_report(day: str | None = None) -> dict:
    day = day or time.strftime("%Y-%m-%d")
    events: dict[str, list] = {}
    errors: list[str] = []

    def add(key, hhmm, icon, text, kind, mode=None):
        events.setdefault(key, []).append({"time": hhmm or "", "icon": icon, "text": text, "kind": kind, "mode": mode})

    # Claude runs (migrate / fix / review) and chats that started, ran or finished on the day
    with b._state_lock:
        threads = dict(b.load_state()["threads"])
    for ts, v in threads.items():
        key = v.get("key")
        stamps = {(v.get("started") or "")[:10], (v.get("last_run") or "")[:10], (v.get("finished_at") or "")[:10]}
        if not key or day not in stamps:
            continue
        mode, status = v.get("mode") or "migrate", v.get("status")
        at = (v.get("finished_at") if (v.get("finished_at") or "").startswith(day) else v.get("last_run")) or v.get("started") or ""
        hhmm = at[11:16]
        if mode == "chat":
            add(key, hhmm, "💬", "Chat with Claude", "chat")
            continue
        label = {"fix": "Fix", "review": "Review", "reply": "Run"}.get(mode, "Migrate")
        if status in ("finished", "ended_turn") and (v.get("finished_at") or "").startswith(day):
            rep = run_report(dict(v, thread_ts=ts)) if status == "finished" else None
            first = ((rep or {}).get("text") or "").splitlines()[0][:140] if rep else ""
            add(key, hhmm, "✅" if status == "finished" else "ℹ️",
                f"{label} {'finished' if status == 'finished' else 'stopped to ask you something'}" + (f": {first}" if first else ""), "run", mode)
        elif status in ("running", "queued"):
            add(key, hhmm, "🛠", f"{label} {status}", "run", mode)
        elif status in ("stopped", "failed"):
            add(key, hhmm, "⏹" if status == "stopped" else "⚠️", f"{label} {status}", "run", mode)
        else:
            add(key, hhmm, "🛠", f"{label} started", "run", mode)

    summaries = _jira_events(day, add, errors)
    _pr_events(day, add, errors)

    # Review threads: reviewer feedback received, and the owner's reply as the thread's newest message
    st = state()
    by_key = {t["key"]: t for t in st["tickets"]}
    for t in st["tickets"]:
        fts = (t.get("feedback") or {}).get("ts")
        if fts and datetime.fromtimestamp(float(fts)).strftime("%Y-%m-%d") == day:
            add(t["key"], datetime.fromtimestamp(float(fts)).strftime("%H:%M"), "💬", "Reviewer feedback received", "feedback")
        rt = t.get("review_turn")
        if rt and rt.get("owner_last") and datetime.fromtimestamp(float(rt["ts"])).strftime("%Y-%m-%d") == day:
            add(t["key"], datetime.fromtimestamp(float(rt["ts"])).strftime("%H:%M"), "↩️", "You replied in the review thread", "reply")

    rows = []
    for key, evs in events.items():
        evs.sort(key=lambda e: e["time"])
        t = by_key.get(key) or {}
        label = report_label(evs, t, threads, key)
        rows.append({"key": key, "summary": t.get("summary") or summaries.get(key, ""), "status": t.get("status", ""),
                     "events": evs, "last": evs[-1]["time"] if evs else "", "label": label})
    rows.sort(key=lambda r: r["last"], reverse=True)
    count = lambda kind: sum(1 for r in rows for e in r["events"] if e["kind"] == kind)
    stamp = b.BASE / DAILY_STAMP_NAME
    posted = stamp.read_text().strip() if stamp.exists() else ""
    rep_lines = [f"{r['key']}: {r['label']}" for r in sorted(rows, key=lambda r: r["key"]) if r.get("label")]
    return {"day": day, "label": datetime.strptime(day, "%Y-%m-%d").strftime("%a %m-%d"), "tickets": rows, "lines": rep_lines,
            "counts": {"tickets": len(rows), "runs": count("run"), "jira": count("jira"), "prs": count("pr"),
                       "feedback": count("feedback")},
            "posted": posted == day, "errors": errors}


def report_label(evs: list, t: dict, threads: dict, key: str) -> str | None:
    """The owner's wording in the daily report channel ("NJ-102235: Fix FB", "NJ-101032: Migration", "… in review",
    "… in progress"). None = nothing worth reporting (e.g. only a chat)."""
    modes = {e.get("mode") for e in evs if e["kind"] == "run"}
    if "migrate" in modes or "start" in modes:
        return "Migration"
    if "fix" in modes:
        return "Fix FB"
    if any(e["kind"] == "reply" for e in evs):
        # Replied in the review thread today: name the work that reply was about (the last migrate / fix run).
        runs = sorted((v for v in threads.values() if v.get("key") == key and v.get("mode") in ("fix", "migrate", None)),
                      key=lambda v: v.get("finished_at") or v.get("last_run") or "")
        return "Fix FB" if runs and runs[-1].get("mode") == "fix" else "Migration" if runs else "in review"
    # Being assigned a ticket or receiving feedback is not work done that day; only the owner's own runs, PRs and Jira
    # moves count. Someone else's move ("… (by <someone>)") or the lead merging the main PR is not the owner's
    # work (owner 2026-11-19: NJ-101033 showed "in progress" for the lead's For Release move).
    def own(e: dict) -> bool:
        if e["kind"] == "jira":
            return "(by " not in e["text"]
        if e["kind"] == "pr":
            return not re.search(r"merged \(main\)", e["text"])
        return e["kind"] == "run"
    if not any(own(e) for e in evs):
        return None
    status = (t.get("status") or "").lower()
    return "in review" if "review" in status else "in progress" if "progress" in status else (t.get("status") or "in progress")


def report_lines(rep: dict) -> list[str]:
    return [f"{r['key']}: {r['label']}" for r in sorted(rep["tickets"], key=lambda r: r["key"]) if r.get("label")]


def daily_text(rep: dict) -> str:
    """For the report channel at 17:40: the lines to paste into today's thread in the daily report channel."""
    lines = report_lines(rep)
    head = f"📋 *{rep['day']} REPORT* — ready to paste in <#{W.DAILY_CHANNEL}>'s thread"
    if not lines:
        return head + "\nNothing moved on your tickets today."
    tail = [f"_Some sources could not be read: {', '.join(rep['errors'])}_"] if rep["errors"] else []
    return "\n".join([head, "```", *lines, "```", *tail, "Details: Claude Tickets → Report"])


def daily_post(slack_client, day: str | None = None) -> bool:
    """Posts the day's report to the report channel (called by the bridge at 17:40 on weekdays)."""
    rep = today_report(day)
    text = daily_text(rep)
    if len(text) > 39000:
        text = text[:38900] + "\n…(see Claude Tickets → Report)"
    slack_client.chat_postMessage(channel=b.CHANNEL, text=text, unfurl_links=False, unfurl_media=False)
    b.log.info("daily report posted for %s (%s tickets)", rep["day"], rep["counts"]["tickets"])
    return True


def start(bridge_module, slack_client) -> None:
    global b, client
    b, client = bridge_module, slack_client
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    b.log.info("dashboard on http://127.0.0.1:%s", PORT)
