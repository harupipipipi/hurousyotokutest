"""Lighter Portfolio Bot v7 — SDK + Correct PnL + Full Stats
   Uses lighter-sdk for type-safe API access.
   PnL entries are DAILY DELTAS; ignore_transfers may not work,
   so we manually subtract inflow and add outflow.
   Volume from accountMetadata trade stats.
   Debug logging for PnL entry verification.
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

def log(m):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {m}")

def sf(v, default=0.0):
    if v is None: return default
    try: return float(v)
    except (ValueError, TypeError): return default

# ───────────────────────────────────────────────

async def fetch_all():
    cfg    = lighter.Configuration(host=API_HOST)
    token  = os.environ.get("LIGHTER_READ_TOKEN", "")
    client = lighter.ApiClient(configuration=cfg)

    try:
        acct_api  = lighter.AccountApi(client)
        order_api = lighter.OrderApi(client)

        # ── public data ──────────────────────────
        ac_resp = await acct_api.account(by="index", value=str(ACCT))
        po_resp = await acct_api.account(by="index", value=str(POOL))
        obd     = await order_api.order_book_details()

        ac = ac_resp.accounts[0]
        po = po_resp.accounts[0]

        # prices
        prices   = {}
        jpy_rate = 0.0
        if obd.order_book_details:
            for m in obd.order_book_details:
                prices[m.symbol] = sf(m.last_trade_price)
                if m.market_id == JPY_MID:
                    jpy_rate = sf(m.last_trade_price)
        if obd.spot_order_book_details:
            for m in obd.spot_order_book_details:
                sym = m.symbol.split("/")[0]
                if sym not in prices:
                    prices[sym] = sf(m.last_trade_price)
        try:
            ad = await order_api.asset_details()
            if ad.asset_details:
                for a in ad.asset_details:
                    if a.symbol not in prices:
                        prices[a.symbol] = sf(a.index_price)
        except Exception:
            pass

        # 1) Spot
        spot_usd = 0.0
        if ac.assets:
            for a in ac.assets:
                bal = sf(a.balance)
                spot_usd += bal if a.symbol == "USDC" else bal * prices.get(a.symbol, 0)

        # 2) Perp
        perp_usd = sf(ac.collateral)
        if ac.positions:
            for pos in ac.positions:
                perp_usd += sf(pos.allocated_margin) + sf(pos.unrealized_pnl)

        # 3) Staking
        ush = 0; principal_usdc = 0.0
        if ac.shares:
            for s in ac.shares:
                if s.public_pool_index == POOL:
                    ush = int(s.shares_amount or 0)
                    principal_usdc = sf(s.principal_amount)

        psh = int(po.pool_info.total_shares or 1) if po.pool_info else 1
        pool_lit = 0.0
        if po.assets:
            for a in po.assets:
                if a.symbol == "LIT": pool_lit = sf(a.balance)
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

        # ── PnL (auth) ──────────────────────────
        pnl_entries = None
        if token:
            try:
                now_ms   = int(time.time() * 1000)
                start_ms = now_ms - 86400 * 500 * 1000
                pnl_resp = await acct_api.pnl(
                    by="index", value=str(ACCT),
                    resolution="1d",
                    start_timestamp=start_ms, end_timestamp=now_ms,
                    count_back=500,
                    auth=token,
                    ignore_transfers=True,
                )
                pnl_entries = pnl_resp.pnl or []
                log(f"PnL API → {len(pnl_entries)} entries")
                if pnl_entries:
                    first = pnl_entries[0]
                    last  = pnl_entries[-1]
                    log(f"  [0]  ts={first.timestamp} trade={first.trade_pnl} "
                        f"spot={first.trade_spot_pnl} pool={first.pool_pnl} "
                        f"stk={first.staking_pnl} in={first.inflow} out={first.outflow} "
                        f"spot_in={first.spot_inflow} spot_out={first.spot_outflow} "
                        f"pool_in={first.pool_inflow} pool_out={first.pool_outflow} "
                        f"stk_in={first.staking_inflow} stk_out={first.staking_outflow}")
                    log(f"  [-1] ts={last.timestamp} trade={last.trade_pnl} "
                        f"spot={last.trade_spot_pnl} pool={last.pool_pnl} "
                        f"stk={last.staking_pnl} in={last.inflow} out={last.outflow} "
                        f"spot_in={last.spot_inflow} spot_out={last.spot_outflow} "
                        f"pool_in={last.pool_inflow} pool_out={last.pool_outflow} "
                        f"stk_in={last.staking_inflow} stk_out={last.staking_outflow}")
            except Exception as e:
                log(f"PnL API error: {e}")

        # ── Trade stats (volume) ─────────────────
        total_volume = 0.0
        if token:
            try:
                meta_resp = await acct_api.account_metadata(
                    by="index", value=str(ACCT), auth=token,
                )
                if meta_resp.account_metadatas:
                    meta = meta_resp.account_metadatas[0]
                    ts = getattr(meta, 'trade_stats', None)
                    if ts is None:
                        ts = meta.additional_properties.get('trade_stats', {})
                    if isinstance(ts, dict):
                        total_volume = sf(ts.get('total_volume'))
                    elif hasattr(ts, 'total_volume'):
                        total_volume = sf(ts.total_volume)
                    log(f"Trade volume: ${total_volume:,.2f}")
            except Exception as e:
                log(f"Metadata API error: {e}")

        # ── Leases / fee credit ──────────────────
        fee_credit = 0.0
        if token:
            try:
                lease_resp = await acct_api.leases(
                    account_index=ACCT, auth=token, limit=50,
                )
                if lease_resp.leases:
                    for ls in lease_resp.leases:
                        if hasattr(ls, 'status') and ls.status == 'leased':
                            amt = sf(getattr(ls, 'fee_amount', 0))
                            for k in ('remaining_fee_credit', 'fee_credit', 'pending_fee'):
                                v = ls.additional_properties.get(k)
                                if v is not None:
                                    amt = sf(v); break
                            fee_credit += amt
                    log(f"Fee credit: ${fee_credit:,.2f}")
            except Exception as e:
                log(f"Lease API error: {e}")

        return portfolio, pnl_entries, total_volume, fee_credit

    finally:
        await client.close()

# ───────────────────────────────────────────────

def compute_stats(entries, total_usd, total_volume_api):
    """
    PnL entries are DAILY DELTAS.
    ignore_transfers=True may not zero inflow/outflow on all API versions,
    so we always manually strip them:
      pure daily PnL = (trade_pnl + trade_spot_pnl + pool_pnl + staking_pnl)
                     - (inflow + spot_inflow + pool_inflow + staking_inflow)
                     + (outflow + spot_outflow + pool_outflow + staking_outflow)
    inflow  = deposits INTO account   (inflates balance, not real profit)
    outflow = withdrawals FROM account (deflates balance, not real loss)
    """
    if not entries:
        return None

    n = len(entries)

    def entry_raw(e):
        return sf(e.trade_pnl) + sf(e.trade_spot_pnl) + sf(e.pool_pnl) + sf(e.staking_pnl)

    def entry_inflow(e):
        return sf(e.inflow) + sf(e.spot_inflow) + sf(e.pool_inflow) + sf(e.staking_inflow)

    def entry_outflow(e):
        return sf(e.outflow) + sf(e.spot_outflow) + sf(e.pool_outflow) + sf(e.staking_outflow)

    daily_pnls = []
    cum_pnl    = 0.0
    total_in   = 0.0
    total_out  = 0.0

    for e in entries:
        raw  = entry_raw(e)
        inf  = entry_inflow(e)
        outf = entry_outflow(e)
        day  = raw - inf + outf
        daily_pnls.append(day)
        cum_pnl  += day
        total_in += inf
        total_out += outf

    log(f"  raw_sum=${sum(entry_raw(e) for e in entries):,.4f} "
        f"total_inflow=${total_in:,.4f} total_outflow=${total_out:,.4f} "
        f"pure_pnl=${cum_pnl:,.4f}")

    deposit_est = total_usd - cum_pnl
    return_rate = (cum_pnl / deposit_est * 100) if abs(deposit_est) > 0.01 else 0.0
    avg_daily   = cum_pnl / n if n > 0 else 0.0

    if n > 1:
        mean = sum(daily_pnls) / n
        var  = sum((x - mean) ** 2 for x in daily_pnls) / (n - 1)
        vol  = math.sqrt(var)
    else:
        vol = 0.0

    sharpe = (avg_daily / vol) if vol > 0 else 0.0

    cum = peak = mdd = 0.0
    for p in daily_pnls:
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > mdd: mdd = dd

    volume = total_volume_api if total_volume_api > 0 else 0.0

    log(f"  cum=${cum_pnl:,.4f} dep≈${deposit_est:,.2f} "
        f"ret={return_rate:,.2f}% avg=${avg_daily:,.4f} "
        f"σ=${vol:,.4f} sharpe={sharpe:,.2f} mdd=${mdd:,.4f}")

    return dict(
        cumulative_pnl=cum_pnl, volume=volume,
        return_rate=return_rate, avg_daily_pnl=avg_daily,
        volatility=vol, sharpe=sharpe, max_drawdown=mdd,
        deposit_est=deposit_est, n_days=n,
    )

# ───────────────────────────────────────────────

def ld(path):
    try:
        with open(path) as f:
            d = json.load(f); return d if "jpy" in d else None
    except Exception: return None

def sv(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f: json.dump(d, f)

def notify(wh, embeds):
    requests.post(wh, json={"username": "Lighter Bot", "embeds": embeds},
                  timeout=30).raise_for_status()

def _s(v): return "+" if v >= 0 else "-"

def pm(val, pct):
    s = _s(val); return f"{s}¥{abs(val):,.0f} ({s}{abs(pct):.2f}%)"

def pm_usd(val):
    s = _s(val); return f"{s}${abs(val):,.2f}"

def build(c, prev, stats, fee_credit):
    j = c["jpy"]; jpy_rate = c["jpy_rate"]

    if prev:
        d = j - prev["jpy"]; dp = (d / prev["jpy"] * 100) if prev["jpy"] else 0; up = d >= 0
    else:
        d = dp = None; up = None

    pnl_usd = bd = bp = None; bup = None
    if stats:
        pnl_usd = stats["cumulative_pnl"]
        bd = pnl_usd * jpy_rate; bup = pnl_usd >= 0; bp = stats["return_rate"]

    if   up is True:  color, ttl = 0x00FF88, "📈 UP"
    elif up is False: color, ttl = 0xFF4444, "📉 DOWN"
    else:             color, ttl = 0x888888, "📊 開始"

    lines = [f"## 💰 ¥{j:,.0f}"]
    if d is not None: lines.append(f"前回比: {pm(d, dp)}")
    if bd is not None:
        lines.append(f"{'📈' if bup else '📉'} 通算: {pm(bd, bp)}")

    embed = dict(title=ttl, description="\n".join(lines), color=color,
                 footer=dict(text=f"LIT ${c['lp']:,.4f} │ ¥{jpy_rate:,.1f}/$ │ {c['ts']}"))

    if stats:
        f = []
        if pnl_usd is not None:
            f.append({"name": "損益",              "value": pm_usd(pnl_usd),                  "inline": True})
        if stats["volume"] > 0:
            f.append({"name": "取引量",             "value": f"${stats['volume']:,.2f}",        "inline": True})
        f.append(    {"name": "リターン率",          "value": f"{stats['return_rate']:,.2f}%",   "inline": True})
        f.append(    {"name": "平均日次損益",        "value": pm_usd(stats["avg_daily_pnl"]),    "inline": True})
        f.append(    {"name": "損益ボラティリティ",   "value": f"${stats['volatility']:,.2f}",    "inline": True})
        f.append(    {"name": "シャープ",            "value": f"{stats['sharpe']:,.2f}",         "inline": True})
        f.append(    {"name": "最大ドローダウン",     "value": f"${stats['max_drawdown']:,.2f}",  "inline": True})
        if fee_credit > 0:
            f.append({"name": "削減予定手数料",       "value": f"${fee_credit:,.2f}",             "inline": True})
        embed["fields"] = f

    return embed

# ───────────────────────────────────────────────

def main():
    wh = os.environ.get("DISCORD_WEBHOOK_URL")
    if not wh: print("DISCORD_WEBHOOK_URL not set"); sys.exit(1)

    log("Fetching via SDK …")
    try:
        c, pnl_entries, total_volume, fee_credit = asyncio.run(fetch_all())
    except Exception as e:
        log(f"FETCH ERROR: {e}")
        try: notify(wh, [dict(title="❌", description=f"```{e}```", color=0xFF0000)])
        except Exception: pass
        sys.exit(1)

    log(f"¥{c['jpy']:,.0f} (${c['usd']:,.2f}) "
        f"[spot=${c['spot_usd']:,.2f} perp=${c['perp_usd']:,.2f} stk=${c['stk_usd']:,.2f}]")

    prev = ld(STATE)
    stats = compute_stats(pnl_entries, c["usd"], total_volume) if pnl_entries else None

    if stats:
        log(f"PnL: ${stats['cumulative_pnl']:,.2f} "
            f"(¥{stats['cumulative_pnl'] * c['jpy_rate']:,.0f})")
    else:
        log("PnL unavailable")

    notify(wh, [build(c, prev, stats, fee_credit)])
    log("Sent!")
    sv(STATE, c); log("Done.")

if __name__ == "__main__": main()
