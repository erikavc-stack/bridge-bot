#!/usr/bin/env python3
"""
Bridge Táctico → Telegram  ·  v4.1 (misma lógica que el indicador Bridge Táctico v8)
Vigila tus monedas en 1m · 3m · 5m · 15m · 1h · 4h y avisa por Telegram, cada señal en su propio mensaje:
  señal LONG/SHORT con entrada (o órdenes límite), DCA, SL, TP, apalancamiento y cantidad,
  orden llenada, TP1, orden cancelada y cierre con el resultado en $.

Primera vez: doble clic en iniciar_bot.bat → te pide el token de @BotFather y detecta tu chat
cuando le envías /start a tu bot. Se guarda en config.json (no compartas ese archivo).

Varios usuarios: comparte el enlace del bot; cuando alguien pulsa INICIAR te llega una solicitud
con botones Aprobar / Rechazar. Cada usuario elige sus temporalidades con /config y su capital con /capital.

Modos:
  python bot.py --loop   → corre sin parar en tu PC (señales al instante, todas las temporalidades)
  python bot.py          → una sola pasada (GitHub Actions: respaldo 15m/1h/4h cuando la PC está apagada)
"""
import json, os, re, sys, time, threading
import urllib.request, urllib.parse, urllib.error

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# ----------------------------- PARÁMETROS (iguales al indicador v8) -----------------------------
ST_FACTOR, ST_ATR = 4.5, 4
ZONE_ATR, TOUCH_WIN = 1.25, 6
SQZ_LEN, ADX_LEN = 20, 14
WT_N1, WT_N2, WT_LVL, WT_WIN = 10, 21, 40.0, 3
MIN_TRADE, USE_EMA, EMA_LEN = 2, True, 200
DCA_W = (0.20, 0.30, 0.50)        # E1 20 % · E2 30 % · E3 50 %  (15m y más)
LOW_W = (0.10, 0.30, 0.60)        # modo preciso 1m/3m: E1 10 % · E2 30 % · E3 60 %
LOW_MIN_DIST = 0.45               # modo preciso 1m/3m: SL mínimo 0.45 %
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
_bitunix_syms = set()   # monedas cuyos precios vienen de Bitunix (para el enlace del gráfico)
_bad_src = set()        # (fuente, moneda) que Binance dijo que NO existe → no volver a preguntar
PLACEHOLDERS = ("", "PEGA_AQUI_TU_TOKEN", "PEGA_AQUI_TU_CHAT_ID")


# ----------------------------- DATOS -----------------------------
_cool_until, _cool_lock = {}, threading.Lock()   # fuente -> hasta cuándo no usarla (límite de Binance)


def _host(url):
    return urllib.parse.urlsplit(url).netloc


def _cool(url, secs):
    with _cool_lock:
        h = _host(url)
        _cool_until[h] = max(_cool_until.get(h, 0), time.time() + secs)


def usable_sources(symbol=None):
    now = time.time()
    ok = [b for b in SOURCES if (_host(b), symbol) not in _bad_src]
    return [b for b in ok if _cool_until.get(_host(b), 0) <= now] or ok[-1:]


def _mark_bad(base, symbol, e):
    """Binance responde 400 cuando la moneda no existe en esa fuente: se recuerda para no perder tiempo."""
    if isinstance(e, urllib.error.HTTPError) and e.code == 400:
        _bad_src.add((_host(base), symbol))


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
    if got:
        _bitunix_syms.add(symbol)
    return [got[t] for t in sorted(got) if got[t]["ct"] < now][-total:]


def fetch_klines(symbol, tf, limit=600):
    q = urllib.parse.urlencode({"symbol": symbol, "interval": tf, "limit": limit})
    last = None
    for base in usable_sources(symbol):
        try:
            rows = http_get(f"{base}?{q}")
            bars = [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5]), ct=int(k[6])) for k in rows]
            now = int(time.time() * 1000)
            return [b for b in bars if b["ct"] < now]  # solo velas cerradas
        except Exception as e:  # probar la siguiente fuente
            _mark_bad(base, symbol, e)
            last = e
    try:
        return fetch_bitunix(symbol, tf, limit)
    except Exception as e:
        last = e
    raise RuntimeError(f"No se pudo descargar {symbol} {tf}: {last}")


def fetch_long(symbol, tf, total=4500):
    """Historial largo (varias peticiones) para calcular el winrate la primera vez."""
    last = None
    for base in usable_sources(symbol):  # futuros; si no responde (EE. UU.), spot
        try:
            return _fetch_long(base, symbol, tf, total)
        except Exception as e:
            _mark_bad(base, symbol, e)
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


# ----------------------------- MODO DE CADA TEMPORALIDAD (igual que el indicador v8 en modo automático) -----------------------------
LIM_W = (0.0, 0.40, 0.60)   # modo límite: sin entrada a mercado · orden límite en E2 (40 %) y E3 (60 %)
ALL_TFS = ("1m", "3m", "5m", "15m", "1h", "4h")
DEFAULT_TFS = ["5m", "15m", "1h", "4h"]


def tf_mode(tf, st):
    """Auditoría v3.6 (24 monedas, comisiones de Bitunix):
    5m  → límite + a favor de la Tendencial de 15m (+0.20 R/op) · 4h → límite (+0.23 R/op)
    1m/3m → modo preciso (a favor de 15m y 4h, DCA 10/30/60) · 15m/1h → DCA 20/30/50 a mercado."""
    if st.get("limit", True) and tf in ("5m", "4h"):
        return dict(mode="limit", W=LIM_W, min_dist=MIN_DIST, htf=["15m"] if tf == "5m" else [])
    if st.get("h4", True) and TF_MS.get(tf, 0) <= 300_000:
        return dict(mode="low", W=LOW_W, min_dist=LOW_MIN_DIST, htf=["4h", "15m"])
    return dict(mode="dca", W=DCA_W, min_dist=MIN_DIST, htf=[])


# ----------------------------- SEÑALES + GESTIÓN (copia exacta del motor del indicador) -----------------------------
def simulate(d, I, capital, risk_pct, dirs=None, W=DCA_W, min_dist=None):
    """Señales y gestión vela a vela. dirs = Tendencial de temporalidades mayores que debe ir a favor.
    W[0] == 0 → modo límite: no se entra a mercado; si el precio llega al TP1 sin llenar E2, la orden se cancela.
    Contabilidad v3.6: cada TP cierra un % de lo que REALMENTE está abierto; ganada = cierre con ganancia neta."""
    min_dist = MIN_DIST if min_dist is None else min_dist
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
        ok_htf = all(ds[i] == side for ds in dirs) if dirs else True
        sig = c1 and not fired and score >= MIN_TRADE and ok_ema and ok_htf
        if sig:
            fired = True
        new_side = side if sig else 0
        last = dict(pts=score if c1 else 0, c=(c1, c2, c3))

        # gestión de la operación abierta
        close_now, exit_px, reason, exit_fee = False, None, "", FEE_TAKER
        if T and i > T["bar"]:
            s = T["side"]
            empty = T["pos"] == 0
            if T["fills"] == 1 and (b["l"] <= T["e2"] if s == 1 else b["h"] >= T["e2"]):
                T["avg"] = (T["avg"] * T["pos"] + T["e2"] * W[1]) / (T["pos"] + W[1]); T["pos"] += W[1]; T["fills"] = 2
                T["fees"] += W[1] * FEE_MAKER
            if T["fills"] == 2 and (b["l"] <= T["e3"] if s == 1 else b["h"] >= T["e3"]):
                T["avg"] = (T["avg"] * T["pos"] + T["e3"] * W[2]) / (T["pos"] + W[2]); T["pos"] += W[2]; T["fills"] = 3
                T["fees"] += W[2] * FEE_MAKER
            if empty and T["pos"] > 0:
                events.append(dict(kind="FILL", bar=i, entry=T["bar"], side=s, price=T["avg"], fills=T["fills"]))
            if T["pos"] == 0 and ((b["h"] >= T["tps"][0]) if s == 1 else (b["l"] <= T["tps"][0])):
                events.append(dict(kind="CANCEL", bar=i, entry=T["bar"], side=s, reason="tp1"))
                T = None
                continue  # orden límite sin llenar y el precio ya llegó al TP1 → cancelada
            if (b["l"] <= T["sl"]) if s == 1 else (b["h"] >= T["sl"]):
                close_now, exit_px, reason = True, T["sl"], "stop"
            else:
                for k in range(T["tp"], 3):
                    px = T["tps"][k]
                    if (b["h"] >= px) if s == 1 else (b["l"] <= px):
                        if k < 2:
                            qk = T["pos"] * TP_SPLIT[k] / T["remS"]  # parte de la posición abierta que cierra este TP
                            T["real"] += qk * s * (px - T["avg"]) / T["avg"]
                            T["fees"] += qk * FEE_MAKER
                            T["pos"] -= qk
                            T["remS"] -= TP_SPLIT[k]
                            T["tp"] = k + 1
                            if k == 0:
                                events.append(dict(kind="TP1", bar=i, entry=T["bar"], side=s, price=px, q=T["q"]))
                        else:
                            close_now, exit_px, reason, exit_fee = True, px, "tp3", FEE_MAKER
                            T["tp"] = 3
                    else:
                        break
        if T and new_side == -T["side"] and not close_now:
            close_now, exit_px, reason = True, b["c"], "reverse"
        if close_now and T and T["pos"] == 0 and T["real"] == 0:  # orden límite sin llenar + señal contraria
            events.append(dict(kind="CANCEL", bar=i, entry=T["bar"], side=T["side"], reason="reverse"))
            T, close_now = None, False
        if close_now and T:
            T["real"] += T["pos"] * T["side"] * (exit_px - T["avg"]) / T["avg"]
            T["fees"] += T["pos"] * exit_fee
            pnl_usd = (T["real"] - T["fees"]) * T["notional"]
            events.append(dict(kind="CLOSE", bar=i, entry=T["bar"], side=T["side"], price=exit_px, q=T["q"], win=pnl_usd > 0, tp=T["tp"],
                               reason=reason, pnl=pnl_usd, r=pnl_usd / risk_usd))
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
            T = dict(side=s, q=score, bar=i, e1=e1, e2=e2, e3=e3, sl=sl, tps=tps, avg=e1, pos=W[0], fills=1, tp=0,
                     real=0.0, remS=1.0, fees=W[0] * FEE_TAKER, notional=notional, lev=lev, qty=qty, margin=margin)
            events.append(dict(kind="OPEN", bar=i, side=s, price=e1, q=score, sl=sl, tps=tps, e2=e2, e3=e3, lev=lev,
                               c=(c1, c2, c3), qty=qty, margin=margin, risk=risk_usd, limit=W[0] == 0, w=W))
    return events, T, last


# ----------------------------- TELEGRAM -----------------------------
def tg(token, method, params=None, timeout=20):
    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            desc = json.loads(e.read().decode()).get("description", "")
        except Exception:
            desc = ""
        raise RuntimeError(f"Telegram {method} {e.code}: {desc}") from None


def kb(rows):
    """Botones bajo el mensaje: [[(texto, url o dato), ...], ...]"""
    return json.dumps({"inline_keyboard": [[{"text": t, ("url" if v.startswith("http") else "callback_data"): v} for t, v in row] for row in rows]})


def send(token, chat_id, text, buttons=None, silent=False):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if buttons:
        p["reply_markup"] = kb(buttons)
    if silent:
        p["disable_notification"] = "true"
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


def wr_dot(h):
    """Color según lo que de verdad gana o pierde por operación (R), no solo el % de acierto."""
    return "⚪" if h["n"] < 5 else "🟢" if h["avg"] >= 0.05 else "🔴" if h["avg"] <= -0.05 else "🟡"


def wr_line(h):
    """Winrate REAL de esa moneda/temporalidad: operación ganada = cerró con ganancia neta (después de comisiones)."""
    n = h["n"] if h else 0
    if n < 5:
        return "⚪ Winrate: — (sin historial)"
    return f"{wr_dot(h)} Winrate: {h['wr']:.0f}% · {h['avg']:+.2f} R/op ({n} ops)"


def message(sym, tf, ev, extra=None, scale=1.0):
    """Mensajes con el formato del Radar de Aurolo. scale = riesgo de este usuario / riesgo del cálculo."""
    extra = extra or {}
    s = "LONG" if ev["side"] == 1 else "SHORT"
    icon = "🟢" if ev["side"] == 1 else "🔴"
    pair, TF = sym + ".P", tf.upper()
    link = f'<a href="{chart_url(sym, tf)}">{pair} {TF}</a>'
    k = ev["kind"]
    if k == "OPEN":
        e1 = ev["price"]
        pct = lambda x: f"{(x - e1) / e1 * 100:+.2f}%"
        q1, q2, q3 = (fq(x * scale) for x in ev["qty"])
        lim = ev.get("limit")
        lines = [f"{icon} <b>{s}</b> {link} {icon}" + ("  📥 <b>ORDEN LÍMITE</b>" if lim else ""), ""]
        if extra.get("late"):
            lines += [f"⏱ <i>Señal de hace {extra['late']} min (sigue activa)</i>", ""]
        lines.append("📡 <b>OBJETIVO DETECTADO:</b>")
        if lim:
            w2, w3 = round(ev["w"][1] * 100), round(ev["w"][2] * 100)
            lines += [f"├── 💲 Precio actual: {money(e1)} (<b>no</b> entrar a mercado)",
                      f"├── 📥 Límite E2 ({w2}%): {money(ev['e2'])} ({pct(ev['e2'])})",
                      f"├── 📥 Límite E3 ({w3}%): {money(ev['e3'])} ({pct(ev['e3'])})"]
        else:
            lines += [f"├── 💰 Entrada: {money(e1)}",
                      f"├── 💰 DCA: E2: {money(ev['e2'])} · E3: {money(ev['e3'])}"]
        lines += [f"├── 🛟 Stop Loss: {money(ev['sl'])} ({pct(ev['sl'])})",
                  f"├── ⚡ Apalancamiento recomendado: <b>{ev['lev']}x</b>",
                  f"└── {wr_line(extra.get('hist'))}", "",
                  "🎯 <b>TAKE PROFITS:</b>",
                  f"├── TP1: {money(ev['tps'][0])} ({pct(ev['tps'][0])})",
                  f"├── TP2: {money(ev['tps'][1])} ({pct(ev['tps'][1])})",
                  f"└── TP3: {money(ev['tps'][2])} ({pct(ev['tps'][2])})"]
        tgt = extra.get("htf_tp")
        if tgt:
            lines.append(f"🧲 Tendencial {tgt[0]}: {money(tgt[1])} ({pct(tgt[1])})")
        qline = f"└── E2: {q2} · E3: {q3} {coin(sym)}" if lim else f"└── E1: {q1} · E2: {q2} · E3: {q3} {coin(sym)}"
        lines += ["", f"📦 <b>CANTIDAD</b> (riesgo ${ev['risk'] * scale:,.0f}):", qline, "",
                  "⚓ <b>ESTRATEGIA:</b>"]
        if lim:
            lines += ["├── 📥 Pon E2 y E3 como órdenes límite (ya con su SL y TP)",
                      "└── ❌ Si el precio toca TP1 sin llenarlas, cancélalas (te aviso)"]
        else:
            lines += ["└── ⏰ Salir cuando indique señal de tiempo"]
        lines += ["", "📻 <i>¿Disparas?</i>", "⚓ Opera en Bitunix · E2, E3 y TP con orden límite"]
        return "\n".join(lines)
    if k == "FILL":
        return "\n".join([f"📥 <b>ORDEN LLENADA</b> {icon}", "", f"📊 Par: {link}", f"{icon} Tipo: {s}",
                          f"💰 Precio medio: {money(ev['price'])}", "", "✅ <i>Ya estás dentro: el SL y los TP siguen igual</i>"])
    if k == "CANCEL":
        why = "el precio llegó al TP1 sin tocar tu entrada" if ev.get("reason") == "tp1" else "apareció la señal contraria"
        return "\n".join(["❌ <b>ORDEN CANCELADA</b> ❌", "", f"📊 Par: {link}", f"{icon} Tipo: {s}",
                          f"ℹ️ {why}.", "", "🗑 <b>Cancela tus órdenes límite E2/E3 de esta señal</b>"])
    if k == "TP1":
        return "\n".join(["✅ <b>TP1 ALCANZADO</b> ✅", "", f"📊 Par: {link}", f"⏰ Temporalidad: {TF}", f"{icon} Tipo: {s}",
                          f"🎯 Precio: {money(ev['price'])}", "", "💡 <i>Cierra una parte y deja correr el resto</i>"])
    res = {"tp3": "🏁 TP3 alcanzado", "stop": "⛔ Stop loss" if not ev.get("tp") else "🏁 Resto cerrado en SL tras TP",
           "reverse": "🔄 Señal contraria"}[ev["reason"]]
    pnl = ev["pnl"] * scale
    lines = ["⏰ <b>OPERACIÓN CERRADA</b> ⏰", "", f"📊 Par: {link}", f"⏰ Temporalidad: {TF}", f"{icon} Tipo: {s}",
             f"{'💰' if pnl >= 0 else '🩸'} Resultado: {res} · {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f} ({ev['r']:+.2f} R)",
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



# ----------------------------- ESTADO -----------------------------
CLOUD = os.environ.get("CLOUD") == "1"


def load_state():
    st = {"sent": [], "init": False}
    for path in (STATE_FILE, STATE_FILE + ".bak"):  # si el archivo quedó dañado (PC apagada al guardar), se usa la copia
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    st.update(json.load(f))
                break
            except Exception as e:
                print(f"⚠️ {os.path.basename(path)} dañado ({e}); se usa la copia de seguridad o se empieza de cero")
    st.setdefault("symbols", [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s.strip()])
    st.setdefault("tfs", [t.strip() for t in os.environ.get("TIMEFRAMES", "1m,3m,5m,15m,1h").split(",") if t.strip()])
    st.setdefault("capital", float(os.environ.get("CAPITAL", "2000")))
    st.setdefault("risk", float(os.environ.get("RISK_PCT", "2")))
    st.setdefault("h4", True)
    st.setdefault("limit", True)
    st.setdefault("radar_every", 0)
    st.setdefault("top", int(os.environ.get("TOP", "30")))
    if not st.get("v32"):  # v3.2: modo preciso de 3m/5m
        st["h4"], st["v32"] = True, True
    if not st.get("v36"):  # v3.6: winrate real (contabilidad corregida) → recalcular el historial
        st["hist"], st["hist_seeded"], st["v36"] = {}, [], True
    if not st.get("v33"):
        st["top"], st["radar_every"], st["v33"] = int(os.environ.get("TOP", "30")), 0, True
    if not st.get("v31"):
        if "1h" not in st["tfs"]:
            st["tfs"].append("1h")
        st["v31"] = True
    st.setdefault("offset", 0)
    st.setdefault("seen_pairs", [])
    st.setdefault("trades", {})
    st.setdefault("live", {})
    st.setdefault("pending", {})
    if not st.get("v40"):  # v4.0: modo límite en 5m/4h (nuevo historial), usuarios con su propia configuración
        for p in list(st.get("hist", {})):
            if p.endswith(("_5m", "_4h")):
                del st["hist"][p]
        st["hist_seeded"] = [p for p in st.get("hist_seeded", []) if not p.endswith(("_5m", "_4h"))]
        tfs = [t for t in st["tfs"] if not (t == "1m" and st.get("mute1m"))]
        if "4h" not in tfs:
            tfs.append("4h")
        st["users"] = {"owner": {"name": "Admin", "tfs": [t for t in ALL_TFS if t in tfs]}}
        st.pop("quiet", None)
        st["v40"] = True
    st.setdefault("users", {"owner": {"name": "Admin", "tfs": list(DEFAULT_TFS)}})
    fav = os.path.join(BASE, "favoritos.txt")  # monedas extra para la PC: una lista separada por comas o líneas
    if not CLOUD and os.path.exists(fav):
        with open(fav, encoding="utf-8") as f:
            for x in re.split(r"[,\s]+", f.read()):
                x = norm_symbol(x) if x.strip() and not x.startswith("#") else ""
                if x and x not in st["symbols"]:
                    st["symbols"].append(x)
    if CLOUD:  # en GitHub las monedas se cambian en Settings → Variables → SYMBOLS
        st["top"] = int(os.environ.get("TOP", "30"))
        st["symbols"] = [norm_symbol(s) for s in re.split(r"[,\s]+", os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT")) if s.strip()]
        st["tfs"] = [t.strip() for t in os.environ.get("TIMEFRAMES", "15m,1h,4h").split(",") if t.strip()]
        st["users"] = {"owner": {"name": "Admin", "tfs": list(st["tfs"])}}  # la nube solo avisa al administrador
        st["pending"] = {}
    owner = st["users"]["owner"]
    owner["capital"], owner["risk"] = st["capital"], st["risk"]
    return st


def save_state(st, now):
    cutoff = now - 7 * 86_400_000
    st["sent"] = sorted(k for k in set(st["sent"]) if int(k.split("_")[-2]) >= cutoff)
    st["trades"] = {k: v for k, v in st.get("trades", {}).items() if v.get("t", 0) >= cutoff}
    data = dict(st)
    if CLOUD:  # el repositorio es público: no se guardan datos de usuarios
        data.pop("users", None)
        data.pop("pending", None)
    tmp = STATE_FILE + ".tmp"
    try:  # guardado seguro: primero un archivo temporal y luego se reemplaza (nunca queda vacío a medias)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(STATE_FILE) and os.path.getsize(STATE_FILE) > 0:
            os.replace(STATE_FILE, STATE_FILE + ".bak")
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print("⚠️ no se pudo guardar el estado:", e)


def scan_tfs(st):
    """Temporalidades que hay que calcular = las que tiene activadas algún usuario."""
    want = set()
    for u in st["users"].values():
        want.update(u.get("tfs", []))
    return [t for t in ALL_TFS if t in want]


def chat_of(uid, owner):
    return owner if uid == "owner" else uid


def uid_of(chat_id, owner):
    return "owner" if str(chat_id) == str(owner) else str(chat_id)


def user_scale(st, uid):
    u = st["users"].get(uid, {})
    mine = u.get("capital", st["capital"]) * u.get("risk", st["risk"]) / 100
    base = st["capital"] * st["risk"] / 100
    return mine / base if base else 1.0


def settings_line(st, uid="owner"):
    u = st["users"].get(uid, {})
    cap, rk = u.get("capital", st["capital"]), u.get("risk", st["risk"])
    line = (f"💰 ${cap:,.0f} · riesgo {rk:g}% = ${cap * rk / 100:,.2f} por operación\n"
            f"⏱ Tus temporalidades: {', '.join(u.get('tfs', [])) or 'ninguna (pausado)'}")
    if uid == "owner":
        line += (f"\n🛡 SL mínimo {MIN_DIST:g}% · 📥 modo límite 5m/4h: {'sí' if st.get('limit', True) else 'no'}"
                 f" · 🎯 modo preciso 1m/3m: {'sí' if st['h4'] else 'no'} · monedas: favoritas + top {st.get('top', 0)}")
    return line


# ----------------------------- USUARIOS Y CONFIGURACIÓN DESDE TELEGRAM -----------------------------
HELP_USER = ("🤖 <b>Bridge Táctico · comandos</b>\n"
             "/config → elige tus temporalidades con botones\n"
             "/capital 500 → tu capital en $ (para calcular cantidades)\n"
             "/riesgo 1 → % de riesgo por operación\n"
             "/winrate → acierto real de cada moneda y temporalidad\n"
             "/radar → tendencia de las monedas favoritas\n"
             "/estado → operaciones activas\n"
             "/stop → dejar de recibir señales\n"
             "/ayuda → este mensaje")
HELP_OWNER = (HELP_USER + "\n\n👑 <b>Administrador</b>\n"
              "/usuarios → quién recibe señales · /expulsar ID\n"
              "/agregar SOL XRP · /quitar SOL · /lista → monedas favoritas\n"
              "/top 30 → además vigila las 30 monedas con más volumen (0 = solo favoritas)\n"
              "/limite si|no → 5m y 4h con órdenes límite (recomendado)\n"
              "/h4 si|no → modo preciso 1m/3m (a favor de 15m y 4h)\n"
              "/radar cada 30 · /radar off → radar automático\n"
              "/nube → cómo funciona el respaldo en GitHub\n\n"
              "👥 Para compartir: pásale a tu amigo el enlace del bot. Al pulsar INICIAR te llega una solicitud para aprobarla.")


def config_rows(u):
    mark = lambda t: ("✅ " if t in u.get("tfs", []) else "▫️ ") + t
    return [[(mark(t), "tf:" + t) for t in ("1m", "3m", "5m")],
            [(mark(t), "tf:" + t) for t in ("15m", "1h", "4h")],
            [("✔️ Listo", "cfg:done")]]


CONFIG_TXT = ("⚙️ <b>Tus avisos</b>\nToca una temporalidad para activarla ✅ o quitarla ▫️.\n"
              "Recomendadas (con ventaja en la auditoría): <b>5m</b> y <b>4h</b>.")


def send_config(token, chat_id, u):
    send(token, chat_id, CONFIG_TXT, config_rows(u))


def handle_callback(token, owner, st, cq):
    cid = str(cq.get("message", {}).get("chat", {}).get("id", ""))
    mid = cq.get("message", {}).get("message_id")
    data = cq.get("data", "")
    uid = uid_of(cid, owner)
    note = ""
    if data.startswith(("ok:", "no:")) and uid == "owner":
        who = data[3:]
        name = st["pending"].pop(who, None) or st["users"].get(who, {}).get("name", who)
        if data.startswith("ok:"):
            st["users"][who] = {"name": name, "tfs": list(DEFAULT_TFS), "capital": st["capital"], "risk": st["risk"]}
            try:
                send(token, who, "✅ <b>Acceso aprobado</b>\nDesde ahora recibes las señales de Bridge Táctico.\n\n" + HELP_USER)
                send_config(token, who, st["users"][who])
            except Exception as e:
                print("aprobar:", e)
            tg(token, "editMessageText", {"chat_id": cid, "message_id": mid, "text": f"✅ {name} aprobado"})
        else:
            try:
                send(token, who, "❌ Tu solicitud no fue aprobada.")
            except Exception:
                pass
            tg(token, "editMessageText", {"chat_id": cid, "message_id": mid, "text": f"❌ {name} rechazado"})
    elif data.startswith("tf:") and uid in st["users"]:
        t = data[3:]
        tfs = st["users"][uid].setdefault("tfs", [])
        if t in tfs:
            tfs.remove(t)
            note = f"{t} desactivado"
        elif t in ALL_TFS:
            tfs.append(t)
            note = f"{t} activado"
        st["users"][uid]["tfs"] = [x for x in ALL_TFS if x in tfs]
        try:
            tg(token, "editMessageReplyMarkup", {"chat_id": cid, "message_id": mid, "reply_markup": kb(config_rows(st["users"][uid]))})
        except Exception as e:
            print("config:", e)
    elif data == "cfg:done" and uid in st["users"]:
        tg(token, "editMessageText", {"chat_id": cid, "message_id": mid, "parse_mode": "HTML",
                                      "text": "✅ <b>Guardado</b>\n" + settings_line(st, uid)})
    try:
        tg(token, "answerCallbackQuery", {"callback_query_id": cq["id"], "text": note})
    except Exception:
        pass


def handle_commands(token, owner, st, ctx):
    try:
        q = urllib.parse.urlencode({"offset": st["offset"], "timeout": 0})
        upd = http_get(f"https://api.telegram.org/bot{token}/getUpdates?{q}")
    except Exception:
        return
    for u in upd.get("result", []):
        st["offset"] = u["update_id"] + 1
        try:
            if u.get("callback_query"):
                handle_callback(token, owner, st, u["callback_query"])
                continue
            msg = u.get("message") or u.get("edited_message") or {}
            ch = msg.get("chat", {})
            if ch.get("type") != "private":
                continue
            command(token, owner, st, ctx, str(ch.get("id")), msg)
        except Exception as e:
            print("comando:", e)


def command(token, owner, st, ctx, cid, msg):
    text = (msg.get("text") or "").strip()
    uid = uid_of(cid, owner)
    parts = text.split()
    cmd = parts[0].split("@")[0].lower() if parts else ""
    args = parts[1:]
    reply = lambda t, b=None: send(token, cid, t, b)
    if uid not in st["users"]:  # alguien nuevo: pide acceso al administrador
        frm = msg.get("from", {})
        name = (frm.get("first_name", "") + " " + frm.get("last_name", "")).strip() or frm.get("username", "") or cid
        if frm.get("username"):
            name += f" (@{frm['username']})"
        if cid in st["pending"]:
            reply("⏳ Tu solicitud ya está con el administrador. Te aviso cuando la apruebe.")
            return
        st["pending"][cid] = name
        reply("👋 <b>Bridge Táctico</b>\nTu solicitud de acceso se envió al administrador. Te aviso cuando la apruebe.")
        send(token, owner, f"👤 <b>Nuevo usuario</b>\n{name} quiere recibir las señales.",
             [[("✅ Aprobar", f"ok:{cid}"), ("❌ Rechazar", f"no:{cid}")]])
        return
    me = st["users"][uid]
    is_owner = uid == "owner"
    if not text.startswith("/"):
        return
    if cmd in ("/start", "/ayuda", "/help"):
        reply(HELP_OWNER if is_owner else HELP_USER)
    elif cmd in ("/config", "/tf"):
        tfs = [a.lower() for a in args if a.lower() in ALL_TFS]
        if tfs:
            me["tfs"] = [t for t in ALL_TFS if t in tfs]
            reply("⏱ Tus temporalidades: " + ", ".join(me["tfs"]))
        else:
            send_config(token, cid, me)
    elif cmd == "/capital":
        v = parse_num(args, amount=True)
        if v and v > 0:
            me["capital"] = v
            if is_owner:
                st["capital"] = v
        reply(settings_line(st, uid))
    elif cmd == "/riesgo":
        v = parse_num(args)
        if v and 0 < v <= 10:
            me["risk"] = v
            if is_owner:
                st["risk"] = v
        reply(settings_line(st, uid))
    elif cmd == "/winrate":
        reply(winrate_report(st, uid))
    elif cmd == "/estado":
        reply(status_report(st, ctx, uid))
    elif cmd == "/radar" and not (is_owner and args):
        reply(radar_report(st, ctx, uid))
    elif cmd == "/stop" and not is_owner:
        st["users"].pop(uid, None)
        reply("🔕 Ya no recibirás señales. Envía /start si quieres volver.")
        send(token, owner, f"🔕 {me.get('name', uid)} dejó de recibir señales.")
    elif cmd == "/lista":
        reply("⭐ Favoritos: " + ", ".join(st["symbols"]) + "\n" + settings_line(st, uid))
    elif not is_owner:
        reply("Ese comando es solo para el administrador. Escribe /ayuda.")
    # ---------- solo administrador ----------
    elif cmd == "/radar":
        if args[0].lower() in ("off", "no", "apagar"):
            st["radar_every"] = 0
            reply("📡 Radar automático: apagado (escribe /radar cuando lo quieras)")
        else:
            v = parse_num(args[1:] if args[0].lower() == "cada" else args)
            if v and v >= 5:
                st["radar_every"] = int(v)
            reply(f"📡 Radar automático cada {st.get('radar_every', 60)} min")
    elif cmd in ("/agregar", "/add"):
        added = []
        for a in args:
            s = norm_symbol(a)
            try:
                fetch_klines(s, "1m", 5)
            except Exception:
                reply(f"⚠️ {s} no existe en Binance ni en Bitunix")
                continue
            if s not in st["symbols"]:
                st["symbols"].append(s)
                added.append(s)
        reply("⭐ Agregado: " + (", ".join(added) if added else "nada nuevo") + "\nFavoritos: " + ", ".join(st["symbols"]))
    elif cmd in ("/quitar", "/remove"):
        for a in args:
            s = norm_symbol(a)
            if s in st["symbols"]:
                st["symbols"].remove(s)
        reply("Favoritos: " + (", ".join(st["symbols"]) or "(vacío)"))
    elif cmd == "/top":
        v = parse_num(args)
        if v is not None and 0 <= v <= 60:
            st["top"] = int(v)
        reply(f"🔎 Escaneo: tus favoritas + las {st.get('top', 0)} monedas con más volumen ({len(active_symbols(st))} en total)")
    elif cmd in ("/limite", "/límite"):
        if args:
            st["limit"] = args[0].lower() in ("si", "sí", "on", "1", "s")
        reply("📥 5m y 4h con órdenes límite: " + ("<b>ACTIVADO</b> (5m solo a favor de la Tendencial de 15m)" if st["limit"] else "desactivado"))
    elif cmd == "/h4":
        if args:
            st["h4"] = args[0].lower() in ("si", "sí", "on", "1", "yes", "y", "s")
        reply("🎯 Modo preciso 1m/3m: " + ("<b>ACTIVADO</b> (a favor de la Tendencial de 15m y 4h · DCA 10/30/60 · SL mín 0.45 %)" if st["h4"] else "desactivado"))
    elif cmd == "/1m":  # compatibilidad
        if args:
            on = args[0].lower() in ("si", "sí", "on", "1", "s")
            me["tfs"] = [t for t in ALL_TFS if (t in me["tfs"] and t != "1m") or (t == "1m" and on)]
        reply("⏱ Tus temporalidades: " + ", ".join(me["tfs"]))
    elif cmd == "/usuarios":
        rows = [f"👑 Tú: {', '.join(st['users']['owner'].get('tfs', []))}"]
        rows += [f"👤 {u.get('name', k)} · ID {k} · {', '.join(u.get('tfs', [])) or 'pausado'}" for k, u in st["users"].items() if k != "owner"]
        rows += [f"⏳ {n} · ID {k} (pendiente)" for k, n in st["pending"].items()]
        reply("👥 <b>Usuarios</b>\n" + "\n".join(rows) + "\n\nPara quitar a alguien: /expulsar ID")
    elif cmd == "/expulsar":
        for a in args:
            gone = st["users"].pop(a, None) if a != "owner" else None
            st["pending"].pop(a, None)
            if gone:
                reply(f"🚫 {gone.get('name', a)} ya no recibe señales")
    elif cmd == "/nube":
        reply("☁️ <b>Respaldo en GitHub</b>\nMientras esta PC está encendida, ella envía todas las señales al instante "
              "(el mensaje fijado 🟢 lo indica). Si la PC se apaga, GitHub avisa 15m, 1h y 4h, pero solo al administrador "
              "y con 5-30 min de retraso (GitHub no es puntual).")


def status_report(st, ctx, uid="owner"):
    tfs = st["users"].get(uid, {}).get("tfs", [])
    lines = ["📊 <b>Operaciones activas</b>"]
    for (sym, tf), info in sorted(ctx.items()):
        T = info.get("T")
        if not T or tf not in tfs:
            continue
        estado = "esperando entrada (límite)" if T["pos"] == 0 else f"TP{T['tp']} ✓" if T["tp"] else "en curso"
        lines.append(f"{'🟢' if T['side'] == 1 else '🔴'} <b>{coin(sym)} {tf}</b> {'LONG' if T['side'] == 1 else 'SHORT'} · "
                     f"E2 {fmt(T['e2'])} · SL {fmt(T['sl'])} · {estado}")
    if len(lines) == 1:
        lines.append("Ninguna en tus temporalidades.")
    lines.append(settings_line(st, uid))
    return "\n".join(lines[:60])


def winrate_report(st, uid="owner"):
    tfs = st["users"].get(uid, {}).get("tfs", []) or scan_tfs(st)
    lines = ["🏆 <b>Winrate real por temporalidad</b> (ganancia neta tras comisiones · R por operación)"]
    for sym in [x for x in active_symbols(st) if any(f"{x}_{t}" in st.get("hist", {}) for t in tfs)]:
        cells = []
        for tf in tfs:
            h = hist_stats(st, f"{sym}_{tf}")
            cells.append(f"{tf.upper()} ⚪ —" if h["n"] < 5 else f"{tf.upper()} {wr_dot(h)} {h['wr']:.0f}% · {h['avg']:+.2f}R ({h['n']})")
        lines.append(f"<b>{coin(sym)}</b>  " + " · ".join(cells))
    lines.append("\n🟢 gana ≥ +0.05 R/op · 🟡 ~0 · 🔴 pierde · ⚪ menos de 5 operaciones")
    out = "\n".join(lines)
    return out if len(out) < 4000 else out[:3900] + "\n…"


def radar_report(st, ctx, uid="owner"):
    tfs = st["users"].get(uid, {}).get("tfs", []) or scan_tfs(st)
    lines = ["📡 <b>Radar de favoritos</b>"]
    for sym in st["symbols"]:
        cells, ups, n = [], 0, 0
        for tf in tfs:
            info = ctx.get((sym, tf))
            if not info:
                cells.append(f"{tf} …")
                continue
            n += 1
            ups += 1 if info["up"] else 0
            T = info["T"]
            mark = (("🟢" if T["side"] == 1 else "🔴") + f"{T['q']}/3") if T else (("▲" if info["up"] else "▼") + ("◦" if info.get("pts", 0) >= 1 else ""))
            cells.append(f"{tf} {mark}")
        bias = f" · {round(ups / n * 100)}% ▲" if n else ""
        lines.append(f"<b>{coin(sym)}</b>{bias}  " + "  ".join(cells))
    lines.append("\n▲▼ tendencia · ◦ tocando la Tendencial · 🟢/🔴 LONG/SHORT activo (calidad)")
    out = "\n".join(lines)
    return out if len(out) < 4000 else out[:3900] + "\n…"


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
_last1m = {}  # moneda -> últimas velas de 1m (para vigilar TP/entradas al minuto sin pedir más datos)


def compute(sym, tf, st):
    """Descarga y calcula una moneda/temporalidad. No toca el estado (se puede ejecutar en paralelo)."""
    pair = f"{sym}_{tf}"
    d = fetch_klines(sym, tf, 1000)
    if tf == "1m":
        _last1m[sym] = (time.time(), d[-5:])
    I = indicators(d)
    m = tf_mode(tf, st)
    dirs = [dir_series(sym, d, h) for h in m["htf"]] or None
    events, T, last = simulate(d, I, st["capital"], st["risk"], dirs, m["W"], m["min_dist"])
    seed = None
    if pair not in st.get("hist_seeded", []):  # la primera vez: winrate con un historial largo
        try:
            dl = fetch_long(sym, tf, 3000)
            dl_dirs = [dir_series(sym, dl, h) for h in m["htf"]] or None
            evl, _, _ = simulate(dl, indicators(dl), st["capital"], st["risk"], dl_dirs, m["W"], m["min_dist"])
            seed = [(str(dl[e["entry"]]["t"]), 1 if e["win"] else 0, round(e["r"], 3)) for e in evl if e["kind"] == "CLOSE" and e["entry"] >= 250]
        except Exception as e:
            print(f"⚠️ historial {sym} {tf}: {e}")
    return dict(sym=sym, tf=tf, d=d, up=I["dir"][-1] < 0, st=I["st"][-1], events=events, T=T, pts=last["pts"], seed=seed)


def deliver(token, owner, st, uids, sym, tf, ev, extra=None):
    """Envía un aviso a cada usuario con su propio capital/riesgo. Devuelve a quién llegó."""
    ok = []
    for uid in uids:
        if uid not in st["users"]:
            continue
        try:
            send(token, chat_of(uid, owner), message(sym, tf, ev, extra, user_scale(st, uid)),
                 signal_buttons(sym, tf) if ev["kind"] == "OPEN" else None)
            ok.append(uid)
        except Exception as e:
            print("telegram:", uid, e)
            if "403" in str(e) and uid != "owner":  # el usuario bloqueó el bot
                st["users"].pop(uid, None)
    if ok:
        print(time.strftime("%H:%M:%S"), "enviado", ev["kind"], sym, tf, f"→ {len(ok)} usuario(s)")
    return ok


def apply(res, st, ctx, token, owner, notify=True):
    """Guarda el resultado y envía cada aviso por separado (una moneda y temporalidad por mensaje).
    Los avisos de seguimiento (entrada llenada, TP1, cancelada, cierre) solo llegan a quien recibió la señal."""
    sym, tf, d = res["sym"], res["tf"], res["d"]
    pair = f"{sym}_{tf}"
    T = res["T"]
    ctx[(sym, tf)] = {"up": res["up"], "st": res["st"], "T": T, "pts": res["pts"]}
    H = st.setdefault("hist", {}).setdefault(pair, {})
    if res["seed"] is not None:
        for k, w, r in res["seed"]:
            H.setdefault(k, [w, r])
        st.setdefault("hist_seeded", []).append(pair)
    for ev in res["events"]:
        if ev["kind"] == "CLOSE" and ev["entry"] >= 250:
            H[str(d[ev["entry"]]["t"])] = [1 if ev["win"] else 0, round(ev["r"], 3)]
    if len(H) > 150:
        for k in sorted(H, key=int)[:-150]:
            del H[k]
    now = int(time.time() * 1000)
    tfms = TF_MS.get(tf, 900_000)
    first_time = pair not in st["seen_pairs"]
    sent = set(st["sent"])
    trades, live = st.setdefault("trades", {}), st.setdefault("live", {})
    for ev in res["events"]:
        tid = f"{sym}_{tf}_{d[ev['bar'] if ev['kind'] == 'OPEN' else ev['entry']]['t']}"
        key = f"{tid}_{ev['kind']}"
        if key in sent:
            continue
        sent.add(key)
        if not notify or first_time:
            continue
        age = now - d[ev["bar"]]["ct"]
        if ev["kind"] == "OPEN":
            active = T is not None and T["bar"] == ev["bar"] and T["tp"] == 0
            if not (age <= 2 * tfms + 120_000 or (active and age <= 3 * tfms + 120_000)):
                continue  # demasiado vieja
            uids = [u for u, p in st["users"].items() if tf in p.get("tfs", [])]
            extra = {"hist": hist_stats(st, pair), "late": int(age // 60_000) if age > 150_000 else 0}
            extra.update(open_extras(sym, tf, ev, ctx))
            to = deliver(token, owner, st, uids, sym, tf, ev, extra)
            if uids and not to:
                sent.discard(key)  # no llegó a nadie (sin internet): se reintenta en la próxima vuelta
            if to:
                trades[tid] = {"to": to, "t": d[ev["bar"]]["t"]}
                if active:
                    live[tid] = {"sym": sym, "tf": tf, "side": ev["side"], "e2": ev["e2"], "tp1": ev["tps"][0], "filled": not ev.get("limit"), "last": now}
        else:
            tr = trades.get(tid)
            if not tr or tr.get("cancelled"):
                continue  # nadie recibió esa señal (o ya se avisó que se canceló): no se manda un TP suelto
            deliver(token, owner, st, tr["to"], sym, tf, ev)
            if ev["kind"] in ("CANCEL", "CLOSE"):
                live.pop(tid, None)
                if ev["kind"] == "CANCEL":
                    tr["cancelled"] = True
    # operaciones vigiladas al minuto: actualizar o retirar
    for tid in [t for t, v in live.items() if v["sym"] == sym and v["tf"] == tf]:
        if not T or tid != f"{sym}_{tf}_{d[T['bar']]['t']}":
            live.pop(tid, None)
        elif T["pos"] > 0:
            live[tid]["filled"] = True
    st["sent"] = list(sent)
    if first_time:
        st["seen_pairs"].append(pair)


def live_watch(token, owner, st):
    """Cada minuto: con las velas de 1m revisa las operaciones de 3m-4h que llegaron a los usuarios
    y avisa al momento: orden límite llenada, TP1 alcanzado u orden a cancelar (sin esperar al cierre de la vela grande)."""
    live, trades = st.get("live", {}), st.get("trades", {})
    if not live:
        return
    sent = set(st["sent"])
    syms = {v["sym"] for v in live.values() if v["tf"] != "1m"}
    bars = {}

    def get1m(sym):
        c = _last1m.get(sym)
        if c and time.time() - c[0] < 50:
            return sym, c[1]
        try:
            return sym, fetch_klines(sym, "1m", 5)
        except Exception:
            return sym, []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, bb in ex.map(get1m, syms):
            bars[sym] = bb
    for tid, v in list(live.items()):
        if v["tf"] == "1m" or tid not in trades:
            continue
        s, to = v["side"], trades[tid]["to"]
        for b in [x for x in bars.get(v["sym"], []) if x["ct"] > v["last"]]:
            hit = lambda px: (b["h"] >= px) if s == 1 else (b["l"] <= px)
            dip = lambda px: (b["l"] <= px) if s == 1 else (b["h"] >= px)
            if not v["filled"]:
                if dip(v["e2"]):
                    v["filled"] = True
                    if f"{tid}_FILL" not in sent:
                        sent.add(f"{tid}_FILL")
                        deliver(token, owner, st, to, v["sym"], v["tf"], dict(kind="FILL", side=s, price=v["e2"]))
                elif hit(v["tp1"]):
                    if f"{tid}_CANCEL" not in sent:
                        sent.add(f"{tid}_CANCEL")
                        deliver(token, owner, st, to, v["sym"], v["tf"], dict(kind="CANCEL", side=s, reason="tp1"))
                    trades[tid]["cancelled"] = True
                    live.pop(tid, None)
                    break
            elif hit(v["tp1"]):
                if f"{tid}_TP1" not in sent:
                    sent.add(f"{tid}_TP1")
                    deliver(token, owner, st, to, v["sym"], v["tf"], dict(kind="TP1", side=s, price=v["tp1"]))
                live.pop(tid, None)  # lo demás (TP2/TP3/SL y resultado) llega al cerrar la vela
                break
            v["last"] = b["ct"]
    st["sent"] = list(sent)


# ----------------------------- PC ENCENDIDA / NUBE DE RESPALDO -----------------------------
def heartbeat(token, owner, st, force=False):
    """Mensaje fijado en tu chat que dice que la PC está activa. La nube lo lee: si está al día, no repite avisos."""
    now = time.time()
    if not force and now - st.get("hb_t", 0) < 300:
        return
    st["hb_t"] = now
    txt = (f"🟢 Bot de la PC activo · {time.strftime('%H:%M')} · {len(active_symbols(st))} monedas · "
           f"{', '.join(scan_tfs(st))} · {len(st['users'])} usuario(s)")
    mid = st.get("status_msg")
    if mid:
        try:
            tg(token, "editMessageText", {"chat_id": owner, "message_id": mid, "text": txt})
            return
        except Exception as e:
            if "not modified" in str(e):
                return
    try:
        r = send(token, owner, txt, silent=True)
        new = r["result"]["message_id"]
        if mid:
            try:
                tg(token, "unpinChatMessage", {"chat_id": owner, "message_id": mid})
            except Exception:
                pass
        tg(token, "pinChatMessage", {"chat_id": owner, "message_id": new, "disable_notification": "true"})
        st["status_msg"] = new
    except Exception as e:
        print("estado:", e)


def pc_alive(token, owner):
    """La nube consulta el mensaje fijado: si la PC lo actualizó hace menos de 12 min, la PC está enviando todo."""
    try:
        pm = tg(token, "getChat", {"chat_id": owner})["result"].get("pinned_message") or {}
    except Exception as e:
        print("getChat:", e)
        return False, {}
    last = max(pm.get("date", 0), pm.get("edit_date", 0))
    return pm.get("text", "").startswith("🟢") and time.time() - last < 12 * 60, pm


def scan(pairs, st, ctx, token, owner, notify=True):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [(sym, tf, ex.submit(compute, sym, tf, st)) for sym, tf in pairs]
        for sym, tf, f in futs:
            try:
                apply(f.result(), st, ctx, token, owner, notify)
            except Exception as e:
                print(f"⚠️ {sym} {tf}: {e}")


def run_once(token, owner, st, ctx, notify=True):
    scan([(s, t) for s in active_symbols(st) for t in scan_tfs(st)], st, ctx, token, owner, notify)


def main():
    loop = "--loop" in sys.argv
    interactive = bool(sys.stdin) and sys.stdin.isatty() and not os.environ.get("CI")
    token, chat, offset = load_credentials(interactive)
    st, ctx = load_state(), {}
    st["offset"] = max(st.get("offset", 0), offset)

    if not loop:  # una pasada (GitHub Actions)
        alive, pm = pc_alive(token, chat) if CLOUD else (False, {})
        if CLOUD and not alive and pm.get("text", "").startswith("🟢"):
            try:
                tg(token, "editMessageText", {"chat_id": chat, "message_id": pm["message_id"],
                                              "text": "☁️ PC apagada · avisos 15m, 1h y 4h desde GitHub (pueden llegar con 5-30 min de retraso)"})
            except Exception as e:
                print("estado:", e)
        run_once(token, chat, st, ctx, notify=not alive)
        if os.environ.get("COMMANDS", "1") == "1":  # en la nube se apaga para no robarle los comandos al bot de la PC
            handle_commands(token, chat, st, ctx)
        if not st["init"] or os.environ.get("TEST") == "1":
            send(token, chat, ("🤖 <b>Bridge Táctico (nube) activo</b>" if not st["init"] else "🧪 <b>Prueba nube OK</b>") +
                 f"\nVigilando {len(active_symbols(st))} monedas en {', '.join(t.upper() for t in scan_tfs(st))}.\n" +
                 ("🟢 La PC está encendida: ella envía las señales al instante; la nube queda de respaldo." if alive
                  else "☁️ La PC está apagada: la nube envía 15m, 1h y 4h."))
        st["init"] = True
        save_state(st, int(time.time() * 1000))
        print("PC activa:", alive)
        return

    print("Bridge Táctico bot en marcha (cierra la ventana o Ctrl+C para detener)…")
    run_once(token, chat, st, ctx)
    heartbeat(token, chat, st, force=True)
    send(token, chat, f"🤖 <b>Bridge Táctico v4 activo</b>\nVigilando {len(active_symbols(st))} monedas en {', '.join(scan_tfs(st))}.\n"
                      "Cada señal llega en su propio mensaje.\n\n" + settings_line(st) +
                      "\n\n/config → elegir temporalidades · /ayuda → comandos", silent=True)
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
                tfs = scan_tfs(st)
                pairs = [(sym, tf) for sym in syms for tf in tfs if minute % (TF_MS[tf] // 60_000) == 0 or (sym, tf) not in ctx]
                t0 = time.time()
                scan(pairs, st, ctx, token, chat)
                live_watch(token, chat, st)
                heartbeat(token, chat, st)
                every = st.get("radar_every", 0)
                if every and minute % every == 0:  # radar automático a Telegram
                    try:
                        send(token, chat, radar_report(st, ctx))
                    except Exception as e:
                        print("radar:", e)
                save_state(st, int(now * 1000))
                print(time.strftime("%H:%M:%S"), f"revisado: {len(syms)} monedas · {len(pairs)} gráficos en {time.time() - t0:.0f}s", "|", ", ".join(tfs))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("error:", e)
        time.sleep(2)


if __name__ == "__main__":
    main()
