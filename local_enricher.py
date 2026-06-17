#!/usr/bin/env python3
"""
local_enricher.py - runs at 9:05 AM (Sun-Thu) via Task Scheduler.
Fetches IBKR positions + Google Calendar events, saves enriched_data.json,
then git-commits and pushes so GitHub Actions (9:15 AM) can read it.

Install deps: pip install requests pytz ib_insync google-auth-oauthlib google-auth-httplib2 google-api-python-client
"""
import json
import os
import subprocess
import warnings
from datetime import datetime
import pytz
import requests

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "enriched_data.json")
TZ_IL = pytz.timezone("Asia/Jerusalem")


# IBKR

def get_ibkr_client_portal():
    """IBKR Client Portal Web API (IB Gateway, https://localhost:5000)."""
    base = "https://localhost:5000/v1/api"
    r = requests.get(f"{base}/portfolio/accounts", verify=False, timeout=5)
    r.raise_for_status()
    accounts = r.json()
    if not accounts or not isinstance(accounts, list):
        return None
    acct = accounts[0]["id"]

    sum_r = requests.get(f"{base}/portfolio/{acct}/summary", verify=False, timeout=10)
    summary = sum_r.json() if sum_r.ok else {}

    pos_r = requests.get(f"{base}/portfolio/{acct}/positions/0", verify=False, timeout=10)
    positions_raw = pos_r.json() if pos_r.ok else []

    def amt(field):
        val = summary.get(field, {})
        return float(val.get("amount", val) if isinstance(val, dict) else val or 0)

    net_liq = amt("netliquidation")
    cash    = amt("totalcashvalue")

    positions = []
    for p in (positions_raw or []):
        if not p.get("position"):
            continue
        positions.append({
            "ticker":         p.get("contractDesc") or p.get("ticker", ""),
            "position":       p.get("position", 0),
            "market_value":   p.get("mktValue", 0),
            "market_price":   p.get("mktPrice", 0),
            "unrealized_pnl": p.get("unrealizedPnl", 0),
            "daily_pnl":      p.get("dailyPnl", 0),
        })

    return {
        "net_liq":        net_liq,
        "cash":           cash,
        "unrealized_pnl": round(sum(p["unrealized_pnl"] for p in positions), 2),
        "daily_pnl":      round(sum(p["daily_pnl"] for p in positions), 2),
        "positions":      sorted(positions, key=lambda x: x["unrealized_pnl"], reverse=True),
        "source":         "portal",
    }


def get_ibkr_tws():
    """Fallback: ib_insync via TWS port 7497."""
    from ib_insync import IB
    ib = IB()
    ib.connect("127.0.0.1", 7497, clientId=98, timeout=5, readonly=True)
    if not ib.isConnected():
        return None

    tags = {av.tag: av.value for av in ib.accountSummary() if av.currency in ("USD", "BASE", "")}
    net_liq = float(tags.get("NetLiquidation", 0))
    cash    = float(tags.get("TotalCashValue", 0))

    positions = []
    for item in ib.portfolio():
        sym = item.contract.symbol
        if not sym or item.position == 0:
            continue
        positions.append({
            "ticker":         sym,
            "position":       item.position,
            "market_value":   item.marketValue,
            "market_price":   item.marketPrice,
            "unrealized_pnl": item.unrealizedPNL or 0,
            "daily_pnl":      getattr(item, "dailyPNL", 0) or 0,
        })

    ib.disconnect()
    return {
        "net_liq":        net_liq,
        "cash":           cash,
        "unrealized_pnl": round(sum(p["unrealized_pnl"] for p in positions), 2),
        "daily_pnl":      round(sum(p["daily_pnl"] for p in positions), 2),
        "positions":      sorted(positions, key=lambda x: x["unrealized_pnl"], reverse=True),
        "source":         "tws",
    }


def get_ibkr_data():
    for fn, label in [(get_ibkr_client_portal, "Client Portal"), (get_ibkr_tws, "TWS")]:
        try:
            data = fn()
            if data:
                print(f"   [IBKR] OK via {label}: net_liq=${data['net_liq']:,.2f}")
                return data
        except Exception as e:
            print(f"   [IBKR] {label} failed: {e}")
    return None


# GOOGLE CALENDAR

def get_calendar_events():
    token_path = os.path.join(SCRIPT_DIR, "token.json")
    if not os.path.exists(token_path):
        print("   [Calendar] token.json not found - run setup_calendar_auth.py once")
        return []

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    service = build("calendar", "v3", credentials=creds)
    now_il  = datetime.now(TZ_IL)
    time_min = now_il.replace(hour=0,  minute=0,  second=0,  microsecond=0).isoformat()
    time_max = now_il.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

    result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for ev in result.get("items", []):
        start   = ev.get("start", {})
        dt_str  = start.get("dateTime") or start.get("date", "")
        title   = ev.get("summary", "No title")
        if "T" in dt_str:
            t = datetime.fromisoformat(dt_str).strftime("%H:%M")
            events.append({"title": title, "time": t})
        else:
            events.append({"title": title, "time": "All day"})

    print(f"   [Calendar] {len(events)} event(s) today")
    return events


# GIT PUSH

def git_push():
    try:
        subprocess.run(["git", "add", "enriched_data.json"], cwd=SCRIPT_DIR, check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=SCRIPT_DIR, capture_output=True)
        if diff.returncode == 0:
            print("   [git] No changes to push")
            return
        ts = datetime.now(TZ_IL).strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "commit", "-m", f"daily enriched data {ts}"], cwd=SCRIPT_DIR, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=SCRIPT_DIR, check=True, capture_output=True)
        print("   [git] Pushed enriched_data.json OK")
    except subprocess.CalledProcessError as e:
        print(f"   [git] Push failed: {e.stderr.decode() if e.stderr else e}")


# MAIN

def main():
    now = datetime.now(TZ_IL)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] local_enricher starting...")

    print("1. IBKR portfolio...")
    ibkr = get_ibkr_data()

    print("2. Google Calendar...")
    try:
        calendar_events = get_calendar_events()
    except Exception as e:
        print(f"   [Calendar] Error: {e}")
        calendar_events = []

    print("3. Writing enriched_data.json...")
    data = {
        "timestamp":       now.isoformat(),
        "calendar_events": calendar_events,
        "ibkr":            ibkr,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("4. Git push...")
    git_push()

    print("Done! GitHub Actions will use this data in ~10 minutes.")


if __name__ == "__main__":
    main()
