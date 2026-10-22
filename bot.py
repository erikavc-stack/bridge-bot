#!/usr/bin/env python3
"""
Bridge Táctico → Telegram  ·  v5.4 (misma lógica que el indicador Bridge Táctico v9.6 · CALIDAD A en 2h/4h/8h sin fin de semana · 1m a 1d · SL a la entrada tras TP1 en 15m/4h/1d · diario /stats ·
confluencias 🔥 (LONG/SHORT activo en 3+ temporalidades) · BTC y ETH vigilados de cerca)
Vigila tus monedas en 1m · 3m · 5m · 15m · 1h · 2h · 4h · 8h · 1d y avisa por Telegram, cada señal en su propio mensaje:
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
import json, os, re, sys, time, threading, socket, signal, html, math
import urllib.request, urllib.parse, urllib.error

VERSION = "5.4"

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

TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
         "4h": 14_400_000, "8h": 28_800_000, "1d": 86_400_000}
H4_MS = 14_400_000
BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
CONFIG_FILE = os.path.join(BASE, "config.json")
SOURCES = ["https://fapi.binance.com/fapi/v1/klines", "https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]
BITUNIX = "https://fapi.bitunix.com/api/v1/futures/market/kline"  # respaldo: monedas que no están en Binance (OKB, RAY...)
_bitunix_syms = set()   # monedas cuyos precios vienen de Bitunix (para el enlace del gráfico)
_bad_src = {}           # (fuente, moneda) -> hasta cuándo no usarla (no existe allí o datos viejos/planos)
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
    ok = [b for b in SOURCES if _bad_src.get((_host(b), symbol), 0) <= now]
    return [b for b in ok if _cool_until.get(_host(b), 0) <= now] or ok[-1:]


def _set_bad(base, symbol, secs):
    _bad_src[(_host(base), symbol)] = time.time() + secs


def _mark_bad(base, symbol, e):
    """Binance responde 400 cuando la moneda no existe en esa fuente: se recuerda 6 h para no perder tiempo."""
    if isinstance(e, urllib.error.HTTPError) and e.code == 400:
        _set_bad(base, symbol, 6 * 3600)


# ----------------------------- ACTIVOS TRADICIONALES (oro, plata, petróleo, acciones) -----------------------------
TRADFI_MAX_LEV = int(os.environ.get("TRADFI_MAX_LEV", "10"))
_COMMODITY_BASES = {"XAU", "XAG", "XPT", "XPD", "CL", "BZ", "NATGAS", "COPPER"}
_EQUITY_BASES = {"SPCX", "CRCL", "TSLA", "NVDA", "AAPL", "MSTR", "COIN", "HOOD", "AMZN", "META", "GOOGL", "MSFT", "PLTR",
                 "AMD", "INTC", "ORCL", "TSM", "AVGO", "SPY", "QQQ"}   # respaldo si Binance no responde (p. ej. desde EE. UU.)
_tradfi = {"t": 0, "map": {}}   # moneda -> "COMMODITY" / "EQUITY" / ... según Binance
_tradfi_lock = threading.Lock()


def tradfi_kind(sym):
    """None si es cripto; si no, el tipo de activo tradicional (se consulta a Binance una vez al día)."""
    if time.time() - _tradfi["t"] > 86_400 and _tradfi_lock.acquire(blocking=False):  # un solo hilo consulta
        try:
            _tradfi["t"] = time.time()
            info = http_get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30)
            _tradfi["map"] = {s["symbol"]: s.get("underlyingType") or "TRADFI" for s in info.get("symbols", [])
                              if s.get("contractType") == "TRADIFI_PERPETUAL"}
        except Exception as e:
            print("exchangeInfo:", e)
            _tradfi["t"] = time.time() - 86_400 + 600  # reintentar en 10 min
        finally:
            _tradfi_lock.release()
    k = _tradfi["map"].get(sym)
    if k:
        return k
    base = sym[:-4] if sym.endswith("USDT") else sym
    return "COMMODITY" if base in _COMMODITY_BASES else "EQUITY" if base in _EQUITY_BASES else None


def market_closed(kind, t=None):
    """Horario del mercado REAL detrás del contrato (en UTC, con margen por el horario de verano).
    Devuelve el motivo si no conviene abrir una operación nueva ahora, o "" si se puede."""
    g = time.gmtime(t if t is not None else time.time())
    wd, h = g.tm_wday, g.tm_hour + g.tm_min / 60   # lunes = 0 … domingo = 6
    if (wd == 4 and h >= 18) or wd == 5 or (wd == 6 and h < 23):
        return "cierre de fin de semana"
    if kind and kind != "COMMODITY" and wd == 6:
        return "cierre de fin de semana"
    if kind and kind != "COMMODITY" and wd == 0 and h < 14.5:
        return "bolsa de EE. UU. cerrada hasta el lunes 13:30–14:30 UTC"
    return ""


def stocks_night(kind, t=None):
    """Acciones fuera del horario de la bolsa de EE. UU. (el precio puede saltar en la apertura)."""
    if not kind or kind == "COMMODITY":
        return False
    g = time.gmtime(t if t is not None else time.time())
    h = g.tm_hour + g.tm_min / 60
    return not (13.5 <= h < 21)


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
        before = len(got)
        for r in rows:
            t = int(r["time"])
            got[t] = dict(t=t, o=float(r["open"]), h=float(r["high"]), l=float(r["low"]), c=float(r["close"]),
                          v=float(r.get("baseVol") or 0), ct=t + ms - 1)
        if len(rows) < 200 or len(got) == before:  # última página (o la API repite la misma): terminar
            break
        end = min(int(r["time"]) for r in rows)
        time.sleep(0.15)
    if got:
        _bitunix_syms.add(symbol)
    return [got[t] for t in sorted(got) if got[t]["ct"] < now][-total:]


def bad_data(bars, tf, sym=None):
    """Datos inservibles: moneda suspendida en esa fuente (velas planas, siempre el mismo precio) o sin velas recientes.
    En oro/acciones las velas planas son normales con el mercado real cerrado: ahí solo se mira que haya velas recientes."""
    if not bars:
        return True
    if bars[-1]["ct"] < int(time.time() * 1000) - 3 * TF_MS.get(tf, 60_000) - 120_000:
        return True
    if sym and tradfi_kind(sym):
        return False
    tail = bars[-50:]
    return len(tail) >= 10 and sum(1 for b in tail if b["h"] == b["l"]) >= 0.8 * len(tail)


def fetch_klines(symbol, tf, limit=600):
    q = urllib.parse.urlencode({"symbol": symbol, "interval": tf, "limit": limit})
    last = None
    for base in usable_sources(symbol):
        try:
            rows = http_get(f"{base}?{q}")
            bars = [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), v=float(k[5]), ct=int(k[6])) for k in rows]
            now = int(time.time() * 1000)
            bars = [b for b in bars if b["ct"] < now]  # solo velas cerradas
            if bad_data(bars, tf, symbol):
                _set_bad(base, symbol, 20 * 60)  # esta fuente tiene la moneda suspendida o atrasada: usar otra 20 min
                raise RuntimeError(f"{symbol} sin datos válidos en {_host(base)}")
            return bars
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
    out = [b for b in out if b["ct"] < now]
    if bad_data(out, tf, symbol):
        _set_bad(base, symbol, 20 * 60)
        raise RuntimeError(f"{symbol} sin datos válidos en {_host(base)}")
    return out


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


def dir_series(sym, d, htf="4h", limit=499):
    """Dirección de la Tendencial de una temporalidad mayor en su última vela CERRADA (1 alcista, -1 bajista).
    v4.9.4: 499 velas (peso 2 en Binance); 1500 solo para el historial largo del winrate."""
    ms = TF_MS[htf]
    bucket = int(time.time() * 1000) // ms
    c = _h4_cache.get((sym, htf))
    if not c or c[0] != bucket or c[2] < limit:
        kk = fetch_klines(sym, htf, limit)
        _, dh = supertrend(kk, ST_FACTOR, ST_ATR)
        _h4_cache[(sym, htf)] = (bucket, {b["t"]: (1 if dh[j] < 0 else -1) for j, b in enumerate(kk)}, limit)
    m = _h4_cache[(sym, htf)][1]
    return [m.get((b["t"] // ms - 1) * ms) for b in d]


def dir4_series(sym, d):
    return dir_series(sym, d, "4h")


def grade_dirs(sym, d, tf, limit=499):
    """v5.3: [Tendencial mayor de la moneda, Tendencial de BTC en esa temporalidad] alineadas con las velas d."""
    gh = GRADE_HTF.get(tf)
    if not gh:
        return []
    try:
        out = [dir_series(sym, d, gh, limit)]
        if sym != QA_REF and not tradfi_kind(sym):
            out.append(dir_series(QA_REF, d, gh, limit))
        return out
    except Exception as e:
        print(f"⚠️ calidad {sym} {tf}: {e}")
        return []


def grade_at(gd, i, side):
    """Calidad A (todo a favor) · B (una) · C (ninguna) y el detalle [moneda, BTC]."""
    if not gd:
        return None, []
    ok = [ds[i] == side for ds in gd]
    n = sum(ok)
    g = "A" if n == len(ok) else "B" if n else "C"
    return g, ok


# ----------------------------- MODO DE CADA TEMPORALIDAD (igual que el indicador v8 en modo automático) -----------------------------
LIM_W = (0.0, 0.40, 0.60)   # modo límite: sin entrada a mercado · orden límite en E2 (40 %) y E3 (60 %)
BE_TFS = tuple(t for t in os.environ.get("BE_TFS", "15m,4h,1d").split(",") if t)   # temporalidades con SL a la entrada tras TP1
ALL_TFS = ("1m", "3m", "5m", "15m", "1h", "2h", "4h", "8h", "1d")
DEFAULT_TFS = ["15m", "2h", "4h", "8h"]   # las de mejor resultado en la auditoría v4.7 (para amigos nuevos)
# v4.9 · CONFLUENCIAS: la misma moneda con LONG (o SHORT) activo en varias temporalidades a la vez.
# Backtest 50 monedas (con comisiones): las señales que salen con 2+ temporalidades MÁS ya activas del mismo lado
# ganaron más que el total (factor 1.22 vs 0.94 en 47 días con 15m-1d · 1.10 vs 1.03 en 174 días con 1h-1d).
CONF_TFS = tuple(t for t in os.environ.get("CONF_TFS", "3m,5m,15m,1h,2h,4h,8h,1d").split(",") if t in TF_MS)
CONF_MIN = int(os.environ.get("CONF_MIN", "3"))   # temporalidades con señal activa del mismo lado para avisar
CONF_COOL = 4 * 3_600_000                          # el mismo aviso no se repite antes de 4 h (salvo que sumen más temporalidades)
MAJORS = ("BTCUSDT", "ETHUSDT")                   # se siguen de cerca: todas las temporalidades, aviso desde 2 y cambios de tendencia
MAJ_FLIP_TFS = ("1h", "4h", "1d")
# v5.3 · CALIDAD: la Tendencial de la temporalidad mayor y la de BTC en esa misma temporalidad a favor de la señal.
# Backtest 30 monedas (2h/4h/8h, comisiones incluidas): calidad A factor 1.41 · 51 % acierto (21 de 30 monedas en positivo)
# frente a 0.75 · 37 % del resto. En 15m/1h no mejora (ahí solo se muestra). Filtrar por el winrate pasado de cada moneda NO ayudó.
GRADE_HTF = {"1m": "15m", "3m": "15m", "5m": "1h", "15m": "1h", "1h": "4h", "2h": "8h", "4h": "1d", "8h": "1d"}
QA_TFS = tuple(t for t in os.environ.get("QA_TFS", "2h,4h,8h").split(",") if t in GRADE_HTF)
QA_REF = "BTCUSDT"
# v5.4: en esas mismas temporalidades no se abren señales en velas de fin de semana (sábado/domingo UTC, poca liquidez).
# Backtest 30 monedas 2h+4h+8h: factor 1.38 → 1.51 y más ganancia total (97.6 R → 111.3 R), 23 de 30 monedas en positivo.
# 1h pierde en el backtest con cualquier filtro probado (mejor caso factor 0.96): se avisa en el mensaje.
WEAK_TF = {"1h": "1h pierde en el backtest (factor 0.9): mejor 2h/4h/8h"}


def tf_mode(tf, st):
    """Auditoría v3.6 (24 monedas, comisiones de Bitunix):
    5m  → límite + a favor de la Tendencial de 15m (+0.20 R/op) · 4h → límite (+0.23 R/op)
    1m/3m → modo preciso (a favor de 15m y 4h, DCA 10/30/60) · 15m/1h → DCA 20/30/50 a mercado."""
    be = tf in BE_TFS  # SL a la entrada tras TP1 (backtest v4.6/4.7: mejora 15m, 4h y 1d; en 1h, 2h y 8h empeora)
    # v4.7: 2h y 8h → DCA 20/30/50 a mercado (backtest 48 monedas: factor 2h 1.06 · 8h 1.42); 1d → DCA + SL a la entrada
    if st.get("limit", True) and tf in ("5m", "4h"):
        return dict(mode="limit", W=LIM_W, min_dist=MIN_DIST, htf=["15m"] if tf == "5m" else [], be=be)
    if st.get("h4", True) and TF_MS.get(tf, 0) <= 300_000:
        return dict(mode="low", W=LOW_W, min_dist=LOW_MIN_DIST, htf=["4h", "15m"], be=be)
    return dict(mode="dca", W=DCA_W, min_dist=MIN_DIST, htf=[], be=be)


# ----------------------------- SEÑALES + GESTIÓN (copia exacta del motor del indicador) -----------------------------
def simulate(d, I, capital, risk_pct, dirs=None, W=DCA_W, min_dist=None, be=False, no_wknd=False):
    """Señales y gestión vela a vela. dirs = Tendencial de temporalidades mayores que debe ir a favor.
    W[0] == 0 → modo límite: no se entra a mercado; si el precio llega al TP1 sin llenar E2, la orden se cancela.
    Contabilidad v3.6: cada TP cierra un % de lo que REALMENTE está abierto; ganada = cierre con ganancia neta.
    be=True (v4.6): al tocar TP1 el SL pasa a la entrada (+0.08 % para cubrir comisiones) y se cancelan E2/E3 pendientes."""
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
        z = ((b["l"] - st[i]) / atr[i] if up else (st[i] - b["h"]) / atr[i]) if atr[i] else float("inf")
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
        ok_wk = not (no_wknd and time.gmtime(b["ct"] / 1000).tm_wday >= 5)  # v5.4: sin entradas en velas de sábado/domingo (UTC)
        sig = c1 and not fired and score >= MIN_TRADE and ok_ema and ok_htf and ok_wk
        if sig:
            fired = True
        new_side = side if sig else 0
        last = dict(pts=score if c1 else 0, c=(c1, c2, c3))

        # gestión de la operación abierta
        close_now, exit_px, reason, exit_fee = False, None, "", FEE_TAKER
        if T and i > T["bar"]:
            s = T["side"]
            empty = T["pos"] == 0
            dca_ok = not (be and T["tp"] >= 1)  # tras el TP1 (modo BE) ya no se compran más E2/E3
            if dca_ok and T["fills"] == 1 and (b["l"] <= T["e2"] if s == 1 else b["h"] >= T["e2"]):
                T["avg"] = (T["avg"] * T["pos"] + T["e2"] * W[1]) / (T["pos"] + W[1]); T["pos"] += W[1]; T["fills"] = 2
                T["fees"] += W[1] * FEE_MAKER
            if dca_ok and T["fills"] == 2 and (b["l"] <= T["e3"] if s == 1 else b["h"] >= T["e3"]):
                T["avg"] = (T["avg"] * T["pos"] + T["e3"] * W[2]) / (T["pos"] + W[2]); T["pos"] += W[2]; T["fills"] = 3
                T["fees"] += W[2] * FEE_MAKER
            if empty and T["pos"] > 0:
                events.append(dict(kind="FILL", bar=i, entry=T["bar"], side=s, price=T["avg"], fills=T["fills"]))
            if T["pos"] == 0 and ((b["h"] >= T["tps"][0]) if s == 1 else (b["l"] <= T["tps"][0])):
                events.append(dict(kind="CANCEL", bar=i, entry=T["bar"], side=s, reason="tp1"))
                T = None
                continue  # orden límite sin llenar y el precio ya llegó al TP1 → cancelada
            if (b["l"] <= T["sl"]) if s == 1 else (b["h"] >= T["sl"]):
                close_now, exit_px, reason = True, T["sl"], "be" if T.get("moved") else "stop"
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
                                events.append(dict(kind="TP1", bar=i, entry=T["bar"], side=s, price=px, q=T["q"], be=be))
                                if be:
                                    T["sl"], T["moved"] = T["avg"] * (1 + s * 0.0008), True
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
        if T and i > T["bar"] and T["pos"] > 0 and ((T["side"] == 1 and last_xdn == i) or (T["side"] == -1 and last_xup == i)):
            events.append(dict(kind="WEAK", bar=i, entry=T["bar"], side=T["side"], price=b["c"]))  # debilidad: WaveTrend gira en contra
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
_tg_skew = [0.0]   # reloj de Telegram − reloj de este equipo (para comparar el mensaje fijado sin depender de la hora local)


def tg_now():
    return time.time() + _tg_skew[0]


def tg(token, method, params=None, timeout=20):
    """Llamada a Telegram. Si Telegram pide esperar (429) o falla la conexión, reintenta en vez de perder el aviso."""
    data = urllib.parse.urlencode(params).encode() if params else None
    for attempt in range(3):
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode())
            res = out.get("result") if isinstance(out, dict) else None
            stamp = (res.get("edit_date") or (res.get("date") if method == "sendMessage" else None)) if isinstance(res, dict) else None
            if stamp and abs(stamp - time.time()) < 300:  # reloj de Telegram (solo de algo que acaba de pasar)
                _tg_skew[0] = stamp - time.time()
            return out
        except urllib.error.HTTPError as e:
            try:
                j = json.loads(e.read().decode())
            except Exception:
                j = {}
            wait = (j.get("parameters") or {}).get("retry_after")
            if e.code == 429 and attempt < 2:
                time.sleep(min(int(wait or 5), 30) + 0.5)
                continue
            raise RuntimeError(f"Telegram {method} {e.code}: {j.get('description', '')}") from None
        except urllib.error.URLError as e:  # sin conexión momentánea (no llegó a Telegram): reintentar
            if attempt < 2 and not isinstance(getattr(e, "reason", None), socket.timeout):
                time.sleep(2 + 3 * attempt)
                continue
            raise


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
    tv = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240", "8h": "480",
          "1d": "D"}.get(tf, "15")
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


def hm_local(ms):
    """Hora local (TZ_HOURS) del cierre de la vela de la señal."""
    return time.strftime("%H:%M", time.gmtime(ms / 1000 + TZ_H * 3600))


def message(sym, tf, ev, extra=None, scale=1.0):
    """Mensajes de Telegram. scale = riesgo de este usuario / riesgo del cálculo."""
    extra = extra or {}
    s = "LONG" if ev["side"] == 1 else "SHORT"
    icon = "🟢" if ev["side"] == 1 else "🔴"
    pair, TF = sym + ".P", tf.upper()
    link = f'<a href="{chart_url(sym, tf)}">{pair} {TF}</a>'
    k = ev["kind"]
    if k == "OPEN":  # v5.3: detallado y compacto · winrate y calidad siempre visibles
        e1 = ev["price"]
        lim = ev.get("limit")
        c = coin(sym)
        W = ev.get("w") or DCA_W
        q1, q2, q3 = (fq(x * scale) for x in ev["qty"])
        avg_all = e1 * W[0] + ev["e2"] * W[1] + ev["e3"] * W[2]
        pa = lambda x: f"{ev['side'] * (x - avg_all) / avg_all * 100:+.1f}%"   # + a favor / − en contra, desde tu precio medio
        pe = lambda x: f"{(x - e1) / e1 * 100:+.1f}%"
        margin = sum(ev.get("margin") or [0]) * scale
        risk = ev["risk"] * scale
        cc = ev.get("c") or (True, False, False)
        why = " · ".join(f"{n} {'✓' if ok else '✗'}" for n, ok in zip(("Tend", "Mom", "WT"), cc))
        g, gc = ev.get("g"), ev.get("gc") or []
        if gc:
            why += " · " + " · ".join(f"{n} {'✓' if ok else '✗'}" for n, ok in zip((GRADE_HTF.get(tf, ""), "BTC"), gc))
        gtx = "" if not g else " · ⭐ <b>Calidad A</b>" if g == "A" else f" · Calidad {g}"
        bar = "━━━━━━━━━━━━"
        lines = [f"{icon} <b>{s} · {c} {tf}</b> · {ev['q']}/3{gtx}" + (f" · {extra['hm']}" if extra.get("hm") else ""),
                 f"{link} · {why}",
                 extra.get("wr") or "🏆 Winrate: calculando historial…"]
        if extra.get("late"):
            lines.append(f"⏱ <i>de hace {extra['late']} min" + (f" · ahora {money(extra['now_px'])}" if extra.get("now_px") else "") + "</i>")
        if extra.get("tradfi"):
            lines.append(f"🏛 máx. {TRADFI_MAX_LEV}x" + (" · bolsa cerrada" if extra.get("night") else ""))
        lines.append(bar)
        if lim:
            lines.append(f"💲 Ahora {money(e1)} · <b>solo órdenes límite</b>")
        else:
            lines.append(f"💰 E1 {money(e1)} · {q1}")
        lines += [f"📥 E2 {money(ev['e2'])} ({pe(ev['e2'])}) · {q2}",
                  f"📥 E3 {money(ev['e3'])} ({pe(ev['e3'])}) · {q3}",
                  f"🛑 SL {money(ev['sl'])} ({pa(ev['sl'])}) · <b>−${risk:,.0f}</b> · ⚡<b>{ev['lev']}x</b>",
                  *[f"🎯 TP{k} {money(t)} ({pa(t)})" for k, t in enumerate(ev['tps'], 1)],
                  bar]
        ctx_l = [f"⚖️ Medio {money(avg_all)} · margen ${margin:,.0f}"]
        if extra.get("dirs"):
            fav = sum(1 for _, up_ in extra["dirs"] if up_ == (ev["side"] == 1))
            ctx_l.append(f"📈 {fav}/{len(extra['dirs'])} TF a favor")
        if "open_n" in extra:
            ctx_l.append(f"🧯 {extra.get('open_n', 0) + 1}" + (f"/{extra['max_ops']}" if extra.get("max_ops") else "") + " abiertas")
        lines.append(" · ".join(ctx_l))
        if extra.get("conf"):
            lines.append(f"🔥 También {s}: {' · '.join(extra['conf'])}")
        if extra.get("same"):
            lines.append(f"⚠️ Ya tienes {c} en {', '.join(extra['same'])}")
        if tf in WEAK_TF:
            lines.append(f"⚠️ <i>{WEAK_TF[tf]}</i>")
        tips = (["sin llenar en TP1 → cancela"] if lim else []) + (["TP1 → SL a entrada"] if tf in BE_TFS else [])
        if tips:
            lines.append("📋 <i>" + " · ".join(tips) + "</i>")
        return "\n".join(lines)
    hdr = f"{link} · {icon} <b>{s}</b>"
    if k == "FILL":
        return "\n".join([f"📥 <b>ORDEN LLENADA · E{ev.get('fills', 2)}</b>", hdr,
                          f"💰 Precio medio ahora: {money(ev['price'])}", "", "✅ <i>Ya estás dentro: el SL y los TP siguen igual</i>"])
    if k == "CANCEL":
        why = "el precio llegó al TP1 sin tocar tu entrada" if ev.get("reason") == "tp1" else "apareció la señal contraria"
        return "\n".join(["❌ <b>ORDEN CANCELADA</b>", hdr,
                          f"ℹ️ {why}.", "", "🗑 <b>Cancela tus órdenes límite E2/E3 de esta señal</b>"])
    if k == "WEAK":
        return "\n".join(["⚠️ <b>DEBILIDAD</b>", hdr, f"💲 Precio: {money(ev['price'])}", "",
                          "📉 WaveTrend giró en contra en zona extrema: la operación pierde fuerza." if ev["side"] == 1 else
                          "📈 WaveTrend giró en contra en zona extrema: la operación pierde fuerza.",
                          "💡 <i>Asegura ganancias: cierra una parte o sube el SL a la entrada</i>"])
    if k == "TP1":
        return "\n".join(["✅ <b>TP1 ALCANZADO</b>", hdr,
                          f"🎯 Precio: {money(ev['price'])}", "",
                          "🔒 <b>Mueve el SL a la entrada</b> y cancela las órdenes E2/E3 que no se llenaron" if ev.get("be") else
                          "💡 <i>Cierra una parte y deja correr el resto</i>",
                          "💡 <i>El 40 % ya se cerró en TP1; el resto va a TP2/TP3 sin riesgo</i>" if ev.get("be") else ""])
    if k == "SL":
        return "\n".join(["⛔ <b>STOP LOSS TOCADO</b>", hdr,
                          f"🛟 Precio: {money(ev['price'])}", "", "ℹ️ <i>Si tenías el SL puesto, la operación ya se cerró.</i>",
                          "<i>El resultado en $ llega al cerrar la vela.</i>"])
    if k == "WKND":
        return "\n".join(["⚠️ <b>CIERRE DE FIN DE SEMANA</b>", hdr, "",
                          "🏛 El mercado real (bolsa / materias primas) cierra pronto hasta el domingo-lunes.",
                          "📉 Al reabrir el precio puede saltar por encima de tu SL.",
                          "💡 <i>Considera cerrar o reducir esta operación antes del cierre.</i>"])
    res = {"tp3": "🏁 TP3 alcanzado", "stop": "⛔ Stop loss" if not ev.get("tp") else "🏁 Resto cerrado en SL tras TP",
           "reverse": "🔄 Señal contraria", "be": "🔒 Resto cerrado en la entrada tras TP1"}[ev["reason"]]
    pnl = ev["pnl"] * scale
    lines = [f"{'🏁' if pnl >= 0 else '🩸'} <b>OPERACIÓN CERRADA · {'GANADA' if pnl >= 0 else 'PERDIDA'}</b>", hdr,
             f"📌 Motivo: {res}" + (f" · salida {money(ev['price'])}" if ev.get("price") else ""),
             f"{'💰' if pnl >= 0 else '🩸'} Resultado: <b>{'+' if pnl >= 0 else '-'}${abs(pnl):,.2f}</b> ({ev['r']:+.2f} R)",
             "<i>Operación terminada · espera la próxima señal</i> 🚢"]
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
        v = float(s.replace(",", "."))
        return v if math.isfinite(v) else None   # "nan" / "inf" no son números válidos
    except ValueError:
        return None



# ----------------------------- ESTADO -----------------------------
CLOUD = os.environ.get("CLOUD") == "1"
MAX_OPS = int(os.environ.get("MAX_OPS", "0"))   # máximo de operaciones abiertas por usuario (0 = sin límite, v4.9.1; /maxops)
_st_lock = threading.Lock()


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
    st.setdefault("calidad", "A")
    if not st.get("v54"):  # v5.4: 2h/4h/8h sin fin de semana → winrate nuevo para esas temporalidades
        for p in list(st.get("hist", {})):
            if p.endswith(tuple("_" + t for t in QA_TFS)):
                del st["hist"][p]
        st["hist_seeded"] = [p for p in st.get("hist_seeded", []) if not p.endswith(tuple("_" + t for t in QA_TFS))]
        st["v54"] = True
    if not st.get("v53"):  # v5.3: 2h/4h/8h solo calidad A → winrate nuevo para esas temporalidades
        for p in list(st.get("hist", {})):
            if p.endswith(tuple("_" + t for t in QA_TFS)):
                del st["hist"][p]
        st["hist_seeded"] = [p for p in st.get("hist_seeded", []) if not p.endswith(tuple("_" + t for t in QA_TFS))]
        st["v53"] = True
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
    st.setdefault("removed", [])   # monedas quitadas con /quitar (favoritos.txt no las vuelve a poner)
    st.setdefault("max_ops", MAX_OPS)
    if not st.get("v491"):  # v4.9.1: se envían TODAS las señales (el usuario decide); el límite queda opcional con /maxops
        st["max_ops"] = MAX_OPS
        for u in st.get("users", {}).values():
            u.pop("max_ops", None)
        st["v491"] = True
    if not st.get("v46"):  # v4.6: control de operaciones abiertas → las anteriores (sin vigilancia al minuto) cuentan como cerradas
        for p in list(st.get("hist", {})):  # 15m y 4h cambian de regla (SL a la entrada tras TP1): winrate nuevo
            if p.endswith(("_15m", "_4h")):
                del st["hist"][p]
        st["hist_seeded"] = [p for p in st.get("hist_seeded", []) if not p.endswith(("_15m", "_4h"))]
        live_ids, sent0 = set(st.get("live", {})), set(st.get("sent", []))
        for tid, tr in st.get("trades", {}).items():
            if f"{tid}_CLOSE" in sent0 or f"{tid}_CANCEL" in sent0:
                tr["closed"] = True
            elif tid not in live_ids:
                tr["legacy"] = True  # de la versión anterior: sigue recibiendo su cierre, pero no bloquea señales nuevas
        st["v46"] = True
    fav = os.path.join(BASE, "favoritos.txt")  # monedas extra para la PC: una lista separada por comas o líneas
    if not CLOUD and os.path.exists(fav):
        with open(fav, encoding="utf-8") as f:
            for x in re.split(r"[,\s]+", re.sub(r"#[^\n]*", "", f.read())):  # todo lo que sigue a # es comentario
                x = norm_symbol(x) if x.strip() else ""
                if x and x not in st["symbols"] and x not in st["removed"]:
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


def keep_ms(tf):
    """Cuánto tiempo se recuerda una señal: 7 días, o 120 velas en 8h/1d (esas operaciones duran semanas)."""
    return max(7 * 86_400_000, 120 * TF_MS.get(tf, 0))


def save_state(st, now):
    def keep(k, t):
        parts = k.split("_")
        return t >= now - keep_ms(parts[1] if len(parts) > 2 else "")
    st["sent"] = sorted(k for k in set(st["sent"]) if keep(k, int(k.split("_")[-2])))
    st["trades"] = {k: v for k, v in st.get("trades", {}).items() if keep(k, v.get("t", 0))}
    with _st_lock:  # el latido (otro hilo) también escribe en el estado
        data = dict(st)
    if CLOUD:  # el repositorio es público: no se guardan datos de usuarios
        data.pop("users", None)
        data.pop("pending", None)
        data.pop("journal", None)
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


def conf_symbols(st):
    """Monedas para las confluencias: BTC y ETH siempre (si alguien los sigue) + las favoritas (si alguien quiere confluencias)."""
    if CLOUD:
        return []
    us = list(st["users"].values())
    out = list(MAJORS) if any(u.get("maj", True) or u.get("conf", True) for u in us) else []
    if any(u.get("conf", True) for u in us):
        out += [s for s in st["symbols"] if s not in out]
    return out


def all_pairs(st):
    """Gráficos a revisar: las temporalidades elegidas por los usuarios + todas las de confluencia en favoritas, BTC y ETH."""
    base = [(s, t) for s in active_symbols(st) for t in scan_tfs(st)]
    return list(dict.fromkeys(base + [(s, t) for s in conf_symbols(st) for t in CONF_TFS]))


def chat_of(uid, owner):
    return owner if uid == "owner" else uid


def uid_of(chat_id, owner):
    return "owner" if str(chat_id) == str(owner) else str(chat_id)


def user_scale(st, uid):
    u = st["users"].get(uid, {})
    mine = u.get("capital", st["capital"]) * u.get("risk", st["risk"]) / 100
    base = st["capital"] * st["risk"] / 100
    return mine / base if base else 1.0


def max_ops_of(st, uid):
    return int(st["users"].get(uid, {}).get("max_ops", MAX_OPS))  # v4.9.4: el /maxops del admin ya no se aplica a los amigos


def open_trades(st, uid, now=None):
    """Operaciones de este usuario que siguen abiertas (o con órdenes límite pendientes)."""
    now = now or int(time.time() * 1000)
    out = []
    for tid, tr in st.get("trades", {}).items():
        if tr.get("closed") or tr.get("cancelled") or tr.get("sl_hit") or tr.get("legacy") or uid not in tr.get("to", []):
            continue
        sym, tf, _ = tid.rsplit("_", 2)
        if now - tr.get("t", 0) > 300 * TF_MS.get(tf, 900_000):  # demasiado vieja (seguridad)
            continue
        out.append((tid, sym, tf))
    return out


def settings_line(st, uid="owner"):
    u = st["users"].get(uid, {})
    cap, rk = u.get("capital", st["capital"]), u.get("risk", st["risk"])
    mx = max_ops_of(st, uid)
    line = (f"💰 ${cap:,.0f} · riesgo {rk:g}% = ${cap * rk / 100:,.2f} por operación\n"
            + (f"🧯 Máx. {mx} operaciones abiertas (riesgo total ${cap * rk / 100 * mx:,.0f})\n" if mx else "🧯 Recibes todas las señales (sin límite; /maxops 5 para limitar)\n") +
            f"⏱ Tus temporalidades: {', '.join(u.get('tfs', [])) or 'ninguna (pausado)'}\n"
            f"🔥 Confluencias: {'sí' if u.get('conf', True) else 'no'} · ₿ BTC/ETH de cerca: {'sí' if u.get('maj', True) else 'no'}")
    if uid == "owner":
        line += (f"\n🛡 SL mínimo {MIN_DIST:g}% · 📥 modo límite 5m/4h: {'sí' if st.get('limit', True) else 'no'}"
                 f" · 🎯 modo preciso 1m/3m: {'sí' if st['h4'] else 'no'} · ⭐ calidad 2h/4h/8h: {'solo A' if st.get('calidad', 'A') == 'A' else 'todas'}"
                 f" · monedas: favoritas + top {st.get('top', 0)}")
    return line


# ----------------------------- USUARIOS Y CONFIGURACIÓN DESDE TELEGRAM -----------------------------
HELP_USER = ("🤖 <b>Bridge Táctico · comandos</b>\n"
             "/config → elige tus temporalidades con botones\n"
             "/capital 500 → tu capital en $ (para calcular cantidades)\n"
             "/riesgo 1 → % de riesgo por operación\n"
             "/maxops 5 → limitar a 5 operaciones abiertas (0 = todas, por defecto)\n"
             "/winrate → acierto real de cada moneda y temporalidad\n"
             "/stats → tu diario: resultado, factor, caída máxima, rachas y calendario (/stats 7 · 90 · todo)\n"
             "/radar → tendencia de las monedas favoritas\n"
             "/btc → BTC y ETH de cerca: tendencia y señales en cada temporalidad\n"
             "/estado → operaciones activas\n"
             "/stop → dejar de recibir señales\n"
             "/ayuda → este mensaje")
HELP_OWNER = (HELP_USER + "\n\n👑 <b>Administrador</b>\n"
              "/usuarios → quién recibe señales · /expulsar ID\n"
              "/agregar SOL XRP · /quitar SOL · /lista → monedas favoritas\n"
              "/top 30 → además vigila las 30 monedas con más volumen (0 = solo favoritas)\n"
              "/limite si|no → 5m y 4h con órdenes límite (recomendado)\n"
              "/h4 si|no → modo preciso 1m/3m (a favor de 15m y 4h)\n"
              "/calidad A|todas → 2h/4h/8h solo señales calidad A y sin fin de semana (recomendado)\n"
              "/radar cada 30 · /radar off → radar automático\n"
              "/nube → cómo funciona el respaldo en GitHub\n"
              "/actualizar → el servidor instala la última versión\n\n"
              "👥 Para compartir: pásale a tu amigo el enlace del bot. Al pulsar INICIAR te llega una solicitud para aprobarla.")


def config_rows(u):
    mark = lambda t: ("✅ " if t in u.get("tfs", []) else "▫️ ") + t
    return [[(mark(t), "tf:" + t) for t in ("1m", "3m", "5m")],
            [(mark(t), "tf:" + t) for t in ("15m", "1h", "2h")],
            [(mark(t), "tf:" + t) for t in ("4h", "8h", "1d")],
            [(("✅ " if u.get("conf", True) else "▫️ ") + "🔥 Confluencias", "cf:conf"),
             (("✅ " if u.get("maj", True) else "▫️ ") + "₿ BTC/ETH de cerca", "cf:maj")],
            [("✔️ Listo", "cfg:done")]]


CONFIG_TXT = ("⚙️ <b>Tus avisos</b>\nToca una temporalidad para activarla ✅ o quitarla ▫️.\n"
              "🔥 Confluencias: una moneda con LONG o SHORT activo en 3+ temporalidades (de 3m a 1d) · "
              "₿ BTC/ETH: aviso desde 2 temporalidades y cuando cambia su tendencia de 1h, 4h o 1d.\n"
              "Auditoría v4.7 (48 monedas, datos reales, con comisiones · factor &gt; 1 = gana):\n"
              "🥇 <b>8h</b> 1.42 · <b>2h</b> 1.06 · <b>4h</b> 1.02 · <b>15m</b> 1.01 · 1h 0.98\n"
              "⚠️ 1d 0.86 · 1m 0.93 · 3m 0.72 · 5m 0.73: pierden, úsalas con riesgo bajo.")


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
            tg(token, "editMessageText", {"chat_id": cid, "message_id": mid, "parse_mode": "HTML", "text": f"✅ {name} aprobado"})
        else:
            try:
                send(token, who, "❌ Tu solicitud no fue aprobada.")
            except Exception:
                pass
            tg(token, "editMessageText", {"chat_id": cid, "message_id": mid, "parse_mode": "HTML", "text": f"❌ {name} rechazado"})
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
    elif data.startswith("cf:") and uid in st["users"] and data[3:] in ("conf", "maj"):
        k = data[3:]
        st["users"][uid][k] = not st["users"][uid].get(k, True)
        note = ("Confluencias " if k == "conf" else "BTC/ETH ") + ("activado" if st["users"][uid][k] else "desactivado")
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
        name = html.escape(name[:60])  # un nombre con < o & rompería el mensaje y no te llegaría la solicitud
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
    elif cmd == "/maxops":
        v = parse_num(args)
        if v is not None and 0 <= v <= 100:
            me["max_ops"] = int(v)
        reply(settings_line(st, uid))
    elif cmd == "/winrate":
        reply(winrate_report(st, uid))
    elif cmd == "/calidad" and is_owner:
        if args:
            new = "A" if args[0].lower() in ("a", "si", "sí", "on", "1") else "todas"
            if new != st.get("calidad", "A"):
                st["calidad"] = new
                for p in list(st.get("hist", {})):  # cambia la regla: winrate nuevo de 2h/4h/8h
                    if p.endswith(tuple("_" + t for t in QA_TFS)):
                        del st["hist"][p]
                st["hist_seeded"] = [p for p in st.get("hist_seeded", []) if not p.endswith(tuple("_" + t for t in QA_TFS))]
        reply("⭐ Calidad en " + ", ".join(QA_TFS) + ": " + ("<b>solo A</b> (Tendencial mayor y BTC a favor · sin fin de semana · backtest: factor 1.51 vs 0.75)" if st.get("calidad", "A") == "A" else "todas las señales") + "\n/calidad A · /calidad todas")
    elif cmd in ("/btc", "/eth"):
        reply(majors_report(st, ctx))
    elif cmd in ("/stats", "/diario"):
        a = args[0].lower() if args else ""
        v = parse_num(args) if args and a not in ("todo", "all", "total") else None
        days = 0 if a in ("todo", "all", "total") else int(min(max(v, 1), 3650)) if v else 30
        reply(stats_report(st, uid, days))
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
            if s in st["removed"]:
                st["removed"].remove(s)
            if s not in st["symbols"]:
                st["symbols"].append(s)
                added.append(s)
        reply("⭐ Agregado: " + (", ".join(added) if added else "nada nuevo") + "\nFavoritos: " + ", ".join(st["symbols"]))
    elif cmd in ("/quitar", "/remove"):
        for a in args:
            s = norm_symbol(a)
            if s in st["symbols"]:
                st["symbols"].remove(s)
            if s not in st["removed"]:
                st["removed"].append(s)
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
    elif cmd == "/actualizar":
        if os.environ.get("INVOCATION_ID"):  # servidor con systemd: al salir se reinicia solo y baja la última versión de GitHub
            reply(f"♻️ [{HOST}] reiniciando para instalar la última versión… (vuelve en 1-2 min)")
            raise KeyboardInterrupt
        reply("ℹ️ /actualizar solo funciona en el servidor. En la PC cierra la ventana y vuelve a abrir iniciar_bot.bat.")
    elif cmd == "/nube":
        reply(f"☁️ <b>Quién envía las señales</b>\nAhora: [{HOST}]{' ★' if MAIN else ''} · v{VERSION}. Solo un bot envía a la vez (el mensaje fijado 🟢 "
              "dice cuál). El servidor 24/7 (★) tiene prioridad; si se apaga, toma el control la PC en 4 min. "
              "Si no hay ninguno, GitHub avisa 15m, 1h y 4h solo al administrador y con 5-30 min de retraso.")


def clip_lines(lines, limit=3800):
    """Junta líneas completas hasta ~3800 caracteres (Telegram rechaza > 4096 o una etiqueta HTML cortada)."""
    out, n = [], 0
    for i, ln in enumerate(lines):
        if n + len(ln) + 1 > limit:
            out.append(f"… y {len(lines) - i} línea(s) más")
            break
        out.append(ln)
        n += len(ln) + 1
    return "\n".join(out)


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
    if len(lines) > 46:
        lines = lines[:46] + [f"… y {len(lines) - 46} más"]
    lines.append(settings_line(st, uid))
    return clip_lines(lines)


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
    return clip_lines(lines[:-1], 3600) + "\n" + lines[-1]


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
    return clip_lines(lines[:-1], 3600) + "\n" + lines[-1]


# ----------------------------- PROCESO -----------------------------
def _stats(vals):
    n = len(vals)
    if not n:
        return {"n": 0, "wr": 0.0, "avg": 0.0}
    return {"n": n, "wr": 100.0 * sum(v[0] for v in vals) / n, "avg": sum(v[1] for v in vals) / n}


def hist_stats(st, pair, side=None):
    vals = list(st.get("hist", {}).get(pair, {}).values())
    if side:
        vals = [v for v in vals if len(v) > 2 and v[2] == side]
    return _stats(vals)


def tf_stats(st, tf):
    """v5.3: winrate de una temporalidad con TODAS las monedas (backtest de 3000 velas + operaciones reales)."""
    return _stats([v for p, h in st.get("hist", {}).items() if p.endswith("_" + tf) for v in h.values()])


def wr_text(st, sym, tf):
    """🏆 Winrate 2h: 51 % (547 ops) · ETH: 58 % (24)"""
    t, h = tf_stats(st, tf), hist_stats(st, f"{sym}_{tf}")
    parts = []
    if t["n"] >= 10:
        parts.append(f"<b>Winrate {tf}: {t['wr']:.0f}%</b> ({t['n']} ops)")
    if h["n"] >= 5:
        parts.append(f"{coin(sym)}: {h['wr']:.0f}% ({h['n']})")
    return "🏆 " + (" · ".join(parts) if parts else "Winrate: calculando historial…")


# ----------------------------- CONFLUENCIAS Y BTC/ETH (v4.9) -----------------------------
def conf_active(sym, ctx):
    """Temporalidades con operación activa (✓) de cada lado y cuántas Tendenciales están alcistas."""
    act, ups, n = {1: [], -1: []}, 0, 0
    for tf in CONF_TFS:
        c = ctx.get((sym, tf))
        if not c:
            continue
        n += 1
        ups += 1 if c["up"] else 0
        T = c.get("T")
        if T:
            act[T["side"]].append(tf)
    return act, ups, n


def dirs_line(sym, ctx, tfs=CONF_TFS):
    return " ".join(f"{t}{'▲' if ctx[(sym, t)]['up'] else '▼'}" for t in tfs if (sym, t) in ctx)


def conf_message(st, ctx, sym, side, tfs, newest, ups, n, scale=1.0):
    """v5.3: compacta, con winrate de cada temporalidad y calidad: dirección clara, plan de la señal más reciente y una línea con cada temporalidad."""
    s, icon = ("LONG", "🟢") if side == 1 else ("SHORT", "🔴")
    c_ = coin(sym)
    fav = ups if side == 1 else n - ups
    c = ctx[(sym, newest)]
    T = c["T"]
    age = max(0, int((time.time() * 1000 - c["t_open"]) // 60_000))
    lev = min(T["lev"], TRADFI_MAX_LEV) if tradfi_kind(sym) else T["lev"]
    link = f"<a href=\"{chart_url(sym, newest)}\">{sym}.P</a>"
    qty = T.get("qty") or [0, 0, 0]
    avg_c = sum(q * e for q, e in zip(qty, (T["e1"], T["e2"], T["e3"]))) / sum(qty) if sum(qty) > 0 else T["e1"]
    pa = lambda x: f"{side * (x - avg_c) / avg_c * 100:+.1f}%"
    risk = st.get("capital", 0) * st.get("risk", 0) / 100 * scale
    bar = "━━━━━━━━━━━━"
    wrs = []
    for t in tfs:
        h, tt = hist_stats(st, f"{sym}_{t}"), tf_stats(st, t)
        x = h if h["n"] >= 5 else tt
        wrs.append(f"{t} {x['wr']:.0f}%" if x["n"] >= 5 else f"{t} —")
    g = T.get("g")
    lines = [f"🔥{icon} <b>CONFLUENCIA {s} · {c_}</b>",
             f"{link} · <b>{len(tfs)} TF en {s}</b> · Tendencial {fav}/{n} a favor",
             "🏆 <b>Winrate</b> " + " · ".join(wrs),
             bar, f"🆕 Señal {newest} · {T['q']}/3" + (" · ⭐ <b>Calidad A</b>" if g == "A" else f" · Calidad {g}" if g else "") + f" · hace {age} min"]
    # v5.2: entradas y TP en vertical, con ✓ cuando ya se tocaron
    fl = T.get("fills", 1)
    if qty[0] > 0:
        lines.append(f"💰 E1 {money(T['e1'])} · {fq(qty[0] * scale)}" + (" ✓" if T["pos"] > 0 else ""))
    lines.append(f"📥 E2 {money(T['e2'])} · {fq(qty[1] * scale)}" + (" ✓" if fl >= 2 else ""))
    lines.append(f"📥 E3 {money(T['e3'])} · {fq(qty[2] * scale)}" + (" ✓" if fl >= 3 else ""))
    lines.append(f"🛑 SL {money(T['sl'])} ({pa(T['sl'])})" + (f" · −${risk:,.0f}" if risk else "") + f" · ⚡{lev}x")
    lines += [f"🎯 TP{k} {money(t)} ({pa(t)})" + (" ✓" if T.get("tp", 0) >= k else "") for k, t in enumerate(T["tps"], 1)]
    lines.append(bar)
    det = []
    for t in tfs:
        Tt = ctx[(sym, t)]["T"]
        h = hist_stats(st, f"{sym}_{t}")
        est = "límite" if Tt["pos"] == 0 else f"TP{Tt['tp']}✓" if Tt.get("tp") else "en curso"
        det.append(f"{t} {Tt['q']}/3 {est}" + (" A" if Tt.get("g") == "A" else ""))
    lines.append("📋 " + " · ".join(det))
    lines.append("📊 <i>3+ TF a la vez: el backtest gana más. Usa SL.</i>" if len(tfs) >= CONF_MIN else f"₿ <i>{c_}: solo 2 TF, tómalo como contexto.</i>")
    return "\n".join(lines)


def confluence(token, owner, st, ctx, notify=True):
    """Avisa cuando una moneda suma LONG (o SHORT) activo en varias temporalidades a la vez.
    Solo si la última señal es reciente (máx. 3 velas) y sin repetir el mismo aviso antes de 4 h."""
    mem = st.setdefault("conf", {})
    now = int(time.time() * 1000)
    st["conf_t"] = now
    for sym in conf_symbols(st):
        act, ups, n = conf_active(sym, ctx)
        if n < len(CONF_TFS) - 1:
            continue  # faltan datos de esta moneda (arranque o fuente caída): no decidir nada
        major = sym in MAJORS
        need = 2 if major else CONF_MIN
        for side in (1, -1):
            tfs, key = act[side], f"{sym}_{side}"
            m = mem.setdefault(key, {"n": 0, "sent_n": 0, "sent_t": 0})
            prev, joined = m["n"], [t for t in tfs if t not in m.get("tfs", [])]
            m["n"], m["tfs"] = len(tfs), list(tfs)
            if len(tfs) < need or len(tfs) <= prev:
                continue
            fresh = [t for t in joined if ctx[(sym, t)].get("t_open") and now - ctx[(sym, t)]["t_open"] <= 3 * TF_MS[t] + 120_000]
            if not fresh:
                continue  # la temporalidad que se sumó trae una señal vieja: no sirve para entrar
            newest = max(fresh, key=lambda t: ctx[(sym, t)]["t_open"])
            if len(tfs) <= m["sent_n"] and now - m["sent_t"] < CONF_COOL:
                continue  # ya se avisó hace poco con las mismas (o más) temporalidades
            kind = tradfi_kind(sym)
            if kind and market_closed(kind):
                continue
            m["sent_n"], m["sent_t"] = len(tfs), now
            if not notify:
                continue
            rcpt = [u for u, p in st["users"].items() if p.get("tfs")
                    and ((p.get("conf", True) and len(tfs) >= CONF_MIN) or (major and p.get("maj", True)))]
            ok = 0
            for uid in rcpt:
                try:
                    try:
                        sc_u = user_scale(st, uid)
                    except Exception:
                        sc_u = 1.0
                    txt = conf_message(st, ctx, sym, side, tfs, newest, ups, n, sc_u)
                    send(token, chat_of(uid, owner), txt, signal_buttons(sym, newest))
                    ok += 1
                except Exception as e:
                    print("confluencia:", uid, e)
            print(time.strftime("%H:%M:%S"), "🔥 confluencia", sym, "LONG" if side == 1 else "SHORT", "+".join(tfs), f"→ {ok}")


def majors_watch(token, owner, st, ctx, notify=True):
    """BTC y ETH de cerca: aviso cuando su Tendencial de 1h, 4h o 1d cambia de dirección (contexto de todo el mercado)."""
    mem = st.setdefault("maj_dir", {})
    st["maj_t"] = int(time.time() * 1000)
    rcpt = [u for u, p in st["users"].items() if p.get("maj", True) and p.get("tfs")]
    for sym in MAJORS:
        for tf in MAJ_FLIP_TFS:
            c = ctx.get((sym, tf))
            if not c:
                continue
            k, up = f"{sym}_{tf}", bool(c["up"])
            prev, mem[k] = mem.get(k), up
            if prev is None or prev == up or not notify or CLOUD:
                continue
            txt = "\n".join([f"₿ <b>{coin(sym)} {tf}: la Tendencial cambió a {'ALCISTA ▲' if up else 'BAJISTA ▼'}</b>", "",
                              f"📍 Línea {money(c['st'])} · precio {money(c.get('px') or c['st'])}",
                              f"📈 {coin(sym)}: {dirs_line(sym, ctx)}", "",
                              "ℹ️ <i>Contexto del mercado, no es una señal de entrada. Revisa tus operaciones abiertas "
                              f"{'en SHORT' if up else 'en LONG'}.</i>"])
            for uid in rcpt:
                try:
                    send(token, chat_of(uid, owner), txt, signal_buttons(sym, tf))
                except Exception as e:
                    print("btc/eth:", uid, e)


def majors_report(st, ctx):
    lines = ["₿ <b>BTC y ETH de cerca</b>"]
    for sym in MAJORS:
        act, ups, n = conf_active(sym, ctx)
        if not n:
            lines.append(f"<b>{coin(sym)}</b> … (aún calculando)")
            continue
        px = next((ctx[(sym, t)].get("px") for t in CONF_TFS if (sym, t) in ctx), None)
        lines += ["", f"<b>{coin(sym)}</b> {money(px) if px else ''} · {round(ups / n * 100)}% alcista", dirs_line(sym, ctx)]
        for side, nm in ((1, "🟢 LONG"), (-1, "🔴 SHORT")):
            if act[side]:
                lines.append(f"{nm} activo en: " + " · ".join(f"{t} ({ctx[(sym, t)]['T']['q']}/3)" for t in act[side]))
        if not act[1] and not act[-1]:
            lines.append("Sin señales activas.")
    lines.append("\n▲▼ Tendencial de cada temporalidad (última vela cerrada)")
    return "\n".join(lines)


# ----------------------------- DIARIO (/stats) -----------------------------
TZ_H = float(os.environ.get("TZ_HOURS", "-3"))   # hora local del calendario (Brasil = -3; España = 2 en verano)
JOURNAL_MAX = 3000
SPARK = "▁▂▃▄▅▆▇█"


def journal_add(st, tid, tr, ev, sym, tf, t_close):
    """Guarda una operación cerrada que llegó a los usuarios. r = resultado en R (1 R = pérdida si toca el SL), con comisiones."""
    j = st.setdefault("journal", [])
    j.append({"id": tid, "sym": sym, "tf": tf, "side": ev.get("side", tr.get("side", 0)), "q": ev.get("q", 0), "t": int(t_close),
              "r": round(float(ev.get("r", 0.0)), 3), "why": ev.get("reason", ""), "to": list(tr.get("to", []))})
    if len(j) > JOURNAL_MAX:
        del j[:len(j) - JOURNAL_MAX]


def spark(vals, width=24):
    """Curva de capital en una línea de texto."""
    if not vals:
        return ""
    if len(vals) > width:  # muestreo uniforme (incluye el primero y el último)
        vals = [vals[round(i * (len(vals) - 1) / (width - 1))] for i in range(width)]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7 + 0.5))] for v in vals)


def stats_report(st, uid="owner", days=30, now=None):
    """Diario del usuario con las operaciones que recibió y ya se cerraron."""
    now = now or int(time.time() * 1000)
    since = now - days * 86_400_000 if days else 0
    ops = sorted((o for o in st.get("journal", []) if uid in o.get("to", []) and o.get("t", 0) >= since), key=lambda o: o["t"])
    u = st["users"].get(uid, {})
    risk = u.get("capital", st["capital"]) * u.get("risk", st["risk"]) / 100
    head = f"📒 <b>Tu diario</b> · {f'últimos {days} días' if days else 'todo el historial'}"
    if not ops:
        return (head + "\nAún no hay operaciones cerradas de las señales que recibiste en este periodo.\n"
                "(El diario empezó con la v4.8 y se llena solo con cada cierre.)\n/stats 7 · /stats 90 · /stats todo")
    rs = [o["r"] for o in ops]
    wins, loss = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    net, gp, gl = sum(rs), sum(wins), -sum(loss)
    pf = "∞" if gl <= 0 else f"{gp / gl:.2f}"
    eq, c, peak, mdd = [0.0], 0.0, 0.0, 0.0
    for r in rs:
        c += r
        eq.append(c)
        peak = max(peak, c)
        mdd = max(mdd, peak - c)
    best_w = best_l = cur = 0
    for r in rs:
        cur = (cur + 1 if cur > 0 else 1) if r > 0 else (cur - 1 if cur < 0 else -1)
        best_w, best_l = max(best_w, cur), min(best_l, cur)
    usd = lambda r: ("+" if r >= 0 else "−") + f"${abs(r) * risk:,.0f}"
    lines = [head,
             f"Operaciones: <b>{len(rs)}</b> · ✅ {len(wins)} · ❌ {len(loss)} · acierto <b>{100 * len(wins) / len(rs):.0f}%</b>",
             f"Resultado: <b>{net:+.2f} R</b> ≈ {usd(net)} (con tu riesgo de ${risk:,.0f} por operación)",
             f"Factor: <b>{pf}</b> · R medio {net / len(rs):+.2f} · mejor {max(rs):+.2f} R · peor {min(rs):+.2f} R",
             f"Caída máxima: −{mdd:.2f} R ({usd(-mdd)})",
             f"Racha actual: {f'🔥 {cur} ganadas' if cur > 0 else f'❄️ {-cur} perdidas'} · mejor {best_w} · peor {-best_l}",
             f"Curva: {spark(eq)}"]
    # calendario de los últimos 14 días (hora local)
    off = int(TZ_H * 3_600_000)
    day = lambda t: (t + off) // 86_400_000
    today = day(now)
    by_day = {}
    for o in ops:
        by_day[day(o["t"])] = by_day.get(day(o["t"]), 0.0) + o["r"]
    cells = []
    for k in range(13, -1, -1):
        v = by_day.get(today - k)
        cells.append("⬜" if v is None else "🟩" if v > 0.05 else "🟥" if v < -0.05 else "🟨")
    lines += ["", "📅 <b>Últimos 14 días</b> (🟩 gana · 🟥 pierde · 🟨 ~0 · ⬜ sin cierres)", "".join(cells[:7]) + "  " + "".join(cells[7:])]
    wd = ("lu", "ma", "mi", "ju", "vi", "sá", "do")   # el día 0 (1-1-1970) fue jueves
    recent = [(k, by_day[k]) for k in sorted(by_day) if k > today - 7]
    if recent:
        lines.append(" · ".join(f"{wd[(k + 3) % 7]} {time.gmtime(k * 86400).tm_mday}: {v:+.1f}R" for k, v in recent))

    def group(key):
        g = {}
        for o in ops:
            a = g.setdefault(o.get(key, "?"), [0, 0.0, 0])
            a[0] += 1
            a[1] += o["r"]
            a[2] += 1 if o["r"] > 0 else 0
        return g
    g_tf = sorted(group("tf").items(), key=lambda x: TF_MS.get(x[0], 0))
    lines += ["", "⏱ <b>Por temporalidad</b> (R · operaciones · acierto)",
              " · ".join(f"{tf} {a[1]:+.1f}R ({a[0]}, {100 * a[2] / a[0]:.0f}%)" for tf, a in g_tf)]
    g_sym = sorted(group("sym").items(), key=lambda x: x[1][1], reverse=True)
    best = " · ".join(f"{coin(s)} {a[1]:+.1f}R" for s, a in g_sym[:3] if a[1] > 0)
    worst = " · ".join(f"{coin(s)} {a[1]:+.1f}R" for s, a in g_sym[::-1][:3] if a[1] < 0)
    if best:
        lines.append("🏅 Mejores: " + best)
    if worst:
        lines.append("⚠️ Peores: " + worst)
    lines.append("\nR = resultado en riesgos (1 R = lo que pierdes si toca el SL), comisiones incluidas.\n/stats 7 · /stats 90 · /stats todo")
    return clip_lines(lines)


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
    for t in ALL_TFS:
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


# ----------------------------- MONEDAS: favoritas + las de más volumen -----------------------------
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


def compute(sym, tf, st, want=None):
    """Descarga y calcula una moneda/temporalidad. No toca el estado (se puede ejecutar en paralelo)."""
    pair = f"{sym}_{tf}"
    if want is None:
        want = set(scan_tfs(st))
    d = fetch_klines(sym, tf, 1000 if tf in want else 499)  # solo confluencia: 499 velas (pesa 2 en Binance, no 5)
    if tf == "1m":
        _last1m[sym] = (time.time(), d[-5:])
    I = indicators(d)
    m = tf_mode(tf, st)
    gd = grade_dirs(sym, d, tf)
    qa = st.get("calidad", "A") == "A" and tf in QA_TFS and bool(gd)
    dirs = ([dir_series(sym, d, h) for h in m["htf"]] + (gd if qa else [])) or None
    events, T, last = simulate(d, I, st["capital"], st["risk"], dirs, m["W"], m["min_dist"], m["be"], qa and not tradfi_kind(sym))
    for ev in events:
        if ev["kind"] == "OPEN":
            ev["g"], ev["gc"] = grade_at(gd, ev["bar"], ev["side"])
    if T:
        T["g"], T["gc"] = grade_at(gd, T["bar"], T["side"])
    seed = None
    if pair not in st.get("hist_seeded", []) and tf in want:  # la primera vez: winrate con un historial largo
        try:
            dl = fetch_long(sym, tf, 3000)
            dl_g = grade_dirs(sym, dl, tf, 1500) if qa else []
            dl_dirs = ([dir_series(sym, dl, h, 1500) for h in m["htf"]] + dl_g) or None
            evl, _, _ = simulate(dl, indicators(dl), st["capital"], st["risk"], dl_dirs, m["W"], m["min_dist"], m["be"], qa and not tradfi_kind(sym))
            seed = [(str(dl[e["entry"]]["t"]), 1 if e["win"] else 0, round(e["r"], 3), e["side"], e["q"]) for e in evl if e["kind"] == "CLOSE" and e["entry"] >= 250]
        except Exception as e:
            print(f"⚠️ historial {sym} {tf}: {e}")
    return dict(sym=sym, tf=tf, d=d, up=I["dir"][-1] < 0, st=I["st"][-1], events=events, T=T, pts=last["pts"], seed=seed)


def deliver(token, owner, st, uids, sym, tf, ev, extra=None, per_user=None):
    """Envía un aviso a cada usuario con su propio capital/riesgo. Devuelve a quién llegó.
    per_user = {uid: datos extra solo para ese usuario} (p. ej. cuántas operaciones tiene abiertas)."""
    ok = []
    for uid in uids:
        if uid not in st["users"]:
            continue
        ex = dict(extra or {}, **((per_user or {}).get(uid) or {}))
        try:
            send(token, chat_of(uid, owner), message(sym, tf, ev, ex, user_scale(st, uid)),
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
    ctx[(sym, tf)] = {"up": res["up"], "st": res["st"], "T": T, "pts": res["pts"], "px": d[-1]["c"],
                      "t_open": d[T["bar"]]["ct"] if T else None}
    H = st.setdefault("hist", {}).setdefault(pair, {})
    if res["seed"] is not None:
        for k, w, r, *rest in res["seed"]:
            H.setdefault(k, [w, r] + rest)
        st.setdefault("hist_seeded", []).append(pair)
    for ev in res["events"]:
        if ev["kind"] == "CLOSE" and ev["entry"] >= 250:
            H[str(d[ev["entry"]]["t"])] = [1 if ev["win"] else 0, round(ev["r"], 3), ev["side"], ev["q"]]
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
            # Solo se avisa si la operación SIGUE VIVA (sin TP ni SL tocados) y no es de hace más de 3 velas.
            active = T is not None and T["bar"] == ev["bar"] and T["tp"] == 0
            if not active or age > 3 * tfms + 120_000:
                continue
            late = age > 150_000
            now_px = None
            if late:  # con retraso: mirar el precio de AHORA (velas de 1m desde el cierre de la vela de la señal)
                try:
                    need = int((now - d[-1]["ct"]) // 60_000) + 2
                    m1 = [b for b in fetch_klines(sym, "1m", min(max(need, 3), 1000)) if b["t"] > d[-1]["ct"]]
                except Exception:
                    m1 = []
                if m1:
                    now_px, s1 = m1[-1]["c"], ev["side"]
                    hi, lo = max(b["h"] for b in m1), min(b["l"] for b in m1)
                    if (hi >= ev["tps"][0] or lo <= ev["sl"]) if s1 == 1 else (lo <= ev["tps"][0] or hi >= ev["sl"]):
                        continue  # en la vela que aún no cerró ya tocó TP1 o SL: la señal ya no sirve
                    gone = (now_px - ev["price"]) / (ev["tps"][0] - ev["price"]) if ev["tps"][0] != ev["price"] else 0
                    if gone >= 0.5 and not ev.get("limit"):
                        continue  # no perseguir el precio: ya recorrió más de media distancia al TP1
            kind = tradfi_kind(sym)
            if kind:
                if market_closed(kind):
                    continue  # oro/acciones: no se abre nada con el mercado real cerrado o a punto de cerrar el fin de semana
                ev = dict(ev, lev=min(ev["lev"], TRADFI_MAX_LEV))
            uids, per_user = [], {}
            for u, p in st["users"].items():
                if tf not in p.get("tfs", []):
                    continue
                mine = open_trades(st, u, now)
                mx = max_ops_of(st, u)
                if mx and len(mine) >= mx:
                    continue  # el usuario puso un máximo con /maxops y ya lo alcanzó
                uids.append(u)  # v4.9.1: llegan todas; si ya tiene esa moneda abierta, el mensaje lo avisa
                per_user[u] = {"open_n": len(mine), "max_ops": mx, "same": [t2 for _, s2, t2 in mine if s2 == sym]}
            extra = {"hist": hist_stats(st, pair), "wr": wr_text(st, sym, tf), "late": int(age // 60_000) if late else 0, "now_px": now_px,
                     "tradfi": bool(kind), "night": stocks_night(kind), "hm": hm_local(d[ev["bar"]]["ct"])}
            extra.update(open_extras(sym, tf, ev, ctx))
            extra["conf"] = [t for t in ALL_TFS if t != tf and ((ctx.get((sym, t)) or {}).get("T") or {}).get("side") == ev["side"]]
            to = deliver(token, owner, st, uids, sym, tf, ev, extra, per_user)
            if uids and not to:
                sent.discard(key)  # no llegó a nadie (sin internet): se reintenta en la próxima vuelta
            if to:
                trades[tid] = {"to": to, "t": d[ev["bar"]]["t"], "side": ev["side"]}
                live[tid] = {"sym": sym, "tf": tf, "side": ev["side"], "e2": ev["e2"], "tp1": ev["tps"][0], "sl": ev["sl"],
                             "filled": not ev.get("limit"), "last": now, "be": tf in BE_TFS}
        else:
            tr = trades.get(tid)
            if not tr or tr.get("cancelled") or (tr.get("closed") and ev["kind"] not in ("CANCEL", "CLOSE")):
                continue  # nadie recibió esa señal (o ya se avisó que se canceló): no se manda un TP suelto
            if ev["kind"] in ("CANCEL", "CLOSE"):
                live.pop(tid, None)
                tr["closed"] = True  # cerrada aunque el mensaje falle: no bloquea la señal nueva de la misma vela
            if ev["kind"] == "CLOSE" and not tr.get("j"):
                journal_add(st, tid, tr, ev, sym, tf, d[ev["bar"]]["ct"])  # v4.8: diario (/stats), una sola vez por operación
                tr["j"] = True
            rcpt = [u for u in tr["to"] if u in st["users"]]
            got = deliver(token, owner, st, rcpt, sym, tf, ev)
            if rcpt and not got:
                sent.discard(key)  # Telegram no respondió: se reintenta en la próxima revisión de esta moneda
                continue
            if ev["kind"] == "CANCEL":
                tr["cancelled"] = True
    # operaciones vigiladas al minuto: actualizar o retirar
    for tid in [t for t, v in live.items() if v["sym"] == sym and v["tf"] == tf]:
        if not T or tid != f"{sym}_{tf}_{d[T['bar']]['t']}" or T["tp"] >= 1:
            live.pop(tid, None)  # cerrada, reemplazada o ya pasó TP1 (el resto llega al cierre de la vela)
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
            if v["filled"] and v.get("sl") and dip(v["sl"]):  # el stop va ANTES que el TP (igual que el cálculo al cierre)
                if f"{tid}_SL" not in sent:
                    sent.add(f"{tid}_SL")
                    deliver(token, owner, st, to, v["sym"], v["tf"], dict(kind="SL", side=s, price=v["sl"]))
                trades[tid]["sl_hit"] = True
                live.pop(tid, None)  # el resultado en $ llega al cerrar la vela
                break
            if v["filled"] and hit(v["tp1"]):
                if f"{tid}_TP1" not in sent:
                    sent.add(f"{tid}_TP1")
                    deliver(token, owner, st, to, v["sym"], v["tf"], dict(kind="TP1", side=s, price=v["tp1"], be=v.get("be")))
                live.pop(tid, None)  # lo demás (TP2/TP3/SL y resultado) llega al cerrar la vela
                break
            v["last"] = b["ct"]
    st["sent"] = list(sent)


# ----------------------------- PC ENCENDIDA / NUBE DE RESPALDO -----------------------------
HOST = (os.environ.get("BOT_NAME") or socket.gethostname() or "PC")[:24]   # nombre de este equipo (PC, ORACLE…)
MAIN = os.environ.get("BOT_MAIN") == "1"   # el servidor 24/7 tiene prioridad sobre la PC


_hb_lock = threading.Lock()
_leading = threading.Event()   # este equipo es el que envía las señales (el latido solo corre mientras lo sea)


def heartbeat(token, owner, st, force=False, fresh=False):
    """Mensaje fijado en tu chat: qué equipo está enviando las señales. La nube y los otros equipos lo leen
    para no repetir avisos. fresh=True → mensaje nuevo (al tomar el control)."""
    if not force and time.time() - st.get("hb_t", 0) < 120:
        return
    with _hb_lock:
        now = time.time()
        with _st_lock:
            st["hb_t"] = now
        try:
            txt = (f"🟢 Bot activo [{HOST}]{' ★' if MAIN else ''} · {time.strftime('%H:%M')} · {len(active_symbols(st))} monedas · "
                   f"{', '.join(scan_tfs(st))} · {len(st['users'])} usuario(s)")
        except Exception:  # (la lista de usuarios cambió justo ahora en el otro hilo)
            txt = f"🟢 Bot activo [{HOST}]{' ★' if MAIN else ''} · {time.strftime('%H:%M')}"
        mid = st.get("status_msg")
        if mid and not fresh:
            try:
                tg(token, "editMessageText", {"chat_id": owner, "message_id": mid, "text": txt})
                return
            except Exception as e:
                if "not modified" in str(e):
                    return
        try:
            r = send(token, owner, txt, silent=True)
            new = r["result"]["message_id"]
            with _st_lock:
                st["status_msg"] = new  # aunque fijarlo falle, no se manda otro mensaje en cada revisión
            if mid:
                try:
                    tg(token, "unpinChatMessage", {"chat_id": owner, "message_id": mid})
                except Exception:
                    pass
            tg(token, "pinChatMessage", {"chat_id": owner, "message_id": new, "disable_notification": "true"})
            with _st_lock:
                st["status_msg"] = new
        except Exception as e:
            print("estado:", e)


def hb_loop(token, owner, st):
    """Latido cada 60 s en su propio hilo: aunque una revisión tarde varios minutos, el mensaje fijado sigue fresco
    y el otro equipo no cree que este se apagó (antes eso hacía que los dos enviaran señales a la vez)."""
    while True:
        time.sleep(60)
        if _leading.is_set():
            try:
                heartbeat(token, owner, st, force=True)
            except Exception as e:
                print("latido:", e)


def am_leader(token, owner, st, force=False):
    """Solo un bot (PC u Oracle) envía señales y contesta comandos. Si otro equipo actualizó el mensaje fijado
    hace menos de 4 min (según el reloj de Telegram), este espera. El que tiene ★ (servidor 24/7) siempre tiene
    prioridad: si ve que el fijado es de otro equipo, vuelve a fijar el suyo para que el otro se retire."""
    now = time.time()
    if not force and now - st.get("lead_chk", 0) < 60:
        return st.get("leader", True)
    st["lead_chk"] = now
    try:
        pm = tg(token, "getChat", {"chat_id": owner})["result"].get("pinned_message") or {}
    except Exception as e:
        print("getChat:", e)
        (_leading.set if st.get("leader", True) else _leading.clear)()
        return st.get("leader", True)
    txt = pm.get("text", "")
    mine = (pm.get("message_id") and pm.get("message_id") == st.get("status_msg")) or f"[{HOST}]" in txt
    fresh = tg_now() - max(pm.get("date", 0), pm.get("edit_date", 0)) < 240
    other = txt.startswith("🟢") and not mine and fresh
    leader = not other or (MAIN and "★" not in txt)
    if leader and MAIN and not mine and not force and now - st.get("repin_t", 0) > 600:  # recuperar el fijado (máx. 1 vez cada 10 min)
        st["repin_t"] = now
        print(time.strftime("%H:%M:%S"), "★ el mensaje fijado era de otro equipo: se vuelve a fijar el de este servidor")
        heartbeat(token, owner, st, force=True, fresh=True)
    st["leader"] = leader
    (_leading.set if leader else _leading.clear)()
    return leader


def weekend_warn(token, owner, st):
    """Viernes 17-18 UTC: aviso a quien tenga abierta una operación de oro, plata, petróleo o acciones
    (el mercado real cierra el fin de semana y al reabrir el precio puede saltar el SL)."""
    g = time.gmtime()
    if not (g.tm_wday == 4 and 17 <= g.tm_hour < 18):
        return
    sent = set(st["sent"])
    for tid, tr in list(st.get("trades", {}).items()):
        if tr.get("closed") or tr.get("cancelled") or tr.get("sl_hit") or f"{tid}_WKND" in sent:
            continue
        sym, tf, _ = tid.rsplit("_", 2)
        if not tradfi_kind(sym):
            continue
        sent.add(f"{tid}_WKND")
        deliver(token, owner, st, tr.get("to", []), sym, tf, dict(kind="WKND", side=tr.get("side", 1)))
    st["sent"] = list(sent)


def start_up(token, chat, st, ctx):
    """Primera revisión completa al arrancar (o al tomar el control)."""
    n_pairs = len(all_pairs(st))
    nuevos = sum(1 for s in active_symbols(st) for t in scan_tfs(st) if f"{s}_{t}" not in st.get("hist_seeded", []))
    print(f"Revisando {n_pairs} gráficos" + (f" ({nuevos} calculan su winrate por primera vez: tarda unos minutos)" if nuevos else ""))
    try:
        send(token, chat, f"⏳ <b>Bot iniciando [{HOST}]</b>: revisando {n_pairs} gráficos" +
             (f" ({nuevos} calculan su winrate por primera vez, tarda unos minutos)." if nuevos else "."), silent=True)
    except Exception as e:
        print("telegram:", e)
    heartbeat(token, chat, st, force=True, fresh=True)  # avisa enseguida que este equipo tomó el control
    t_now = int(time.time() * 1000)
    recent_c = t_now - st.get("conf_t", 0) < 15 * 60_000   # reinicio corto (/actualizar): lo que se armó mientras tanto sí se avisa
    recent_m = t_now - st.get("maj_t", 0) < 15 * 60_000    # (la memoria guardada evita repetir lo ya enviado)
    run_once(token, chat, st, ctx, progress=True)
    confluence(token, chat, st, ctx, notify=recent_c)
    majors_watch(token, chat, st, ctx, notify=recent_m)
    heartbeat(token, chat, st, force=True)
    try:
        send(token, chat, f"🤖 <b>Bridge Táctico v{VERSION} activo [{HOST}]</b>\nVigilando {len(active_symbols(st))} monedas en {', '.join(scan_tfs(st))}.\n"
                          "Cada señal llega en su propio mensaje.\n\n" + settings_line(st) +
                          "\n\n/config → elegir temporalidades · /ayuda → comandos", silent=True)
    except Exception as e:
        print("telegram:", e)
    st["init"] = True
    save_state(st, int(time.time() * 1000))


def pc_alive(token, owner):
    """La nube consulta el mensaje fijado: si la PC lo actualizó hace menos de 12 min, la PC está enviando todo."""
    try:
        pm = tg(token, "getChat", {"chat_id": owner})["result"].get("pinned_message") or {}
    except Exception as e:
        print("getChat:", e)
        return False, {}
    last = max(pm.get("date", 0), pm.get("edit_date", 0))
    return pm.get("text", "").startswith("🟢") and time.time() - last < 12 * 60, pm


def scan(pairs, st, ctx, token, owner, notify=True, progress=False):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    t_save = time.time()
    ex = ThreadPoolExecutor(max_workers=8)
    try:
        want = set(scan_tfs(st))
        futs = {ex.submit(compute, sym, tf, st, want): (sym, tf) for sym, tf in pairs}
        total = len(futs)
        for n, f in enumerate(as_completed(list(futs)), 1):  # cada señal sale apenas está lista (una moneda lenta no frena a las demás)
            sym, tf = futs.pop(f)   # v4.9.4: se suelta cada resultado (1000 velas) apenas se usa → menos memoria en el servidor
            try:
                res = f.result()
                del f
                apply(res, st, ctx, token, owner, notify)
                del res
            except Exception as e:
                print(f"⚠️ {sym} {tf}: {e}")
            if progress and (n % 20 == 0 or n == total):
                print(time.strftime("%H:%M:%S"), f"preparando: {n}/{total} gráficos")
            if progress and time.time() - t_save > 60:  # la primera vez tarda: se guarda el avance cada minuto
                save_state(st, int(time.time() * 1000))
                heartbeat(token, owner, st)
                t_save = time.time()
    except BaseException:  # Ctrl+C / reinicio del servidor: no esperar a las monedas en cola
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)


def run_once(token, owner, st, ctx, notify=True, progress=False):
    scan(all_pairs(st), st, ctx, token, owner, notify, progress)


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
                                              "text": "☁️ PC y servidor apagados · avisos 15m, 1h y 4h desde GitHub (pueden llegar con 5-30 min de retraso)"})
            except Exception as e:
                print("estado:", e)
        run_once(token, chat, st, ctx, notify=not alive)
        if os.environ.get("COMMANDS", "1") == "1":  # en la nube se apaga para no robarle los comandos al bot de la PC
            handle_commands(token, chat, st, ctx)
        if not st["init"] or os.environ.get("TEST") == "1":
            send(token, chat, ("🤖 <b>Bridge Táctico (nube) activo</b>" if not st["init"] else "🧪 <b>Prueba nube OK</b>") +
                 f"\nVigilando {len(active_symbols(st))} monedas en {', '.join(t.upper() for t in scan_tfs(st))}.\n" +
                 ("🟢 Hay un bot activo (PC o servidor): él envía las señales al instante; GitHub queda de respaldo." if alive
                  else "☁️ No hay ningún bot activo: GitHub envía 15m, 1h y 4h."))
        st["init"] = True
        save_state(st, int(time.time() * 1000))
        print("PC activa:", alive)
        return

    print(f"Bridge Táctico bot en marcha [{HOST}{' ★' if MAIN else ''}] (cierra la ventana o Ctrl+C para detener)…")

    def _stop(*_):  # el servidor se reinicia una vez al día (systemd manda SIGTERM): guardar antes de salir
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _stop)
    except Exception:
        pass
    threading.Thread(target=hb_loop, args=(token, chat, st), daemon=True).start()
    leader = am_leader(token, chat, st, force=True)
    done = {}  # temporalidad -> última vela revisada (si una revisión dura más de 1 min, no se salta ningún cierre)
    try:
        if leader:
            m0 = int(time.time() // 60)  # velas que cierren DURANTE el arranque se revisan después
            start_up(token, chat, st, ctx)
            done = {tf: m0 // (TF_MS[tf] // 60_000) for tf in ALL_TFS}
        last_min = None
        while True:
            try:
                was, leader = leader, am_leader(token, chat, st)
                if not leader:
                    if was:
                        print(time.strftime("%H:%M:%S"), "⏸ Otro equipo está enviando las señales (mensaje fijado 🟢): este bot queda en espera")
                    time.sleep(10)
                    continue
                if not was:
                    print(time.strftime("%H:%M:%S"), "▶ Este bot toma el control")
                    ctx.clear()
                    m0 = int(time.time() // 60)
                    start_up(token, chat, st, ctx)
                    done = {tf: m0 // (TF_MS[tf] // 60_000) for tf in ALL_TFS}
                handle_commands(token, chat, st, ctx)
                now = time.time()
                minute = int(now // 60)
                if minute != last_min and now % 60 >= 3:  # 3 s después del cierre de cada vela de 1m
                    last_min = minute
                    syms = active_symbols(st)
                    tfs = scan_tfs(st)
                    due = {tf for tf in ALL_TFS if done.get(tf) != minute // (TF_MS[tf] // 60_000)}
                    pairs = [(s, t) for s, t in all_pairs(st) if t in due or (s, t) not in ctx]
                    for tid, tr in st.get("trades", {}).items():  # operaciones abiertas: se siguen aunque la moneda ya no esté en la lista
                        if not (tr.get("closed") or tr.get("cancelled")):
                            s2, t2, _ = tid.rsplit("_", 2)
                            if t2 in TF_MS and (s2, t2) not in pairs and (t2 in due or (s2, t2) not in ctx):
                                pairs.append((s2, t2))
                    keep = set(all_pairs(st)) | set(pairs)
                    for k in [k for k in ctx if k not in keep]:
                        del ctx[k]
                    t0 = time.time()
                    scan(pairs, st, ctx, token, chat)
                    for tf in due:
                        done[tf] = minute // (TF_MS[tf] // 60_000)
                    live_watch(token, chat, st)
                    confluence(token, chat, st, ctx)
                    majors_watch(token, chat, st, ctx)
                    weekend_warn(token, chat, st)
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
                raise
            except Exception as e:
                print("error:", e)
            time.sleep(2)
    except KeyboardInterrupt:
        print("Deteniendo: guardando el estado…")
        _leading.clear()
        save_state(st, int(time.time() * 1000))


if __name__ == "__main__":
    main()
