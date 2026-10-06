#!/usr/bin/env python3
"""
Bridge Táctico → Telegram  ·  v3.5 (misma lógica que el indicador Bridge Táctico v7.3)
Vigila tus favoritos en 1m / 3m / 5m / 15m y avisa por Telegram:
  nueva señal LONG/SHORT x/3 con entrada, DCA, SL, TP y CANTIDAD/MARGEN de cada entrada,
  TP1 alcanzado y cierre con el resultado en $.

Primera vez: doble clic en iniciar_bot.bat → te pide el token de @BotFather y detecta tu chat
cuando le envías /start a tu bot. Se guarda en config.json (no compartas ese archivo).

Comandos en Telegram:
  /radar  /estado  /agregar SOL XRP  /quitar SOL  /lista  /tf 1m 3m 5m 15m
  /capital 2000  /riesgo 2  /h4 si|no  /ayuda

Modos:
  python bot.py --loop   → corre sin parar en tu PC (necesario para 1m/3m/5m)
  python bot.py          → una sola pasada (GitHub Actions, 15m o más)

Variables de entorno (opcionales): TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (si no hay config.json),
  SYMBOLS ("BTCUSDT,ETHUSDT"), TIMEFRAMES ("1m,3m,5m,15m"), CAPITAL (2000), RISK_PCT (2),
  MIN_DIST (0.6), FEE_TAKER (0.06), FEE_MAKER (0.02), USE_H4 ("1" = filtro 4h en 1m-5m), TEST ("1")
"""
import json, os, re, sys, time, math, threading
import urllib.request, urllib.parse, urllib.error

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# ----------------------------- PARÁMETROS (iguales al indicador v7.3) -----------------------------
ST_FACTOR, ST_ATR = 4.5, 4
ZONE_ATR, TOUCH_WIN = 1.25, 6
SQZ_LEN, ADX_LEN = 20, 14
WT_N1, WT_N2, WT_LVL, WT_WIN = 10, 21, 40.0, 3
MIN_TRADE, USE_EMA, EMA_LEN = 2, True, 200
DCA_W = (0.20, 0.30, 0.50)        # E1 20 % · E2 30 % · E3 50 %  (15m y más)
LOW_W = (0.10, 0.30, 0.60)        # modo preciso 3m/5m: E1 10 % · E2 30 % · E3 60 %
LOW_MIN_DIST = 0.45               # modo preciso 3m/5m: SL mínimo 0.45 %
DCA_ATR, SL_ATR = 1.0, 1.0
TP_R = (0.5, 1.0, 2.0)
TP_SPLIT = (0.40, 0.30, 0.30)
MAX_LEV, LOSS_MARGIN_PCT = 50, 50.0   # apalancamiento recomendado: si toca el SL pierdes ~50 % del margen
LEV_STEPS = (5, 10, 15, 20, 25, 30, 40, 50)


def rec_leverage(worst):
    """Apalancamiento recomendado según la distancia al stop de ESA moneda (redondeado hacia abajo a 5/10/15/20/25/30/40/50x)."""
    raw = LOSS_MARGIN_PCT / max(worst * 100, 0.01)
    ok = [x for x in LEV_STEPS if x <= min(raw, MAX_LEV)]
    return ok[-1] if ok else LEV_STEPS[0]
MIN_DIST = float(os.environ.get("MIN_DIST", "0.6"))           # % mínimo entre entrada media y SL
FEE_TAKER = float(os.environ.get("FEE_TAKER", "0.06")) / 100  # E1 a mercado y stop
FEE_MAKER = float(os.environ.get("FEE_MAKER", "0.02")) / 100  # E2/E3 y TP con orden límite

TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}
H4_MS = 14_400_000
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
CONFIG_FILE = os.path.join(BASE, "config.json")
SOURCES = ["https://fapi.binance.com/fapi/v1/klines", "https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]
BITUNIX = "https://fapi.bitunix.com/api/v1/futures/market/kline"  # respaldo: monedas que no están en Binance (OKB, RAY...)
_bitunix_syms = set()
PLACEHOLDERS = ("", "PEGA_AQUI_TU_TOKEN", "PEGA_AQUI_TU_CHAT_ID")


# ----------------------------- DATOS -----------------------------
_cool_until, _cool_lock = {}, threading.Lock()   # fuente -> hasta cuándo no usarla (límite de Binance)


def _host(url):
    return urllib.parse.urlsplit(url).netloc


def _cool(url, secs):
    with _cool_lock:
        h = _host(url)
        _cool_until[h] = max(_cool_until.get(h, 0), time.time() + secs)


def usable_sources():
    now = time.time()
    return [b for b in SOURCES if _cool_until.get(_host(b), 0) <= now] or SOURCES[-1:]


def http_get(url, timeout=20):
    """GET con freno: si Binance avisa que vamos cerca del límite, esa fuente descansa y se usa la siguiente."""
    req = urllib.request.Request(url, headers={"User-Agent": "bridge-tactico-bot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            used = r.headers.get("X-MBX-USED-WEIGHT-1M")
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (418, 429):  # demasiadas peticiones: descansar antes de que Binance bloquee la IP
            _cool(url, int(e.headers.get("Retry-After") or (300 if e.code == 418 else 60)))
        raise
    limit = 1800 if "fapi." in url else 4800
    if used and int(used) > limit:
        _cool(url, 61 - time.time() % 60)
    return data


def fetch_bitunix(symbol, tf, total):
    """Velas cerradas de Bitunix Futuros (máx. 200 por petición; 3m se arma con velas de 1m)."""
    now = int(time.time() * 1000)
    if tf == "3m":
        out = {}
        for b in fetch_bitunix(symbol, "1m", total * 3 + 3):
            k = b["t"] // 180_000 * 180_000
            x = out.get(k)
            if not x:
                out[k] = dict(t=k, o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"], ct=k + 179_999)
            else:
                x["h"], x["l"], x["c"], x["v"] = max(x["h"], b["h"]), min(x["l"], b["l"]), b["c"], x["v"] + b["v"]
        return [out[k] for k in sorted(out) if out[k]["ct"] < now][-total:]
    ms, got, end = TF_MS[tf], {}, None
    while len(got) < total + 1:
        q = {"symbol": symbol, "interval": tf, "limit": 200}
        if end:
            q["endTime"] = end
        j = http_get(f"{BITUNIX}?{urllib.parse.urlencode(q)}", timeout=30)
        if j.get("code") != 0:
            raise RuntimeError(f"Bitunix: {j.get('msg')}")
        rows = j.get("data") or []
        for r in rows:
            t = int(r["time"])
            got[t] = dict(t=t, o=float(r["open"]), h=float(r["high"]), l=float(r["low"]), c=float(r["close"]),
                          v=float(r.get("baseVol") or 0), ct=t + ms - 1)
        if len(rows) < 200:
            break
        end = min(int(r["time"]) for r in rows)
        time.sleep(0.15)
    _bitunix_syms.add(symbol)
    return [got[t] for t in sorted(got) if got[t]["ct"] < now][-total:]


def fetch_klines(symbol, tf, limit=600):
    q = urllib.parse.urlencode({"symbol": symbol, "interval": tf, "limit": limit})
    last = None
    for base in ([] if symbol in _bitunix_syms else usable_sources()):
        try:
            rows = http_get(f"{base}?{q}")
            bars = [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5]), ct=int(k[6])) for k in rows]
            now = int(time.time() * 1000)
            return [b for b in bars if b["ct"] < now]  # solo velas cerradas
        except Exception as e:  # probar la siguiente fuente
            last = e
    try:
        return fetch_bitunix(symbol, tf, limit)
    except Exception as e:
        last = e
    raise RuntimeError(f"No se pudo descargar {symbol} {tf}: {last}")


def fetch_long(symbol, tf, total=4500):
    """Historial largo (varias peticiones) para calcular el winrate la primera vez."""
    last = None
    for base in ([] if symbol in _bitunix_syms else usable_sources()):  # futuros; si no responde (EE. UU.), spot
        try:
            return _fetch_long(base, symbol, tf, total)
        except Exception as e:
            last = e
    try:
        return fetch_bitunix(symbol, tf, total)
    except Exception as e:
        last = e
    raise RuntimeError(f"historial {symbol} {tf}: {last}")


def _fetch_long(base, symbol, tf, total):
    out, end = [], None
    while len(out) < total:
        q = {"symbol": symbol, "interval": tf, "limit": min(1000, total - len(out))}
        if end:
            q["endTime"] = end
        rows = http_get(f"{base}?{urllib.parse.urlencode(q)}", timeout=30)
        if not rows:
            break
        bars = [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5]), ct=int(k[6])) for k in rows]
        out = bars + out
        end = bars[0]["t"] - 1
        time.sleep(0.3)
    now = int(time.time() * 1000)
    return [b for b in out if b["ct"] < now]


# ----------------------------- INDICADORES -----------------------------
def rma(x, p):
    out, a = [], None
    for v in x:
        a = v if a is None else (a * (p - 1) + v) / p
        out.append(a)
    return out


def ema(x, p):
    out, a, k = [], None, 2 / (p + 1)
    for v in x:
        a = v if a is None else a + k * (v - a)
        out.append(a)
    return out


def sma(x, p):
    out, s = [], 0.0
    for i, v in enumerate(x):
        s += v
        if i >= p:
            s -= x[i - p]
        out.append(s / min(i + 1, p))
    return out


def true_range(d):
    return [b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - d[i - 1]["c"]), abs(b["l"] - d[i - 1]["c"])) for i, b in enumerate(d)]


def supertrend(d, factor, period):
    atr = rma(true_range(d), period)
    st_, dir_ = [], []
    lo = up = st = None
    direction = 1
    for i, b in enumerate(d):
        src = (b["h"] + b["l"]) / 2
        u, dn = src - factor * atr[i], src + factor * atr[i]
        if i:
            pc = d[i - 1]["c"]
            u = u if (u > lo or pc < lo) else lo
            dn = dn if (dn < up or pc > up) else up
        if i == 0:
            direction = 1
        elif st == up:
            direction = -1 if b["c"] > dn else 1
        else:
            direction = 1 if b["c"] < u else -1
        lo, up = u, dn
        st = lo if direction == -1 else up
        st_.append(st)
        dir_.append(direction)
    return st_, dir_


def linreg_last(ys):
    n = len(ys)
    sx = n * (n - 1) / 2
    sxx = sum(j * j for j in range(n))
    sy = sum(ys)
    sxy = sum(j * y for j, y in enumerate(ys))
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    ic = (sy - slope * sx) / n
    return ic + slope * (n - 1)


def indicators(d):
    n = len(d)
    st, di = supertrend(d, ST_FACTOR, ST_ATR)
    tr = true_range(d)
    atr14 = rma(tr, 14)
    L = SQZ_LEN
    x = []
    for i in range(n):
        w = d[max(0, i - L + 1): i + 1]
        hh, ll = max(b["h"] for b in w), min(b["l"] for b in w)
        sm = sum(b["c"] for b in w) / len(w)
        x.append(d[i]["c"] - ((hh + ll) / 2 + sm) / 2)
    mom = [float("nan") if i < L - 1 else linreg_last(x[i - L + 1: i + 1]) for i in range(n)]
    pdm, mdm = [0.0], [0.0]
    for i in range(1, n):
        u, dn = d[i]["h"] - d[i - 1]["h"], d[i - 1]["l"] - d[i]["l"]
        pdm.append(u if (u > dn and u > 0) else 0.0)
        mdm.append(dn if (dn > u and dn > 0) else 0.0)
    trr, pr, mr = rma(tr, ADX_LEN), rma(pdm, ADX_LEN), rma(mdm, ADX_LEN)
    dx = []
    for i in range(n):
        a = 100 * pr[i] / trr[i] if trr[i] else 0
        b = 100 * mr[i] / trr[i] if trr[i] else 0
        dx.append(100 * abs(a - b) / (a + b) if (a + b) else 0)
    adx = rma(dx, ADX_LEN)
    ap = [(b["h"] + b["l"] + b["c"]) / 3 for b in d]
    esa = ema(ap, WT_N1)
    de = ema([abs(a - e) for a, e in zip(ap, esa)], WT_N1)
    ci = [(a - e) / (0.015 * dd) if dd else 0 for a, e, dd in zip(ap, esa, de)]
    wt1 = ema(ci, WT_N2)
    wt2 = sma(wt1, 4)
    e200 = ema([b["c"] for b in d], EMA_LEN)
    return dict(st=st, dir=di, atr=atr14, mom=mom, adx=adx, wt1=wt1, wt2=wt2, ema=e200)


# Filtro 4h: dirección de la Tendencial de 4h en su última vela CERRADA (igual que el indicador)
_h4_cache = {}


def dir_series(sym, d, htf="4h"):
    """Dirección de la Tendencial de una temporalidad mayor en su última vela CERRADA (1 alcista, -1 bajista)."""
    ms = TF_MS[htf]
    bucket = int(time.time() * 1000) // ms
    c = _h4_cache.get((sym, htf))
    if not c or c[0] != bucket:
        kk = fetch_klines(sym, htf, 1500)
        _, dh = supertrend(kk, ST_FACTOR, ST_ATR)
        _h4_cache[(sym, htf)] = (bucket, {b["t"]: (1 if dh[j] < 0 else -1) for j, b in enumerate(kk)})
    m = _h4_cache[(sym, htf)][1]
    return [m.get((b["t"] // ms - 1) * ms) for b in d]


def dir4_series(sym, d):
    return dir_series(sym, d, "4h")


# ----------------------------- SEÑALES + GESTIÓN (copia del motor del indicador) -----------------------------
def simulate(d, I, capital, risk_pct, dir4=None, dir15=None, low=False):
    """low=True: modo preciso de 3m/5m (backtest 8 monedas: 3m +0.25 → +0.54 R/op, 5m +0.25 → +0.46 R/op)."""
    W = LOW_W if low else DCA_W
    min_dist = LOW_MIN_DIST if low else MIN_DIST
    st, di, atr, mom, adx, wt1, wt2, e200 = (I[k] for k in ("st", "dir", "atr", "mom", "adx", "wt1", "wt2", "ema"))
    events = []
    last_zone, zone_side, fired = -10**9, 0, False
    last_xup = last_xdn = -10**9
    T = None
    last = dict(pts=0, c=(False, False, False))
    risk_usd = capital * risk_pct / 100
    for i in range(30, len(d)):
        b = d[i]
        up = di[i] < 0
        side = 1 if up else -1
        if di[i] != di[i - 1]:
            last_zone, fired = -10**9, False
        if wt1[i] > wt2[i] and wt1[i - 1] <= wt2[i - 1] and min(wt1[i], wt1[i - 1]) <= -WT_LVL:
            last_xup = i
        if wt1[i] < wt2[i] and wt1[i - 1] >= wt2[i - 1] and max(wt1[i], wt1[i - 1]) >= WT_LVL:
            last_xdn = i
        z = (b["l"] - st[i]) / atr[i] if up else (st[i] - b["h"]) / atr[i]
        if z <= ZONE_ATR and (b["c"] > st[i] if up else b["c"] < st[i]):
            if i - last_zone > TOUCH_WIN:
                fired = False
            last_zone, zone_side = i, side
        c1 = i - last_zone <= TOUCH_WIN and zone_side == side
        c2 = (mom[i] > mom[i - 1] if up else mom[i] < mom[i - 1]) and adx[i] < adx[i - 1]
        c3 = (i - last_xup <= WT_WIN) if up else (i - last_xdn <= WT_WIN)
        score = 1 + int(c2) + int(c3)
        ok_ema = (not USE_EMA) or (b["c"] > e200[i] if up else b["c"] < e200[i])
        ok_h4 = (dir4 is None or dir4[i] == side) and (dir15 is None or dir15[i] == side)
        sig = c1 and not fired and score >= MIN_TRADE and ok_ema and ok_h4
        if sig:
            fired = True
        new_side = side if sig else 0
        last = dict(pts=score if c1 else 0, c=(c1, c2, c3))

        # gestión de la operación abierta
        close_now, exit_px, reason, exit_fee = False, None, "", FEE_TAKER
        if T and i > T["bar"]:
            s = T["side"]
            if T["fills"] == 1 and (b["l"] <= T["e2"] if s == 1 else b["h"] >= T["e2"]):
                T["avg"] = (T["avg"] * T["fw"] + T["e2"] * W[1]) / (T["fw"] + W[1]); T["fw"] += W[1]; T["fills"] = 2
            if T["fills"] == 2 and (b["l"] <= T["e3"] if s == 1 else b["h"] >= T["e3"]):
                T["avg"] = (T["avg"] * T["fw"] + T["e3"] * W[2]) / (T["fw"] + W[2]); T["fw"] += W[2]; T["fills"] = 3
            if (b["l"] <= T["sl"]) if s == 1 else (b["h"] >= T["sl"]):
                close_now, exit_px, reason = True, T["sl"], "stop"
            else:
                for k in range(T["tp"], 3):
                    px = T["tps"][k]
                    if (b["h"] >= px) if s == 1 else (b["l"] <= px):
                        if k < 2:
                            T["real"] += TP_SPLIT[k] * s * (px - T["avg"]) / T["avg"]
                            T["feeTP"] += TP_SPLIT[k] * FEE_MAKER
                            T["rem"] -= TP_SPLIT[k]
                            T["tp"] = k + 1
                            if k == 0:
                                events.append(dict(kind="TP1", bar=i, side=s, price=px, q=T["q"]))
                        else:
                            close_now, exit_px, reason, exit_fee = True, px, "tp3", FEE_MAKER
                            T["tp"] = 3
                    else:
                        break
        if T and new_side == -T["side"] and not close_now:
            close_now, exit_px, reason = True, b["c"], "reverse"
        if close_now and T:
            fee_in = W[0] * FEE_TAKER + (W[1] * FEE_MAKER if T["fills"] >= 2 else 0.0) + (W[2] * FEE_MAKER if T["fills"] >= 3 else 0.0)
            fee_out = T["fw"] * (T["feeTP"] + T["rem"] * exit_fee)
            T["real"] += T["rem"] * T["side"] * (exit_px - T["avg"]) / T["avg"]
            pnl_usd = (T["real"] * T["fw"] - fee_in - fee_out) * T["notional"]
            events.append(dict(kind="CLOSE", bar=i, entry=T["bar"], side=T["side"], price=exit_px, q=T["q"], win=T["tp"] >= 1, reason=reason, pnl=pnl_usd, r=pnl_usd / risk_usd))
            T = None
        if new_side and not T:
            s, e1 = new_side, b["c"]
            e2 = st[i]
            e3 = st[i] - s * DCA_ATR * atr[i]
            sl = e3 - s * SL_ATR * atr[i]
            avg_all = e1 * W[0] + e2 * W[1] + e3 * W[2]
            worst = max(abs(avg_all - sl) / avg_all, 1e-4)
            if worst * 100 < min_dist:
                continue  # stop demasiado corto: la comisión se come la ganancia
            lev = rec_leverage(worst)
            R = abs(e1 - sl)
            tps = [e1 + s * R * r for r in TP_R]
            notional = risk_usd / worst
            qty = [notional * w / e for w, e in zip(W, (e1, e2, e3))]
            margin = [notional * w / lev for w in W]
            T = dict(side=s, q=score, bar=i, e1=e1, e2=e2, e3=e3, sl=sl, tps=tps, avg=e1, fw=W[0], fills=1, tp=0,
                     real=0.0, rem=1.0, feeTP=0.0, notional=notional, lev=lev, qty=qty, margin=margin)
            events.append(dict(kind="OPEN", bar=i, side=s, price=e1, q=score, sl=sl, tps=tps, e2=e2, e3=e3, lev=lev,
                               c=(c1, c2, c3), qty=qty, margin=margin, risk=risk_usd))
    return events, T, last


# ----------------------------- TELEGRAM -----------------------------
def tg(token, method, params=None, timeout=20):
    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def send(token, chat_id, text, buttons=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if buttons:  # botones bajo el mensaje: [[(texto, url), ...], ...]
        p["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "url": u} for t, u in row] for row in buttons]})
    return tg(token, "sendMessage", p)


def chart_url(sym, tf):
    tv = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240"}.get(tf, "15")
    ex = "" if sym in _bitunix_syms else "BINANCE%3A"
    return f"https://www.tradingview.com/chart/?symbol={ex}{sym}.P&interval={tv}"


def signal_buttons(sym, tf):
    return [[("📈 Ver gráfico " + tf.upper(), chart_url(sym, tf))]]


def fmt(p):
    if p >= 100:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}"
    if p >= 0.01:
        return f"{p:.5f}"
    return f"{p:.8f}".rstrip("0")


def fq(v):  # cantidad de monedas (igual que el panel del indicador)
    s = f"{v:.0f}" if v >= 100 else f"{v:.2f}" if v >= 1 else f"{v:.4f}" if v >= 0.01 else f"{v:.6f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def coin(sym):
    for q in ("USDT", "USDC", "BUSD"):
        if sym.endswith(q):
            return sym[: -len(q)]
    return sym


def money(p):
    return "$" + fmt(p)


def wr_line(h):
    """Winrate histórico de esa moneda/temporalidad (TP1 alcanzado = ganada, como el panel del indicador)."""
    n = h["n"] if h else 0
    if n < 5:
        return "⚪ Winrate: — (sin historial)"
    w = h["wr"]
    dot = "🟢" if w >= 60 else "🟡" if w >= 50 else "🔴"
    return f"{dot} Winrate: {w:.0f}%"


def message(sym, tf, ev, extra=None):
    """Mensajes con el mismo formato que el Radar de Aurolo."""
    extra = extra or {}
    s = "LONG" if ev["side"] == 1 else "SHORT"
    icon = "🟢" if ev["side"] == 1 else "🔴"
    pair, TF = sym + ".P", tf.upper()
    link = f'<a href="{chart_url(sym, tf)}">{pair} {TF}</a>'
    if ev["kind"] == "OPEN":
        e1 = ev["price"]
        pct = lambda x: f"{(x - e1) / e1 * 100:+.2f}%"
        q1, q2, q3 = (fq(x) for x in ev["qty"])
        lines = [f"{icon} <b>{s}</b> {link} {icon}", "",
                 "📡 <b>OBJETIVO DETECTADO:</b>",
                 f"├── 💰 Entrada: {money(e1)}",
                 f"├── 💰 DCA: E2: {money(ev['e2'])} · E3: {money(ev['e3'])}",
                 f"├── 🛟 Stop Loss: {money(ev['sl'])} ({pct(ev['sl'])})",
                 f"├── ⚡ Apalancamiento recomendado: <b>{ev['lev']}x</b>",
                 f"└── {wr_line(extra.get('hist'))}", "",
                 "🎯 <b>TAKE PROFITS:</b>",
                 f"├── TP1: {money(ev['tps'][0])} ({pct(ev['tps'][0])})",
                 f"├── TP2: {money(ev['tps'][1])} ({pct(ev['tps'][1])})"]
        tgt = extra.get("htf_tp")
        if tgt:
            lines.append(f"└── 1er TP ({tgt[0]}): {money(tgt[1])} ({pct(tgt[1])})")
        else:
            lines.append(f"└── TP3: {money(ev['tps'][2])} ({pct(ev['tps'][2])})")
        lines += ["", f"📦 <b>CANTIDAD</b> (riesgo ${ev['risk']:,.0f}):",
                  f"└── E1: {q1} · E2: {q2} · E3: {q3} {coin(sym)}", "",
                  "⚓ <b>ESTRATEGIA:</b>",
                  "└── ⏰ Salir cuando indique señal de tiempo", "",
                  "📻 <i>¿Disparas?</i>",
                  "⚓ Opera en Bitunix · E2, E3 y TP con orden límite"]
        return "\n".join(lines)
    if ev["kind"] == "TP1":
        return "\n".join([f"✅ <b>TP1 ALCANZADO</b> ✅", "", f"📊 Par: {pair}", f"⏰ Temporalidad: {TF}", f"{icon} Tipo: {s}",
                          f"🎯 Precio: {money(ev['price'])}", "", "💡 <i>Cierra una parte y deja correr el resto</i>"])
    res = {"tp3": "🏁 TP3 alcanzado", "stop": "⛔ Stop loss" if not ev["win"] else "🏁 Resto cerrado en SL tras TP",
           "reverse": "🔄 Señal contraria"}[ev["reason"]]
    lines = ["⏰ <b>OPERACIÓN CERRADA</b> ⏰", "", f"📊 Par: {pair}", f"⏰ Temporalidad: {TF}", f"🔴 Tipo: {s}",
             f"{'💰' if ev['pnl'] >= 0 else '🩸'} Resultado: {res} · {'+' if ev['pnl'] >= 0 else '-'}${abs(ev['pnl']):,.2f} ({ev['r']:+.2f} R)",
             "", "⚠️ <i>Esta operación ya cerró. No es posible entrar.</i>", "<i>Espera la próxima señal</i> 🚢"]
    return "\n".join(lines)


# ----------------------------- CONFIGURACIÓN (asistente de la primera vez) -----------------------------
def setup_wizard(token=""):
    print("=" * 64)
    print("  Bridge Táctico · conectar Telegram (solo la primera vez)")
    print("=" * 64)
    print("1) En Telegram abre @BotFather, envía /newbot y sigue los pasos.")
    print("2) Copia el token que te da (algo como 123456789:AAH...).")
    me = None
    while not me:
        if not token:
            token = input("\nPega aquí el token (clic derecho = pegar) y pulsa Enter: ").strip().strip('"').strip()
        try:
            r = tg(token, "getMe")
            me = r.get("result") if r.get("ok") else None
        except Exception:
            me = None
        if not me:
            print("✗ Ese token no funciona. Revisa que esté completo y vuelve a pegarlo.")
            token = ""
    user = me.get("username", "")
    print(f"\n✓ Bot @{user} conectado.")
    print(f"3) Abre en Telegram  https://t.me/{user}  y pulsa INICIAR (o envía /start).")
    print("   Esperando tu mensaje (hasta 10 minutos)...")
    offset, deadline = 0, time.time() + 600
    while time.time() < deadline:
        try:
            upd = tg(token, "getUpdates", {"offset": offset, "timeout": 25}, timeout=40)
        except Exception:
            time.sleep(3)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            ch = (u.get("message") or {}).get("chat", {})
            if ch.get("type") != "private" or not ch.get("id"):
                continue
            who = (ch.get("first_name", "") + " " + ch.get("last_name", "")).strip() or ch.get("username", "") or str(ch["id"])
            ans = input(f"\nMensaje recibido de «{who}». ¿Eres tú? [S/n]: ").strip().lower()
            if ans not in ("", "s", "si", "sí", "y", "yes"):
                print("   Vale, sigo esperando...")
                continue
            chat = str(ch["id"])
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"token": token, "chat_id": chat}, f)
            send(token, chat, "✅ <b>Bridge Táctico conectado</b>\nDesde ahora te aviso aquí de las señales de tus favoritos.")
            print("\n✓ Listo. Guardado en config.json (no compartas ese archivo: contiene tu token).\n")
            return token, chat, offset
    sys.exit("No llegó ningún mensaje en 10 minutos. Cierra y vuelve a abrir iniciar_bot.bat.")


def load_credentials(interactive):
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token in PLACEHOLDERS:
        token = cfg.get("token", "")
    if chat in PLACEHOLDERS:
        chat = str(cfg.get("chat_id", ""))
    if token and chat:
        return token, chat, 0
    if not interactive:
        sys.exit("Faltan TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID")
    return setup_wizard(token)


# ----------------------------- ESTADO -----------------------------
def load_state():
    st = {"sent": [], "init": False}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            st.update(json.load(f))
    st.setdefault("symbols", [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()])
    st.setdefault("tfs", [t.strip() for t in os.environ.get("TIMEFRAMES", "1m,3m,5m,15m,1h").split(",") if t.strip()])
    st.setdefault("capital", float(os.environ.get("CAPITAL", "2000")))
    st.setdefault("risk", float(os.environ.get("RISK_PCT", "2")))
    st.setdefault("h4", True)
    st.setdefault("radar_every", 0)
    st.setdefault("top", int(os.environ.get("TOP", "30")))
    if not st.get("v32"):  # v3.2: modo preciso de 3m/5m activado y 1m sin avisos (no es rentable)
        st["h4"], st["mute1m"], st["v32"] = True, True, True
        st["hist_seeded"] = [p for p in st.get("hist_seeded", []) if not p.endswith(("_1m", "_3m", "_5m"))]
        for p in list(st.get("hist", {})):
            if p.endswith(("_1m", "_3m", "_5m")):
                del st["hist"][p]
    if not st.get("v33"):  # v3.3: escanea las 30 monedas con más volumen, avisos de 1m activos, radar solo con /radar
        st["top"], st["mute1m"], st["radar_every"], st["v33"] = int(os.environ.get("TOP", "30")), False, 0, True
    st.setdefault("offset", 0)
    st.setdefault("seen_pairs", [])
    fav = os.path.join(BASE, "favoritos.txt")  # monedas extra para la PC: una lista separada por comas o líneas
    if os.environ.get("CLOUD") != "1" and os.path.exists(fav):
        with open(fav, encoding="utf-8") as f:
            for x in re.split(r"[,\s]+", f.read()):
                x = norm_symbol(x) if x.strip() and not x.startswith("#") else ""
                if x and x not in st["symbols"]:
                    st["symbols"].append(x)
    if os.environ.get("CLOUD") == "1":  # en GitHub las monedas se cambian en Settings → Variables → SYMBOLS
        st["top"] = int(os.environ.get("TOP", "30"))
        st["symbols"] = [norm_symbol(s) for s in re.split(r"[,\s]+", os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT")) if s.strip()]
        st["tfs"] = [t.strip() for t in os.environ.get("TIMEFRAMES", "15m,1h").split(",") if t.strip()]
        st["v31"] = True
    if not st.get("v31"):  # v3.1: también vigila 1h
        if "1h" not in st["tfs"]:
            st["tfs"].append("1h")
        st["v31"] = True
    return st


def save_state(st, now):
    cutoff = now - 7 * 86_400_000
    st["sent"] = sorted(k for k in set(st["sent"]) if int(k.split("_")[-2]) >= cutoff)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def norm_symbol(x):
    x = x.strip().upper().replace("/", "").replace("-", "").replace(".P", "")
    if x and not x.endswith(("USDT", "USDC", "BUSD")):
        x += "USDT"
    return x


def parse_num(args, amount=False):
    if not args:
        return None
    s = args[0].replace("$", "").replace("%", "").strip()
    if amount and re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):  # 2.000 o 2,000 = dos mil
        s = s.replace(".", "").replace(",", "")
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def settings_line(st):
    return (f"💰 ${st['capital']:,.0f} · riesgo {st['risk']:g}% = ${st['capital'] * st['risk'] / 100:,.2f} por operación\n"
            f"🛡 SL mínimo {MIN_DIST:g}% · 🎯 modo preciso 3m/5m: {'sí' if st['h4'] else 'no'} · avisos 1m: {'no' if st.get('mute1m', False) else 'sí'} · monedas: favoritas + top {st.get('top', 0)}")


# ----------------------------- COMANDOS DE TELEGRAM -----------------------------
HELP = ("🤖 <b>Bridge Táctico · comandos</b>\n"
        "/radar → todas tus monedas y temporalidades de un vistazo\n"
        "/radar cada 30 · /radar off → radar automático (por defecto cada 60 min)\n"
        "/estado → detalle: tendencia y operaciones activas\n"
        "/winrate → acierto de cada moneda en cada temporalidad\n"
        "/top 30 → además de tus favoritas, vigila las 30 monedas con más volumen (0 = solo favoritas)\n"
        "/agregar SOL XRP → añade a favoritos\n"
        "/quitar SOL → quita de favoritos\n"
        "/lista → favoritos y ajustes\n"
        "/tf 1m 3m 5m 15m → temporalidades a vigilar\n"
        "/capital 2000 → tu capital en $ (para calcular cantidades)\n"
        "/riesgo 2 → % de riesgo por operación\n"
        "/h4 si | no → modo preciso 3m/5m: solo a favor de 15m y 4h (activado)\n"
        "/1m si | no → avisos de 1m (apagados: pierden por comisiones)\n"
        "/nube si | no → 15m y 1h los avisa GitHub (evita avisos repetidos)\n"
        "/ayuda → este mensaje")


def handle_commands(token, chat, st, ctx):
    try:
        q = urllib.parse.urlencode({"offset": st["offset"], "timeout": 0})
        upd = http_get(f"https://api.telegram.org/bot{token}/getUpdates?{q}")
    except Exception:
        return
    for u in upd.get("result", []):
        st["offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("edited_message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat):
            continue  # solo obedece a tu chat
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        parts = text.split()
        cmd, args = parts[0].split("@")[0].lower(), parts[1:]
        if cmd in ("/start", "/ayuda", "/help"):
            send(token, chat, HELP)
        elif cmd in ("/agregar", "/add"):
            added = []
            for a in args:
                s = norm_symbol(a)
                try:
                    fetch_klines(s, "1m", 5)
                except Exception:
                    send(token, chat, f"⚠️ {s} no existe en Binance ni en Bitunix")
                    continue
                if s not in st["symbols"]:
                    st["symbols"].append(s)
                    added.append(s)
            send(token, chat, "⭐ Agregado: " + (", ".join(added) if added else "nada nuevo") + "\nFavoritos: " + ", ".join(st["symbols"]))
        elif cmd in ("/quitar", "/remove"):
            for a in args:
                s = norm_symbol(a)
                if s in st["symbols"]:
                    st["symbols"].remove(s)
            send(token, chat, "Favoritos: " + (", ".join(st["symbols"]) or "(vacío)"))
        elif cmd == "/tf":
            tfs = [a.lower() for a in args if a.lower() in TF_MS]
            if tfs:
                st["tfs"] = tfs
            send(token, chat, "⏱ Temporalidades: " + ", ".join(st["tfs"]))
        elif cmd == "/capital":
            v = parse_num(args, amount=True)
            if v and v > 0:
                st["capital"] = v
            send(token, chat, settings_line(st))
        elif cmd == "/riesgo":
            v = parse_num(args)
            if v and 0 < v <= 10:
                st["risk"] = v
            send(token, chat, settings_line(st))
        elif cmd == "/h4":
            if args:
                st["h4"] = args[0].lower() in ("si", "sí", "on", "1", "yes", "y", "s")
            send(token, chat, "🎯 Modo preciso 3m/5m: " + ("<b>ACTIVADO</b> (solo a favor de la Tendencial de 15m y 4h · DCA 10/30/60 · SL mín 0.45 %)" if st["h4"] else "desactivado"))
        elif cmd == "/1m":
            if args:
                st["mute1m"] = args[0].lower() not in ("si", "sí", "on", "1", "s")
            send(token, chat, "⏱ Avisos de 1m: " + ("desactivados (en el backtest pierden por comisiones)" if st.get("mute1m", False) else "<b>activados</b> ⚠️ en el backtest 1m pierde por comisiones: fíjate en su winrate"))
        elif cmd == "/lista":
            send(token, chat, "⭐ Favoritos: " + ", ".join(st["symbols"]) + "\n⏱ Temporalidades: " + ", ".join(st["tfs"]) + "\n" + settings_line(st))
        elif cmd == "/estado":
            send(token, chat, status_report(st, ctx))
        elif cmd == "/radar":
            if args and args[0].lower() in ("off", "no", "apagar"):
                st["radar_every"] = 0
                send(token, chat, "📡 Radar automático: apagado (escribe /radar cuando lo quieras)")
            elif args:
                v = parse_num(args[1:] if args[0].lower() == "cada" else args)
                if v and v >= 5:
                    st["radar_every"] = int(v)
                send(token, chat, f"📡 Radar automático cada {st.get('radar_every', 60)} min")
            else:
                send(token, chat, radar_report(st, ctx))
        elif cmd == "/winrate":
            send(token, chat, winrate_report(st))
        elif cmd == "/top":
            v = parse_num(args)
            if v is not None and 0 <= v <= 60:
                st["top"] = int(v)
            send(token, chat, f"🔎 Escaneo: tus favoritas + las {st.get('top', 0)} monedas con más volumen ({len(active_symbols(st))} en total)")
        elif cmd == "/nube":
            if args:
                st["quiet"] = ["15m", "1h"] if args[0].lower() in ("si", "sí", "on", "1", "s") else []
            send(token, chat, "☁️ 15m y 1h: " + ("los avisa GitHub (la PC solo 1m-5m, sin repetidos)" if st.get("quiet") else "los avisa esta PC"))


def status_report(st, ctx):
    lines = ["📊 <b>Estado</b>"]
    for sym in st["symbols"]:
        row = []
        for tf in st["tfs"]:
            info = ctx.get((sym, tf))
            if not info:
                row.append(f"{tf} …")
                continue
            trend = "▲ alcista" if info["up"] else "▼ bajista"
            T = info["T"]
            if T:
                op = f" → <b>{'LONG' if T['side'] == 1 else 'SHORT'} {T['q']}/3 activo</b> (E1 {fmt(T['e1'])} · SL {fmt(T['sl'])})"
            elif info.get("pts", 0) >= 1:
                op = " · tocando la Tendencial"
            else:
                op = ""
            row.append(f"{tf} {trend}{op}")
        lines.append(f"<b>{sym}</b>\n  " + "\n  ".join(row))
    lines.append(settings_line(st))
    return "\n".join(lines)


def winrate_report(st):
    lines = ["🏆 <b>Winrate por temporalidad</b> (TP1 alcanzado)"]
    for sym in [x for x in active_symbols(st) if any(f"{x}_{t}" in st.get("hist", {}) for t in st["tfs"])]:
        cells = []
        for tf in st["tfs"]:
            h = hist_stats(st, f"{sym}_{tf}")
            if h["n"] < 5:
                cells.append(f"{tf.upper()} ⚪ —")
            else:
                dot = "🟢" if h["wr"] >= 60 else "🟡" if h["wr"] >= 50 else "🔴"
                cells.append(f"{tf.upper()} {dot} {h['wr']:.0f}% ({h['n']})")
        lines.append(f"<b>{coin(sym)}</b>\n  " + "\n  ".join(cells))
    lines.append("\n🟢 ≥60 % · 🟡 50-59 % · 🔴 <50 % · ⚪ menos de 5 operaciones")
    return "\n".join(lines)


def radar_report(st, ctx):
    lines = ["📡 <b>Radar de favoritos</b>"]
    for sym in st["symbols"]:
        cells, ups, n = [], 0, 0
        for tf in st["tfs"]:
            info = ctx.get((sym, tf))
            if not info:
                cells.append(f"{tf} …")
                continue
            n += 1
            ups += 1 if info["up"] else 0
            T = info["T"]
            if T:
                mark = ("🟢" if T["side"] == 1 else "🔴") + f"{T['q']}/3"
            else:
                mark = ("▲" if info["up"] else "▼") + ("◦" if info.get("pts", 0) >= 1 else "")
            cells.append(f"{tf} {mark}")
        bias = f" · {round(ups / n * 100)}% ▲" if n else ""
        lines.append(f"<b>{coin(sym)}</b>{bias}\n  " + "  ".join(cells))
    lines.append("\n▲▼ tendencia · ◦ tocando la Tendencial · 🟢/🔴 LONG/SHORT activo (calidad)")
    return "\n".join(lines)


# ----------------------------- PROCESO -----------------------------
def hist_stats(st, pair):
    h = st.get("hist", {}).get(pair, {})
    n = len(h)
    if not n:
        return {"n": 0, "wr": 0.0, "avg": 0.0}
    return {"n": n, "wr": 100.0 * sum(v[0] for v in h.values()) / n, "avg": sum(v[1] for v in h.values()) / n}


# Tendencial de cualquier temporalidad (última vela cerrada), con caché por vela
_tf_cache = {}


def tf_state(sym, tf):
    bucket = int(time.time() * 1000) // TF_MS[tf]
    c = _tf_cache.get((sym, tf))
    if not c or c[0] != bucket:
        k = fetch_klines(sym, tf, 300)
        stl, di = supertrend(k, ST_FACTOR, ST_ATR)
        _tf_cache[(sym, tf)] = c = (bucket, di[-1] < 0, stl[-1])
    return c[1], c[2]


def open_extras(sym, tf, ev, ctx):
    dirs, cands = [], []
    for t in ("1m", "3m", "5m", "15m", "1h", "4h"):
        try:
            if (sym, t) in ctx:
                up, lvl = ctx[(sym, t)]["up"], ctx[(sym, t)]["st"]
            else:
                up, lvl = tf_state(sym, t)
        except Exception:
            continue
        dirs.append((t, up))
        if TF_MS[t] > TF_MS[tf]:  # TP Tendencial: la Tendencial contraria de una temporalidad mayor
            if (ev["side"] == 1 and not up and lvl > ev["price"]) or (ev["side"] == -1 and up and lvl < ev["price"]):
                cands.append((abs(lvl - ev["price"]), t, lvl))
    tgt = min(cands)[1:] if cands else None
    return {"dirs": dirs, "htf_tp": tgt}


# ----------------------------- MONEDAS: favoritas + las de más volumen (como el radar de Aurolo) -----------------------------
_top_cache = {"t": 0, "list": []}
EXCLUDE = {"USDCUSDT", "BTCDOMUSDT", "DEFIUSDT", "FDUSDUSDT", "TUSDUSDT"}


def top_symbols(n):
    if n <= 0:
        return []
    if time.time() - _top_cache["t"] > 3600 or len(_top_cache["list"]) < n:
        for url in ("https://fapi.binance.com/fapi/v1/ticker/24hr", "https://data-api.binance.vision/api/v3/ticker/24hr"):
            try:
                rows = http_get(url, timeout=30)
                rows = [r for r in rows if r["symbol"].endswith("USDT") and r["symbol"] not in EXCLUDE and float(r.get("quoteVolume", 0)) > 0]
                rows.sort(key=lambda r: float(r["quoteVolume"]), reverse=True)
                _top_cache.update(t=time.time(), list=[r["symbol"] for r in rows[:60]])
                break
            except Exception as e:
                print("top:", e)
    return _top_cache["list"][:n]


def active_symbols(st):
    out = list(st["symbols"])
    for x in top_symbols(int(st.get("top", 0))):
        if x not in out:
            out.append(x)
    return out


# ----------------------------- ESCANEO (en paralelo) -----------------------------
def compute(sym, tf, st):
    """Descarga y calcula una moneda/temporalidad. No toca el estado (se puede ejecutar en paralelo)."""
    pair = f"{sym}_{tf}"
    d = fetch_klines(sym, tf, 1000)
    I = indicators(d)
    low = st.get("h4", True) and TF_MS.get(tf, 0) <= 300_000  # modo preciso en 1m/3m/5m
    dir4 = dir_series(sym, d, "4h") if low else None
    dir15 = dir_series(sym, d, "15m") if low else None
    events, T, last = simulate(d, I, st["capital"], st["risk"], dir4, dir15, low)
    seed = None
    if pair not in st.get("hist_seeded", []):  # la primera vez: winrate con un historial largo
        try:
            dl = fetch_long(sym, tf, 3000)
            evl, _, _ = simulate(dl, indicators(dl), st["capital"], st["risk"], dir_series(sym, dl, "4h") if low else None,
                                 dir_series(sym, dl, "15m") if low else None, low)
            seed = [(str(dl[e["entry"]]["t"]), 1 if e["win"] else 0, round(e["r"], 3)) for e in evl if e["kind"] == "CLOSE" and e["entry"] >= 250]
        except Exception as e:
            print(f"⚠️ historial {sym} {tf}: {e}")
    return dict(sym=sym, tf=tf, d=d, up=I["dir"][-1] < 0, st=I["st"][-1], events=events, T=T, pts=last["pts"], seed=seed)


def apply(res, st, ctx, token, chat, notify=True):
    """Guarda el resultado y envía a Telegram cada señal por separado (una por moneda y temporalidad)."""
    sym, tf, d = res["sym"], res["tf"], res["d"]
    pair = f"{sym}_{tf}"
    ctx[(sym, tf)] = {"up": res["up"], "st": res["st"], "T": res["T"], "pts": res["pts"]}
    H = st.setdefault("hist", {}).setdefault(pair, {})
    if res["seed"] is not None:
        for k, w, r in res["seed"]:
            H.setdefault(k, [w, r])
        st.setdefault("hist_seeded", []).append(pair)
    for ev in res["events"]:
        if ev["kind"] == "CLOSE" and ev["entry"] >= 250:
            H.setdefault(str(d[ev["entry"]]["t"]), [1 if ev["win"] else 0, round(ev["r"], 3)])
    if len(H) > 150:
        for k in sorted(H, key=int)[:-150]:
            del H[k]
    now = int(time.time() * 1000)
    first_time = pair not in st["seen_pairs"]
    sent = set(st["sent"])
    muted = set(st.get("quiet", [])) | ({"1m"} if st.get("mute1m", False) else set())
    for ev in res["events"]:
        key = f"{sym}_{tf}_{d[ev['bar']]['t']}_{ev['kind']}"
        if key in sent:
            continue
        recent = now - d[ev["bar"]]["ct"] <= 2 * TF_MS.get(tf, 900_000) + 120_000
        if notify and recent and not first_time and tf not in muted:
            extra = {"hist": hist_stats(st, pair)}
            if ev["kind"] == "OPEN":
                extra.update(open_extras(sym, tf, ev, ctx))
            try:
                send(token, chat, message(sym, tf, ev, extra), signal_buttons(sym, tf) if ev["kind"] == "OPEN" else None)
                print(time.strftime("%H:%M:%S"), "enviado", ev["kind"], sym, tf)
            except Exception as e:
                print("telegram:", e)
                continue  # se reintenta en la próxima vuelta
        sent.add(key)
    st["sent"] = list(sent)
    if first_time:
        st["seen_pairs"].append(pair)


def scan(pairs, st, ctx, token, chat):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [(sym, tf, ex.submit(compute, sym, tf, st)) for sym, tf in pairs]
        for sym, tf, f in futs:
            try:
                apply(f.result(), st, ctx, token, chat)
            except Exception as e:
                print(f"⚠️ {sym} {tf}: {e}")


def run_once(token, chat, st, ctx):
    scan([(s, t) for s in active_symbols(st) for t in st["tfs"]], st, ctx, token, chat)


def main():
    loop = "--loop" in sys.argv
    interactive = bool(sys.stdin) and sys.stdin.isatty() and not os.environ.get("CI")
    token, chat, offset = load_credentials(interactive)
    st, ctx = load_state(), {}
    st["offset"] = max(st.get("offset", 0), offset)

    if not loop:  # una pasada (GitHub Actions): primero revisa el mercado para que /radar y /estado tengan datos
        run_once(token, chat, st, ctx)
        if os.environ.get("COMMANDS", "1") == "1":  # en la nube se apaga para no robarle los comandos al bot de la PC
            handle_commands(token, chat, st, ctx)
        if not st["init"] or os.environ.get("TEST") == "1":
            send(token, chat, ("🤖 <b>Bridge Táctico (nube) activo</b>" if not st["init"] else "🧪 <b>Prueba nube OK</b>") +
                 f"\nVigilando {len(active_symbols(st))} monedas en {', '.join(t.upper() for t in st['tfs'])}. Cada señal llega en su propio mensaje.")
        st["init"] = True
        save_state(st, int(time.time() * 1000))
        return

    print("Bridge Táctico bot en marcha (cierra la ventana o Ctrl+C para detener)…")
    run_once(token, chat, st, ctx)
    send(token, chat, f"🤖 <b>Bridge Táctico activo</b>\nVigilando {len(active_symbols(st))} monedas en {', '.join(t.upper() for t in st['tfs'])}.\n"
                      "Cada señal llega en su propio mensaje.\n\n" + settings_line(st) + "\n\nEscribe /ayuda para ver los comandos.")
    st["init"] = True
    save_state(st, int(time.time() * 1000))
    last_min = None
    while True:
        try:
            handle_commands(token, chat, st, ctx)
            now = time.time()
            minute = int(now // 60)
            if minute != last_min and now % 60 >= 3:  # 3 s después del cierre de cada vela de 1m
                last_min = minute
                syms = active_symbols(st)
                pairs = [(sym, tf) for sym in syms for tf in st["tfs"] if minute % (TF_MS[tf] // 60_000) == 0 or (sym, tf) not in ctx]
                scan(pairs, st, ctx, token, chat)
                every = st.get("radar_every", 0)
                if every and minute % every == 0:  # radar automático a Telegram
                    try:
                        send(token, chat, radar_report(st, ctx))
                    except Exception as e:
                        print("radar:", e)
                save_state(st, int(now * 1000))
                print(time.strftime("%H:%M:%S"), f"revisado: {len(syms)} monedas", "|", ", ".join(st["tfs"]))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("error:", e)
        time.sleep(2)


if __name__ == "__main__":
    main()
