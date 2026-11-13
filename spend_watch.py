#!/usr/bin/env python3
"""Daily spend watchdog for the OpenClaw fleet.

OpenClaw has no native per-agent spend cap, so this reads the real cost recorded
on every assistant turn in each agent's session transcript and compares today's
total against limits. When an agent or the fleet goes over, it messages Ily once
per day per breach.

It never silently changes agent behaviour — it reports. Enforcing a hard cap by
disabling agents would stop real work without warning.

Limits: /root/village/spend_limits.json
State:  /root/village/spend_state.json  (prevents duplicate alerts)
Log:    /root/village/spend_watch.log
"""
import json, glob, os, re, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

LIMITS_PATH = "/root/village/spend_limits.json"
STATE_PATH = "/root/village/spend_state.json"
LOG_PATH = "/root/village/spend_watch.log"
CHAT_ID = "1423821630"

DEFAULTS = {"per_agent_daily_usd": 0.50, "fleet_daily_usd": 1.50, "warn_at_fraction": 0.8}


def load_limits():
    try:
        d = json.load(open(LIMITS_PATH))
    except Exception:
        d = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in d.items() if k in DEFAULTS})
    return out


def deepseek_balance():
    """DeepSeek returns no per-turn cost, so watch its balance directly."""
    key = None
    for ln in open("/root/.hermes/.env", encoding="utf-8", errors="ignore"):
        m = re.match(r"\s*DEEPSEEK_API_KEY\s*=\s*(.+?)\s*$", ln)
        if m:
            key = m.group(1).strip().strip('"').strip("'")
    if not key:
        return None
    try:
        req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                     headers={"Authorization": f"Bearer {key}"})
        d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
        for b in d.get("balance_infos", []):
            if b.get("currency") == "USD":
                return float(b.get("total_balance", 0))
    except Exception:
        return None
    return None


def hermes_token():
    for ln in open("/root/.hermes/.env", encoding="utf-8", errors="ignore"):
        m = re.match(r"\s*TELEGRAM_BOT_TOKEN\s*=\s*(.+?)\s*$", ln)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def _local_date(ts):
    """Transcript timestamps are UTC; budgets are in local time. Convert."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return str(ts)[:10]


def today_spend():
    """Sum real per-turn cost from OpenClaw transcripts for today (LOCAL date)."""
    today = datetime.now().strftime("%Y-%m-%d")
    by_agent = defaultdict(float)
    turns = defaultdict(int)
    for f in glob.glob("/root/.openclaw/agents/*/sessions/*.jsonl"):
        if "trajectory" in f:
            continue
        agent = f.split("/agents/")[1].split("/")[0]
        try:
            fh = open(f, encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if _local_date(d.get("timestamp")) != today:
                    continue
                u = (d.get("message") or {}).get("usage") or {}
                c = (u.get("cost") or {}).get("total") or 0
                if c:
                    by_agent[agent] += c
                    turns[agent] += 1
    return today, dict(by_agent), dict(turns)


def send(text):
    tok = hermes_token()
    if not tok:
        log("ERROR: no hermes telegram token, cannot send alert")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text,
                                       "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data)
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        return bool(r.get("ok"))
    except Exception as e:
        log(f"ERROR sending alert: {e}")
        return False


def log(msg):
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')}  {msg}\n")


def main():
    lim = load_limits()
    today, by_agent, turns = today_spend()
    fleet = sum(by_agent.values())

    try:
        state = json.load(open(STATE_PATH))
    except Exception:
        state = {}
    if state.get("date") != today:
        state = {"date": today, "alerted": {}}
    alerted = state.setdefault("alerted", {})

    # per agent
    for agent, spent in sorted(by_agent.items(), key=lambda x: -x[1]):
        cap = lim["per_agent_daily_usd"]
        key = f"agent:{agent}"
        if spent >= cap and key not in alerted:
            send(f"💸 <b>Budget watch — {agent}</b>\n\n"
                 f"Today: <b>${spent:.2f}</b> of ${cap:.2f} daily budget\n"
                 f"Turns: {turns.get(agent, 0)}\n\n"
                 f"Over budget. Check what it's working on before it goes further.")
            alerted[key] = round(spent, 4)
            log(f"ALERT agent {agent} ${spent:.2f} >= ${cap:.2f}")

    # fleet total
    fcap = lim["fleet_daily_usd"]
    if fleet >= fcap and "fleet" not in alerted:
        top = max(by_agent.items(), key=lambda x: x[1]) if by_agent else ("-", 0)
        send(f"💸 <b>Budget watch — whole fleet</b>\n\n"
             f"Today: <b>${fleet:.2f}</b> of ${fcap:.2f} daily budget\n"
             f"Biggest spender: {top[0]} ${top[1]:.2f}\n\n"
             f"Agents are still running. Nothing was stopped.")
        alerted["fleet"] = round(fleet, 4)
        log(f"ALERT fleet ${fleet:.2f} >= ${fcap:.2f}")

    # DeepSeek balance: agents fall back here, and it bills without per-turn cost
    bal = deepseek_balance()
    if bal is not None:
        floor = lim.get("deepseek_floor_usd", 2.00)
        if bal <= floor and "deepseek" not in alerted:
            send(f"💸 <b>DeepSeek balance low</b>\n\n"
                 f"Remaining: <b>${bal:.2f}</b>\n\n"
                 f"This is the fallback the fleet is running on. When it empties, "
                 f"agents stop answering again.")
            alerted["deepseek"] = round(bal, 2)
            log(f"ALERT deepseek balance ${bal:.2f} <= ${floor:.2f}")
        log(f"deepseek balance ${bal:.2f}")

    state["alerted"] = alerted
    json.dump(state, open(STATE_PATH, "w"), indent=2)
    log(f"checked {today}: fleet ${fleet:.4f} agents=" +
        ", ".join(f"{a}:${v:.3f}" for a, v in sorted(by_agent.items())))


if __name__ == "__main__":
    main()
