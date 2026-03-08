"""Lighter DEX Portfolio Tracker — Discord Bot

Account : 281474976622700
Tracks  : Spot USDC  +  Spot LIT  +  Staking LIT (Public Pool)

Total USD = spot_usdc
          + spot_lit  * lit_price
          + (user_shares / pool_total_shares * pool_lit_balance) * lit_price
"""

import argparse, json, os, sys
from datetime import datetime, timezone
import requests

# ── config ──────────────────────────────────────────────
API      = "https://mainnet.zklighter.elliot.ai"
ACCT     = 281474976622700          # target account
POOL     = 281474976624800          # LIT staking pool
LIT_MKT  = 120                     # LIT perp market_id (for price)
CACHE    = ".cache/state.json"
EXPLORER = f"https://app.lighter.xyz/explorer/accounts/{ACCT}"

# ── helpers ─────────────────────────────────────────────
def log(m):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {m}")

def get(path, p):
    r = requests.get(f"{API}{path}", params=p, timeout=30)
    r.raise_for_status()
    return r.json()

# ── fetch portfolio ─────────────────────────────────────
def fetch():
    a  = get("/api/v1/account", {"by":"index","value":str(ACCT)})["accounts"][0]
    po = get("/api/v1/account", {"by":"index","value":str(POOL)})["accounts"][0]
    mk = get("/api/v1/orderBookDetails", {"market_id": LIT_MKT})

    # LIT price
    lp = l24 = 0.0
    for m in mk.get("order_book_details", []):
        if m["symbol"] == "LIT":
            lp  = float(m["last_trade_price"])
            l24 = float(m.get("daily_price_change", 0))

    # spot balances
    usdc = lit = 0.0
    for x in a.get("assets", []):
        b = float(x.get("balance","0")) + float(x.get("locked_balance","0"))
        if   x["symbol"] == "USDC": usdc = b
        elif x["symbol"] == "LIT":  lit  = b

    # staking shares
    ush = 0; princ = 0.0
    for s in a.get("shares", []):
        if s.get("public_pool_index") == POOL:
            ush   = int(s.get("shares_amount", 0))
            princ = float(s.get("principal_amount", "0"))

    psh = int(po.get("pool_info",{}).get("total_shares", 1))
    plit = 0.0
    for x in po.get("assets", []):
        if x["symbol"] == "LIT":
            plit = float(x.get("balance", "0"))

    # staking LIT amount
    sl = (ush / psh) * plit if psh > 0 else 0.0

    su = sl  * lp          # staking USD
    lu = lit * lp          # spot LIT USD
    total = usdc + lu + su

    return dict(
        ts=datetime.now(timezone.utc).isoformat(),
        total=total, usdc=usdc, lit=lit, lit_u=lu,
        sl=sl, su=su, princ=princ,
        lp=lp, l24=l24,
        ush=ush, psh=psh, plit=plit,
    )

# ── cache ───────────────────────────────────────────────
def load():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return None

def save(d):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(d, f)

# ── discord ─────────────────────────────────────────────
def post(wh, embeds):
    requests.post(
        wh, json={"username": "Lighter Bot", "embeds": embeds}, timeout=30
    ).raise_for_status()

def mkembed(c, p, dbg):
    t = c["total"]

    # diff
    diff = pct = None; up = None
    if p:
        pt = p["total"]; diff = t - pt
        pct = (diff / pt * 100) if pt else 0.0
        up  = diff >= 0

    # style
    if   up is True:  color, ttl = 0x00FF88, "📈 UP"
    elif up is False: color, ttl = 0xFF4444, "📉 DOWN"
    else:             color, ttl = 0x888888, "📊 初回スナップショット"
    if dbg: ttl = f"🔧 {ttl}"

    fl = []

    # total
    fl.append(dict(name="💰 総資産", value=f"**${t:,.2f}**", inline=True))

    # change
    if diff is not None:
        s = "+" if up else "-"
        fl.append(dict(
            name="📊 変動",
            value=f"**{s}${abs(diff):,.4f}** ({s}{abs(pct):.2f}%)",
            inline=True))

    # LIT price + 24h
    lcs = "+" if c["l24"] >= 0 else ""
    fl.append(dict(
        name="🪙 LIT",
        value=f"${c['lp']:,.4f} ({lcs}{c['l24']:.1f}% 24h)",
        inline=True))

    # breakdown
    fl.append(dict(name="💵 USDC",    value=f"${c['usdc']:,.2f}", inline=True))
    fl.append(dict(
        name="🔒 Staking",
        value=f"{c['sl']:,.2f} LIT (${c['su']:,.2f})",
        inline=True))

    if c["lit"] > 0.0001:
        fl.append(dict(
            name="✨ Spot LIT",
            value=f"{c['lit']:,.4f} LIT (${c['lit_u']:,.4f})",
            inline=True))

    # LIT vs previous
    if p and p.get("lp"):
        pl = p["lp"]; ld = c["lp"] - pl
        lpc = (ld / pl * 100) if pl else 0
        s = "+" if ld >= 0 else ""
        fl.append(dict(
            name="LIT 前回比",
            value=f"${pl:,.4f} → ${c['lp']:,.4f} ({s}{ld:,.4f} / {s}{lpc:.2f}%)",
            inline=False))

    # debug extras
    if dbg:
        fl.append(dict(
            name="🔧 Shares",
            value=(f"User: {c['ush']:,} / Pool: {c['psh']:,}\n"
                   f"Pool LIT: {c['plit']:,.2f} | Principal: {c['princ']:,.4f}"),
            inline=False))
        if p:
            fl.append(dict(
                name="🔧 前回データ",
                value=f"${p['total']:,.4f} @ {p.get('ts','?')}",
                inline=False))

    return dict(
        title=ttl, color=color, fields=fl,
        url=EXPLORER,
        footer=dict(text=f"Lighter DEX │ {ACCT}"))

# ── main ────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    wh = os.environ.get("DISCORD_WEBHOOK_URL")
    if not wh:
        print("ERROR: DISCORD_WEBHOOK_URL not set"); sys.exit(1)

    log("Fetching portfolio...")
    try:
        c = fetch()
    except Exception as e:
        log(f"ERROR: {e}")
        post(wh, [dict(title="❌ Error", description=f"```{e}```", color=0xFF0000)])
        sys.exit(1)

    log(f"Total: ${c['total']:,.2f} | LIT: ${c['lp']:,.4f} | "
        f"USDC: {c['usdc']:.2f} | Staking: {c['sl']:.2f} LIT")

    p = load()
    if p:
        log(f"Previous: ${p['total']:,.2f} | Diff: ${c['total']-p['total']:+,.4f}")
    else:
        log("First run — no previous data")

    post(wh, [mkembed(c, p, args.debug)])
    log("Sent to Discord!")

    save(c)
    log("State saved.")

if __name__ == "__main__":
    main()
