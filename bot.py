"""Lighter Portfolio Bot v6 — SDK + Accurate PnL + Full Statistics
   Uses official lighter Python SDK for type-safe API access.
   Read-Only Token auth for PnL / leases.
"""
import asyncio, json, math, os, sys, time
from datetime import datetime, timezone, timedelta

import lighter
import requests

ACCT      = 281474976622700
POOL      = 281474976624800
JPY_MID   = 98
STATE     = ".cache/state.json"
JST       = timezone(timedelta(hours=9))
API_HOST  = "https://mainnet.zklighter.elliot.ai"

# ───────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────

def log(m):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {m}")

def sf(v, default=0.0):
    """safe-float: str / int / None → float"""
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default

# ───────────────────────────────────────────────
# SDK-based data fetching
# ───────────────────────────────────────────────

async def fetch_all():
    """Fetch portfolio, PnL, leases via lighter SDK."""
    cfg    = lighter.Configuration(host=API_HOST)
    token  = os.environ.get("LIGHTER_READ_TOKEN", "")
    client = lighter.ApiClient(configuration=cfg)

    try:
        acct_api  = lighter.AccountApi(client)
        order_api = lighter.OrderApi(client)

        # ── public data (no auth) ───────────────
        ac_resp = await acct_api.account(by="index", value=str(ACCT))
        po_resp = await acct_api.account(by="index", value=str(POOL))
        ad_resp = await order_api.order_book_details()

        ac = ac_resp.accounts[0]
        po = po_resp.accounts[0]

        # ── prices ──────────────────────────────
        prices   = {}
        jpy_rate = 0.0
        if ad_resp.order_book_details:
            for m in ad_resp.order_book_details:
                prices[m.symbol] = sf(m.last_trade_price)
                if m.market_id == JPY_MID:
                    jpy_rate = sf(m.last_trade_price)
        if ad_resp.spot_order_book_details:
            for m in ad_resp.spot_order_book_details:
                sym = m.symbol.split("/")[0]
                if sym not in prices:
                    prices[sym] = sf(m.last_trade_price)

        # asset index prices as fallback
        asset_api = lighter.OrderApi(client)
        try:
            ad_detail = await asset_api.asset_details()
            if ad_detail.asset_details:
                for a in ad_detail.asset_details:
                    if a.symbol not in prices:
                        prices[a.symbol] = sf(a.index_price)
        except Exception:
            pass

        # ── 1) Spot ─────────────────────────────
        spot_usd = 0.0
        if ac.assets:
            for a in ac.assets:
                bal = sf(a.balance)
                if a.symbol == "USDC":
                    spot_usd += bal
                else:
                    spot_usd += bal * prices.get(a.symbol, 0)

        # ── 2) Perp ─────────────────────────────
        perp_usd = sf(ac.collateral)
        if ac.positions:
            for pos in ac.positions:
                perp_usd += sf(pos.allocated_margin)
                perp_usd += sf(pos.unrealized_pnl)

        # ── 3) Staking ──────────────────────────
        ush = 0
        principal_usdc = 0.0
        if ac.shares:
            for s in ac.shares:
                if s.public_pool_index == POOL:
                    ush = int(s.shares_amount or 0)
                    principal_usdc = sf(s.principal_amount)

        psh = int(po.pool_info.total_shares or 1) if po.pool_info else 1
        pool_lit = 0.0
        if po.assets:
            for a in po.assets:
                if a.symbol == "LIT":
                    pool_lit = sf(a.balance)

        stk_lit = (ush / psh) * pool_lit if psh > 0 else 0.0
        stk_usd = stk_lit * prices.get("LIT", 0)

        total_usd = spot_usd + perp_usd + stk_usd

        now = datetime.now(JST)
        portfolio = dict(
            ts=now.strftime("%-m/%-d %H:%M"),
            usd=total_usd, jpy=total_usd * jpy_rate,
            lp=prices.get("LIT", 0), jpy_rate=jpy_rate,
            spot_usd=spot_usd, perp_usd=perp_usd,
            stk_usd=stk_usd, stk_lit=stk_lit,
            principal_usdc=principal_usdc,
        )

        # ── PnL (auth required) ─────────────────
        pnl_entries = None
        if token:
            try:
                now_ms   = int(time.time() * 1000)
                start_ms = now_ms - 86400 * 500 * 1000
                pnl_resp = await acct_api.pnl(
                    by="index",
                    value=str(ACCT),
                    resolution="1d",
                    start_timestamp=start_ms,
                    end_timestamp=now_ms,
                    count_back=500,
                    auth=token,
                    ignore_transfers=True,
                )
                pnl_entries = pnl_resp.pnl or []
                log(f"PnL API → {len(pnl_entries)} entries")
            except Exception as e:
                log(f"PnL API error: {e}")

        # ── Leases / fee credit (auth required) ──
        fee_credit = 0.0
        if token:
            try:
                lease_resp = await acct_api.leases(
                    account_index=ACCT,
                    auth=token,
                    limit=50,
                )
                if lease_resp.leases:
                    for ls in lease_resp.leases:
                        fee_credit += sf(getattr(ls, "remaining_fee_credit",
                                         getattr(ls, "fee_credit", 0)))
                log(f"Fee credit: ${fee_credit:,.2f}")
            except Exception as e:
                log(f"Lease API error: {e}")

        return portfolio, pnl_entries, fee_credit

    finally:
        await client.close()

# ───────────────────────────────────────────────
# statistics from PnL entries
# ───────────────────────────────────────────────

def compute_stats(entries, total_usd):
    """
    Each PnLEntry = daily delta.
    Cumulative PnL = Σ (trade_pnl + trade_spot_pnl + pool_pnl + staking_pnl)
    Safety net: subtract inflows / add outflows in case ignore_transfers failed.
    """
    if not entries:
        return None

    daily_pnls   = []
    cum_pnl      = 0.0
    total_volume  = 0.0

    for e in entries:
        tp  = sf(e.trade_pnl)
        tsp = sf(e.trade_spot_pnl)
        pp  = sf(e.pool_pnl)
        skp = sf(e.staking_pnl)

        # safety net: strip leaked transfers
        inf  = (sf(e.inflow) + sf(e.spot_inflow)
              + sf(e.pool_inflow) + sf(e.staking_inflow))
        outf = (sf(e.outflow) + sf(e.spot_outflow)
              + sf(e.pool_outflow) + sf(e.staking_outflow))

        day = tp + tsp + pp + skp - inf + outf
        daily_pnls.append(day)
        cum_pnl += day
        total_volume += abs(tp) + abs(tsp)

    n = len(daily_pnls)
    if n == 0:
        return None

    # deposit ≈ current equity − cumulative PnL
    deposit_est = total_usd - cum_pnl
    return_rate = (cum_pnl / deposit_est * 100) if deposit_est > 0 else 0.0

    avg_daily = cum_pnl / n

    # volatility (sample std-dev)
    if n > 1:
        var = sum((x - avg_daily) ** 2 for x in daily_pnls) / (n - 1)
        vol = math.sqrt(var)
    else:
        vol = 0.0

    # Sharpe (daily, risk-free = 0)
    sharpe = (avg_daily / vol) if vol > 0 else 0.0

    # max drawdown
    cum = peak = mdd = 0.0
    for p in daily_pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > mdd:
            mdd = dd

    log(f"  cum=${cum_pnl:,.4f} dep≈${deposit_est:,.2f} "
        f"ret={return_rate:,.2f}% avg=${avg_daily:,.4f} "
        f"σ=${vol:,.4f} sharpe={sharpe:,.2f} mdd=${mdd:,.4f}")

    return dict(
        cumulative_pnl=cum_pnl, volume=total_volume,
        return_rate=return_rate, avg_daily_pnl=avg_daily,
        volatility=vol, sharpe=sharpe, max_drawdown=mdd,
        deposit_est=deposit_est, n_days=n,
    )

# ───────────────────────────────────────────────
# persistence
# ───────────────────────────────────────────────

def ld(path):
    try:
        with open(path) as f:
            d = json.load(f)
            return d if "jpy" in d else None
    except Exception:
        return None

def sv(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f)

# ───────────────────────────────────────────────
# Discord embed
# ───────────────────────────────────────────────

def notify(wh, embeds):
    requests.post(wh, json={"username": "Lighter Bot", "embeds": embeds},
                  timeout=30).raise_for_status()

def _s(v):
    return "+" if v >= 0 else "-"

def pm(val, pct):
    s = _s(val)
    return f"{s}¥{abs(val):,.0f} ({s}{abs(pct):.2f}%)"

def pm_usd(val):
    s = _s(val)
    return f"{s}${abs(val):,.2f}"

def build(c, prev, stats, fee_credit):
    j        = c["jpy"]
    jpy_rate = c["jpy_rate"]

    # previous comparison
    if prev:
        d  = j - prev["jpy"]
        dp = (d / prev["jpy"] * 100) if prev["jpy"] else 0
        up = d >= 0
    else:
        d = dp = None; up = None

    # cumulative PnL
    pnl_usd = bd = bp = None; bup = None
    if stats:
        pnl_usd = stats["cumulative_pnl"]
        bd  = pnl_usd * jpy_rate
        bup = pnl_usd >= 0
        bp  = stats["return_rate"]

    # colour / title
    if   up is True:  color, ttl = 0x00FF88, "📈 UP"
    elif up is False: color, ttl = 0xFF4444, "📉 DOWN"
    else:             color, ttl = 0x888888, "📊 開始"

    # description
    lines = [f"## 💰 ¥{j:,.0f}"]
    if d is not None:
        lines.append(f"前回比: {pm(d, dp)}")
    if bd is not None:
        ico = "📈" if bup else "📉"
        lines.append(f"{ico} 通算: {pm(bd, bp)}")

    embed = dict(
        title=ttl, description="\n".join(lines), color=color,
        footer=dict(text=f"LIT ${c['lp']:,.4f} │ ¥{jpy_rate:,.1f}/$ │ {c['ts']}"),
    )

    # statistics fields
    if stats:
        fields = []
        if pnl_usd is not None:
            fields.append({"name": "損益",           "value": pm_usd(pnl_usd),                   "inline": True})
        if stats["volume"] > 0:
            fields.append({"name": "取引量",          "value": f"${stats['volume']:,.2f}",         "inline": True})
        fields.append(    {"name": "リターン率",       "value": f"{stats['return_rate']:,.2f}%",    "inline": True})
        fields.append(    {"name": "平均日次損益",     "value": pm_usd(stats["avg_daily_pnl"]),     "inline": True})
        fields.append(    {"name": "損益ボラティリティ", "value": f"${stats['volatility']:,.2f}",     "inline": True})
        fields.append(    {"name": "シャープ",         "value": f"{stats['sharpe']:,.2f}",          "inline": True})
        fields.append(    {"name": "最大ドローダウン",  "value": f"${stats['max_drawdown']:,.2f}",   "inline": True})
        if fee_credit > 0:
            fields.append({"name": "削減予定手数料",    "value": f"${fee_credit:,.2f}",              "inline": True})
        embed["fields"] = fields

    return embed

# ───────────────────────────────────────────────
# main
# ───────────────────────────────────────────────

def main():
    wh = os.environ.get("DISCORD_WEBHOOK_URL")
    if not wh:
        print("DISCORD_WEBHOOK_URL not set"); sys.exit(1)

    log("Fetching via SDK …")
    try:
        c, pnl_entries, fee_credit = asyncio.run(fetch_all())
    except Exception as e:
        log(f"FETCH ERROR: {e}")
        try:
            notify(wh, [dict(title="❌", description=f"```{e}```", color=0xFF0000)])
        except Exception:
            pass
        sys.exit(1)

    log(f"¥{c['jpy']:,.0f} (${c['usd']:,.2f}) "
        f"[spot=${c['spot_usd']:,.2f} perp=${c['perp_usd']:,.2f} "
        f"stk=${c['stk_usd']:,.2f}]")

    prev = ld(STATE)

    # stats
    stats = compute_stats(pnl_entries, c["usd"]) if pnl_entries else None
    if stats:
        log(f"PnL: ${stats['cumulative_pnl']:,.2f} "
            f"(¥{stats['cumulative_pnl'] * c['jpy_rate']:,.0f})")
    else:
        log("PnL unavailable — stats hidden")

    notify(wh, [build(c, prev, stats, fee_credit)])
    log("Sent!")
    sv(STATE, c)
    log("Done.")

if __name__ == "__main__":
    main()
