"""Lighter Portfolio Bot v3 — JPY simple view"""
import json, os, sys
from datetime import datetime, timezone, timedelta
import requests

API     = "https://mainnet.zklighter.elliot.ai"
ACCT    = 281474976622700
POOL    = 281474976624800
LIT_MID = 120
JPY_MID = 98
STATE   = ".cache/state.json"
BASE    = ".cache/baseline.json"
JST     = timezone(timedelta(hours=9))

def log(m):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {m}")

def get(path, p):
    r = requests.get(f"{API}{path}", params=p, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch():
    ac = get("/api/v1/account",{"by":"index","value":str(ACCT)})["accounts"][0]
    po = get("/api/v1/account",{"by":"index","value":str(POOL)})["accounts"][0]
    mk = get("/api/v1/orderBookDetails",{})

    lp = jpy = 0.0
    for m in mk.get("order_book_details",[]):
        if m["market_id"]==LIT_MID: lp  = float(m["last_trade_price"])
        if m["market_id"]==JPY_MID: jpy = float(m["last_trade_price"])

    usdc = lit = 0.0
    for a in ac.get("assets",[]):
        b = float(a.get("balance","0"))+float(a.get("locked_balance","0"))
        if   a["symbol"]=="USDC": usdc=b
        elif a["symbol"]=="LIT":  lit=b

    ush=0
    for s in ac.get("shares",[]):
        if s.get("public_pool_index")==POOL:
            ush=int(s.get("shares_amount",0))

    psh=int(po.get("pool_info",{}).get("total_shares",1))
    pl=0.0
    for a in po.get("assets",[]):
        if a["symbol"]=="LIT": pl=float(a.get("balance","0"))

    sl=(ush/psh)*pl if psh>0 else 0.0
    usd=usdc+lit*lp+sl*lp

    now=datetime.now(JST)
    return dict(ts=now.strftime("%-m/%-d %H:%M"),
                usd=usd, jpy=usd*jpy, lp=lp, jpy_rate=jpy)

def ld(path):
    try:
        with open(path) as f:
            d=json.load(f)
            if "jpy" not in d: return None
            return d
    except Exception: return None

def sv(path,d):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,"w") as f: json.dump(d,f)

def notify(wh, embeds):
    requests.post(wh,json={"username":"Lighter Bot","embeds":embeds},timeout=30).raise_for_status()

def pm(val,pct):
    s="+" if val>=0 else "-"
    return f"{s}¥{abs(val):,.0f} ({s}{abs(pct):.2f}%)"

def build(c, prev, base):
    j=c["jpy"]

    if prev:
        d=j-prev["jpy"]; dp=(d/prev["jpy"]*100) if prev["jpy"] else 0; up=d>=0
    else:
        d=dp=None; up=None

    if base:
        bd=j-base["jpy"]; bp=(bd/base["jpy"]*100) if base["jpy"] else 0; bup=bd>=0
    else:
        bd=bp=None; bup=None

    if   up is True:  color,ttl=0x00FF88,"📈 UP"
    elif up is False: color,ttl=0xFF4444,"📉 DOWN"
    else:             color,ttl=0x888888,"📊 開始"

    lines=[f"## 💰 ¥{j:,.0f}"]
    if d is not None:
        lines.append(f"前回比: {pm(d,dp)}")
    if bd is not None:
        ico="📈" if bup else "📉"
        lines.append(f"{ico} 通算: {pm(bd,bp)}　(¥{base['jpy']:,.0f} から)")

    return dict(title=ttl, description="\n".join(lines), color=color,
                footer=dict(text=f"LIT ${c['lp']:,.4f} │ ¥{c['jpy_rate']:,.1f}/$ │ {c['ts']}"))

def main():
    wh=os.environ.get("DISCORD_WEBHOOK_URL")
    if not wh: print("DISCORD_WEBHOOK_URL not set"); sys.exit(1)

    log("Fetching...")
    try: c=fetch()
    except Exception as e:
        log(f"ERROR: {e}")
        notify(wh,[dict(title="❌",description=f"```{e}```",color=0xFF0000)])
        sys.exit(1)

    log(f"¥{c['jpy']:,.0f} (${c['usd']:,.2f})")

    prev=ld(STATE)
    base=ld(BASE)

    if not base:
        sv(BASE,c); base=None
        log("Baseline set")

    notify(wh,[build(c,prev,base)]); log("Sent!")
    sv(STATE,c); log("Done.")

if __name__=="__main__": main()
