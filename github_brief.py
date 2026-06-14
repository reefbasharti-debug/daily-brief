"""
github_brief.py - Minimal daily brief for GitHub Actions (cloud)
Sends market data to Telegram. No local dependencies needed.
Requires: yfinance requests pytz
Env vars: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""
import os, sys, requests, pytz
import yfinance as yf
from datetime import datetime

TOKEN   = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TZ_IL = pytz.timezone("Asia/Jerusalem")
now   = datetime.now(TZ_IL)

DAYS_HE = {0:"שני",1:"שלישי",2:"רביעי",3:"חמישי",4:"שישי",5:"שבת",6:"ראשון"}
day_name = DAYS_HE[now.weekday()]

def pct(val):
    if val is None: return "N/A"
    sign = "+" if val >= 0 else ""
    emoji = "✅" if val >= 0 else "🔴"
    return f"{emoji} {sign}{val:.2f}%"

def get_market():
    results = {}
    labels = {"SPY": "S&P 500", "DIA": "דאו ג׳ונס", "VXX": "VIX (פחד)"}
    days_since_sun = {6:0, 0:1, 1:2, 2:3, 3:4, 4:5}.get(now.weekday(), 0)

    for sym, label in labels.items():
        try:
            tk = yf.Ticker(sym)
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

market = get_market()

lines = [
    f"\U0001F305 <b>בוקר טוב! יום {day_name}, {now.strftime('%d/%m/%Y')}</b>",
    "",
    "\U0001F4C8 <b>שוק ההון:</b>",
]

for sym, d in market.items():
    if "error" in d:
        lines.append(f"• <b>{sym}</b> ({d['label']}) — שגיאה")
    else:
        week_part = f" | שבוע: {pct(d['week'])}" if d["week"] is not None else ""
        lines.append(f"• <b>{sym}</b> ({d['label']}): ${d['price']} | יום: {pct(d['day'])}{week_part}")

lines += [
    "",
    "\U0001F4C5 <b>לוח שנה:</b> לא מחובר",
    "\U0001F4BC <b>תיק IBKR:</b> לא מחובר",
    "",
    f"<i>נשלח אוטומטית ב-{now.strftime('%H:%M')} | GitHub Actions ☁️</i>",
]

msg = "\n".join(lines)

resp = requests.post(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
    timeout=15,
)
resp.raise_for_status()
data = resp.json()
print(f"Sent OK message_id={data['result']['message_id']}")
