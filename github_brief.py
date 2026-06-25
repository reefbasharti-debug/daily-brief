"""
github_brief.py - Daily brief for GitHub Actions (cloud)
Reads enriched_data.json (pushed by local_enricher.py at 9:05 AM) for
Calendar + IBKR data. Falls back gracefully if file is missing/stale.

Requires: yfinance requests pytz
Env vars: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import os
import requests
import pytz
import yfinance as yf
from datetime import datetime, timedelta

TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TZ_IL    = pytz.timezone("Asia/Jerusalem")
now      = datetime.now(TZ_IL)
DAYS_HE  = {0:"Monday", 1:"Tuesday", 2:"Wednesday", 3:"Thursday", 4:"Friday", 5:"Saturday", 6:"Sunday"}
day_name = DAYS_HE[now.weekday()]


# HELPERS

def pct(val):
    if val is None: return "N/A"
    sign  = "+" if val >= 0 else ""
    emoji = "\u2705" if val >= 0 else "\U0001f534"
    return f"{emoji} {sign}{val:.2f}%"

def fmt_usd(val):
    sign  = "+" if val >= 0 else "-"
    emoji = "\u2705" if val >= 0 else "\U0001f534"
    return f"{emoji} {sign}${abs(val):,.2f}"


# MARKET DATA

def get_market():
    results = {}
    labels  = {"SPY": "S&P 500", "DIA": "Dow Jones", "VXX": "VIX (Fear)"}
    days_since_sun = {6:0, 0:1, 1:2, 2:3, 3:4, 4:5}.get(now.weekday(), 0)

    for sym, label in labels.items():
        try:
            tk   = yf.Ticker(sym)
            hist = tk.history(period="10d", interval="1d")
            if len(hist) < 2:
                results[sym] = {"label": label, "error": "no data"}
                continue
            price   = hist["Close"].iloc[-1]
            prev    = hist["Close"].iloc[-2]
            day_chg = (price - prev) / prev * 100

            week_chg = None
            if days_since_sun > 0 and len(hist) >= days_since_sun + 1:
                sun_open = hist["Open"].iloc[-days_since_sun]
                week_chg = (price - sun_open) / sun_open * 100

            results[sym] = {
                "label": label,
                "price": round(price, 2),
                "day":   round(day_chg, 2),
                "week":  round(week_chg, 2) if week_chg is not None else None,
            }
        except Exception as e:
            results[sym] = {"label": label, "error": str(e)}
    return results


# ENRICHED DATA (Calendar + IBKR from local_enricher.py)

def load_enriched():
    """Load enriched_data.json if it was pushed on the same calendar day (Israel time)."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enriched_data.json")
        if not os.path.exists(path):
            print("[enriched] enriched_data.json not found")
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ts    = datetime.fromisoformat(data["timestamp"])
        ts_il = ts.astimezone(TZ_IL)
        if ts_il.date() != now.date():
            print(f"[enriched] File is from {ts_il.date()}, today is {now.date()}, skipping")
            return None
        print(f"[enriched] Loaded (timestamp: {ts_il.strftime('%H:%M')})")
        return data
    except Exception as e:
        print(f"[enriched] Error: {e}")
        return None




# ─── CLOUD CALENDAR (Google Service Account) ──────────────────────────────────

def get_calendar_cloud():
    """Fetch today's events via Google Service Account (no local machine needed)."""
    try:
        import json as _json
        sa_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not sa_str:
            print("[calendar_cloud] GOOGLE_SERVICE_ACCOUNT_JSON not set")
            return None
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        sa_info = _json.loads(sa_str)
        creds   = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        svc    = build("calendar", "v3", credentials=creds)
        cal_id = os.environ.get("GOOGLE_CALENDAR_ID", "reefbasharti@gmail.com")
        t_min  = now.replace(hour=0,  minute=0,  second=0,  microsecond=0).isoformat()
        t_max  = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        res    = svc.events().list(
            calendarId=cal_id, timeMin=t_min, timeMax=t_max,
            singleEvents=True, orderBy="startTime").execute()
        events = []
        for e in res.get("items", []):
            st = e.get("start", {})
            if "dateTime" in st:
                dt = datetime.fromisoformat(st["dateTime"])
                t  = dt.astimezone(TZ_IL).strftime("%H:%M")
            else:
                t = "\u05db\u05dc \u05d4\u05d9\u05d5\u05dd"
            events.append({"title": e.get("summary", "\u05d0\u05d9\u05e8\u05d5\u05e2"), "time": t})
        print(f"[calendar_cloud] {len(events)} events")
        return events
    except Exception as ex:
        print(f"[calendar_cloud] Error: {ex}")
        return None


# ─── CLOUD IBKR (Flex Query — end-of-day, no IB Gateway) ─────────────────────

def get_ibkr_flex():
    """Fetch end-of-day portfolio via IBKR Flex Query (no IB Gateway needed)."""
    try:
        import xml.etree.ElementTree as ET
        import time as _t
        ft  = os.environ.get("IBKR_FLEX_TOKEN", "")
        fqid = os.environ.get("IBKR_FLEX_QUERY_ID", "")
        if not ft or not fqid:
            print("[ibkr_flex] Secrets not set")
            return None
        base = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService"
        r1   = requests.get(f"{base}.SendRequest?t={ft}&q={fqid}&v=3", timeout=30)
        rt1  = ET.fromstring(r1.text)
        if rt1.findtext("Status") != "Success":
            print(f"[ibkr_flex] SendRequest failed: {r1.text[:150]}")
            return None
        ref  = rt1.findtext("ReferenceCode")
        _t.sleep(5)
        r2   = requests.get(f"{base}.GetStatement?t={ft}&q={ref}&v=3", timeout=30)
        rt2  = ET.fromstring(r2.text)
        eq   = rt2.find(".//EquitySummaryInBase")
        net_liq = float(eq.get("total", 0)) if eq is not None else 0.0
        cn   = rt2.find(".//CashReportCurrency[@currency='BASE_SUMMARY']")
        cash = float(cn.get("endingCash", 0)) if cn is not None else 0.0
        positions = []
        upnl_total = 0.0
        for p in rt2.findall(".//OpenPosition[@levelOfDetail='SYMBOL']"):
            upnl = float(p.get("fifoPnlUnrealized", 0))
            upnl_total += upnl
            positions.append({
                "ticker": p.get("symbol",""),
                "position": int(float(p.get("position",0))),
                "market_value":  round(float(p.get("positionValue",0)), 2),
                "market_price":  round(float(p.get("markPrice",0)), 2),
                "unrealized_pnl": round(upnl, 2),
                "daily_pnl": None
            })
        print(f"[ibkr_flex] {len(positions)} positions, net_liq={net_liq:.2f}")
        return {"net_liq": round(net_liq,2), "cash": round(cash,2),
                "unrealized_pnl": round(upnl_total,2), "daily_pnl": None,
                "positions": positions, "source": "flex (end-of-day)"}
    except Exception as ex:
        print(f"[ibkr_flex] Error: {ex}")
        return None

# BUILD MESSAGE

market   = get_market()
enriched = load_enriched()

cal_events = (enriched or {}).get("calendar_events")
ibkr       = (enriched or {}).get("ibkr")

# Cloud fallbacks when computer was off
if cal_events is None:
    cal_events = get_calendar_cloud()
if ibkr is None:
    ibkr = get_ibkr_flex()

lines = [
    f"\U0001f305 <b>Good morning! {day_name}, {now.strftime('%d/%m/%Y')}</b>",
    "",
]

# Calendar
lines.append("\U0001f4c5 <b>Today's Calendar:</b>")
if cal_events is None:
    lines.append("\u2022 Not connected (computer was off at 9:05)")
elif len(cal_events) == 0:
    lines.append("\u2022 No events today")
else:
    for ev in cal_events:
        t     = ev.get("time", "")
        title = ev.get("title", "")
        icon  = "\U0001f553" if t not in ("All day", "") else "\U0001f4cc"
        lines.append(f"\u2022 {icon} {t} - {title}" if t else f"\u2022 \U0001f4cc {title}")

# Market
lines += ["", "\U0001f4c8 <b>Markets:</b>"]
for sym, d in market.items():
    if "error" in d:
        lines.append(f"\u2022 <b>{sym}</b> ({d['label']}) - error")
    else:
        week_part = f" | Week: {pct(d['week'])}" if d["week"] is not None else ""
        lines.append(f"\u2022 <b>{sym}</b> ({d['label']}): ${d['price']} | Day: {pct(d['day'])}{week_part}")

# IBKR
lines += ["", "\U0001f4bc <b>IBKR Portfolio:</b>"]
if ibkr is None:
    lines.append("\u2022 Not connected (computer was off at 9:05)")
else:
    net_liq       = ibkr.get("net_liq", 0)
    cash          = ibkr.get("cash", 0)
    daily_pnl     = ibkr.get("daily_pnl", 0)
    unrealized    = ibkr.get("unrealized_pnl", 0)
    positions     = ibkr.get("positions", [])

    lines.append(f"\U0001f4b0 Total value: <b>${net_liq:,.2f}</b> | Cash: ${cash:,.2f}")
    lines.append(f"\U0001f4ca Daily: {fmt_usd(daily_pnl)} | Unrealized: {fmt_usd(unrealized)}")

    winners = [p for p in positions if p.get("unrealized_pnl", 0) > 0]
    losers  = [p for p in positions if p.get("unrealized_pnl", 0) <= 0]

    if winners:
        parts = " | ".join(
            f"<b>{p['ticker']}</b> +${p['unrealized_pnl']:.2f}"
            for p in winners[:3]
        )
        lines.append(f"\U0001f3c6 {parts}")
    if losers:
        parts = " | ".join(
            f"<b>{p['ticker']}</b> -${abs(p['unrealized_pnl']):.2f}"
            for p in losers[:3]
        )
        lines.append(f"\U0001f4c9 {parts}")

lines += [
    "",
    f"<i>Auto-sent at {now.strftime('%H:%M')} | GitHub Actions \u2601\ufe0f</i>",
]

msg = "\n".join(lines)

# SEND

resp = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
    timeout=15,
)
resp.raise_for_status()
data = resp.json()
print(f"Sent OK message_id={data['result']['message_id']}")
