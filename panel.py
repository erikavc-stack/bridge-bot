#!/usr/bin/env python3
"""
BRIDGE · panel flotante para TradingView (Windows) · v2.0

Un widget pequeño y moderno que queda ENCIMA de TradingView (como STRIKE):
  - detecta la moneda que tienes abierta en TradingView (por el título de la ventana)
  - temporalidades 1m…1D: verde = LONG para apuntar, rojo = SHORT (clic para elegir otra)
  - botón ENTRAR: abre la señal elegida en Bitunix (E1/E2/E3, SL y TP) después de confirmar
  - tus posiciones en Bitunix: total no realizado, riesgo abierto y cada posición con su PnL
  - vigila las operaciones que abriste desde aquí: TP, break even, gestión STRIKE, "soltar" si cambias algo a mano

Cómo está hecho: este programa (Python, sin librerías extra) calcula todo con el motor de bot.py y sirve la interfaz
(HTML) solo para tu PC (127.0.0.1, con una clave por sesión); la ventana es Microsoft Edge en modo aplicación
(sin pestañas ni barra de direcciones), siempre encima. Al cerrar la ventana, se cierra el panel.

Necesita bot.py v6.4 o más en esta carpeta. Doble clic en iniciar_panel.bat (o el acceso directo BRIDGE).
Las llaves de Bitunix del panel se guardan en panel_llaves.json (solo en esta PC, nunca en GitHub) y NO cambian
qué equipo manda en el bot de Telegram. Crea la llave en Bitunix solo con permiso de trading (sin retiros).
"""
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PANEL_VERSION = "2.0"
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
PANEL_FILE = os.path.join(AQUI, "panel.json")          # ajustes (sin llaves)
KEYS_FILE = os.path.join(AQUI, "panel_llaves.json")    # llaves de Bitunix solo para el panel
NOMBRE = "BRIDGE"
WIN_W, WIN_H, FOLD_H = 300, 690, 96                     # tamaño de la ventana (px) y alto plegado
AIM_TTL = 90                                            # segundos para confirmar un Entrar

DEFAULTS = dict(capital=500.0, riesgo=1.0, max=10.0, lev=0, perdida=5.0, modo="prueba", gestion="bot", berr=1.0, parcial=25.0,
                comp=True, seguro=True, seguro_lim=5.0, seguir_tv=True, moneda="BTCUSDT", telegram=True, encima=True,
                tfs=["1m", "3m", "5m", "15m", "1h", "2h", "4h", "8h", "1d"], pos="", alto=WIN_H, plegado=False)


# ----------------------------- AJUSTES Y LLAVES -----------------------------
def cargar_json(path, base=None):
    out = dict(base or {})
    try:
        with open(path, encoding="utf-8") as f:
            out.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ {os.path.basename(path)} dañado: {e}")
    return out


def guardar_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def poner_llaves(key, secret):
    """Las llaves van al entorno de ESTE proceso (bot.bx_keys las lee de ahí primero)."""
    if key and secret:
        os.environ["BITUNIX_KEY"], os.environ["BITUNIX_SECRET"] = key.strip(), secret.strip()


# ----------------------------- WINDOWS: VENTANAS -----------------------------
def _ventanas(filtro):
    """[(hwnd, título, exe)] de las ventanas visibles que cumplen filtro(título, exe), de la de más arriba a la de más abajo."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                exe = ""
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                hp = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
                if hp:
                    s, sz = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
                    if k.QueryFullProcessImageNameW(hp, 0, s, ctypes.byref(sz)):
                        exe = s.value.rsplit("\\", 1)[-1].lower()
                    k.CloseHandle(hp)
                if filtro(b.value, exe):
                    out.append((h, b.value, exe))
        return True

    u.EnumWindows(cb, 0)
    return out


SYM_RE = re.compile(r"(?:^|[\s:])([A-Z0-9]{2,20}?(?:USDT|USDC))(?:\.P|PERP)?(?=\s|$|,)")


def moneda_de_titulo(title):
    """'ETHUSDT.P ▲ 1.823,25 +3.1% / Sem nome' → 'ETHUSDT' · 'BINANCE:SOLUSDT.P 141.5 …' → 'SOLUSDT'."""
    m = SYM_RE.search((title or "").upper())
    return m.group(1) if m else None


def ventanas_tradingview():
    return [t for _, t, _ in _ventanas(lambda t, exe: exe == "tradingview.exe" or "tradingview" in t.lower())]


def moneda_tradingview():
    for t in ventanas_tradingview():
        s = moneda_de_titulo(t)
        if s:
            return s
    return None


NAVEGADORES = ("msedge.exe", "chrome.exe")


def ventana_panel():
    """La ventana del panel (Edge/Chrome en modo aplicación con el título BRIDGE)."""
    w = _ventanas(lambda t, exe: t == NOMBRE and exe in NAVEGADORES)
    return w[0][0] if w else None


def _win(h, after, x=0, y=0, cx=0, cy=0, flags=0):
    import ctypes
    from ctypes import wintypes
    f = ctypes.windll.user32.SetWindowPos
    # tipos explícitos: en Windows de 64 bits HWND_TOPMOST (-1) tiene que llegar como puntero, no como entero de 32 bits
    f.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    f.restype = wintypes.BOOL
    return f(h, after, x, y, cx, cy, flags)


def poner_encima(h, on):
    _win(h, -1 if on else -2, flags=0x0001 | 0x0002 | 0x0010)  # TOPMOST/NOTOPMOST · NOSIZE · NOMOVE · NOACTIVATE


def esta_encima(h):
    import ctypes
    f = ctypes.windll.user32.GetWindowLongW
    f.restype = ctypes.c_long
    return bool(f(h, -20) & 0x8)  # GWL_EXSTYLE · WS_EX_TOPMOST


def rect(h):
    import ctypes
    from ctypes import wintypes
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def alto_ventana(h, alto):
    x, y, w, _ = rect(h)
    _win(h, 0, cx=w, cy=alto, flags=0x0002 | 0x0004 | 0x0010)  # NOMOVE · NOZORDER · NOACTIVATE


def navegador():
    for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
              r"%LocalAppData%\Google\Chrome\Application\chrome.exe"):
        p = os.path.expandvars(p)
        if os.path.exists(p):
            return p
    return None


def mensaje_error(text):
    """Error antes de tener ventana (p. ej. bot.py viejo): cuadro de Windows."""
    print(text)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, NOMBRE, 0x10)


# ----------------------------- SEÑALES (motor del bot) -----------------------------
def limpiar(text):
    """HTML de Telegram → texto plano."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def fila_tf(bot, st, sym, tf):
    """Una temporalidad: dirección de la Tendencial, puntos y la señal activa (si hay)."""
    res = bot.compute(sym, tf, st, want=set())  # want vacío: sin historial largo (rápido)
    d, T = res["d"], res["T"]
    row = dict(tf=tf, up=res["up"], pts=res["pts"], px=d[-1]["c"], sig=None)
    if not T:
        return row
    ev = next((e for e in res["events"] if e["kind"] == "OPEN" and e["bar"] == T["bar"]), None)
    age = len(d) - 1 - T["bar"]
    sig = dict(side=T["side"], e1=T["e1"], e2=T["e2"], sl=T["sl"], tp1=T["tps"][0], tp=T["tp"], age=age,
               tid=f"panel_{sym}_{tf}_{d[T['bar']]['t']}", ev=ev, ct=d[T["bar"]]["ct"], limit=bool(ev and ev.get("limit")),
               filled=T["pos"] > 0)
    if T["tp"] >= 1:
        sig["estado"], sig["ok"] = f"TP{T['tp']} ✓", False
    elif age > 3:
        sig["estado"], sig["ok"] = f"hace {age} velas", False
    elif not ev:
        sig["estado"], sig["ok"] = "—", False
    else:
        sig["estado"], sig["ok"] = ("límite" if sig["limit"] and not sig["filled"] else f"hace {age}" if age else "AHORA"), True
    row["sig"] = sig
    return row


def elegir_tf(rows, tfs, actual=None):
    """La temporalidad elegida si sigue con señal válida; si no, la señal válida más reciente (en empate, la mayor)."""
    ok = [(r["sig"]["age"], -tfs.index(tf), tf) for tf, r in rows.items() if r.get("sig") and r["sig"]["ok"]]
    if actual and any(t == actual for _, _, t in ok):
        return actual
    return min(ok)[2] if ok else None


def dinero(v):
    return f"{'+' if v >= 0 else '−'}${abs(v):,.2f}"


# ----------------------------- ESTADO DEL PANEL -----------------------------
class App:
    def __init__(self, bot, cfg, token="", chat=""):
        self.bot, self.cfg, self.token, self.chat = bot, cfg, token, chat
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(4)
        self.rows, self.rows_sym, self.rows_t, self.loading = {}, None, 0.0, True
        self.sym, self.sel, self.sel_manual = cfg["moneda"], None, False
        self.pos = dict(rows=[], eq=0.0, err="", t=0.0)
        self.logs, self.pending = [], {}
        self.hwnd, self.last_rect = None, None
        self.want_sig = threading.Event()
        self.stop = threading.Event()
        self.st = {"users": {"owner": {}}, "trades": {}, "aim": {}}
        self.sync_st()

    def sync_st(self):
        c, st = self.cfg, self.st
        st.update(capital=c["capital"], risk=c["riesgo"], auto_capital=c["capital"], auto_risk=c["riesgo"], auto_max=c["max"],
                  auto_lev=int(c["lev"]), auto_loss=c["perdida"], auto_mg=c["gestion"], auto_berr=c["berr"],
                  auto_parcial=c["parcial"], auto_comp=c["comp"], seguro=c["seguro"], seguro_lim=c["seguro_lim"],
                  aim_mode=c["modo"])
        st["users"]["owner"].update(capital=c["capital"], risk=c["riesgo"])

    def save(self):
        with self.lock:
            self.cfg["moneda"] = self.sym
            if self.last_rect:
                x, y, _, h = self.last_rect
                self.cfg["pos"] = f"{x},{y}"
                if not self.cfg["plegado"] and h > FOLD_H + 40:
                    self.cfg["alto"] = h
            guardar_json(PANEL_FILE, self.cfg)

    def log(self, text):
        line = limpiar(text).replace("\n", " · ").strip()
        with self.lock:
            self.logs = (self.logs + [(time.time(), time.strftime("%H:%M"), line)])[-300:]

    # --- hilos ---
    def loop_signals(self):
        while not self.stop.is_set():
            self.want_sig.wait(1.0)
            if time.time() - self.rows_t < 30 and not self.want_sig.is_set():
                continue
            self.want_sig.clear()
            sym = self.sym
            with self.lock:
                self.loading = True
            futs = {tf: self.pool.submit(fila_tf, self.bot, self.st, sym, tf) for tf in self.cfg["tfs"]}
            rows, errs = {}, []
            for tf, fu in futs.items():
                try:
                    rows[tf] = fu.result(timeout=60)
                except Exception as e:
                    errs.append(f"{tf}: {e}")
            with self.lock:
                self.rows_t, self.loading = time.time(), False
                if sym != self.sym:
                    continue  # cambiaste de moneda mientras calculaba
                self.rows, self.rows_sym = rows, sym
                if not self.sel_manual or self.sel not in rows:
                    self.sel = elegir_tf(rows, self.cfg["tfs"], self.sel)
            for e in errs[:2]:
                self.log(f"⚠️ {sym} {e}"[:200])

    def loop_positions(self):
        while not self.stop.is_set():
            if self.bot.bx_keys():
                try:
                    rows, eq = self.bot.bx_portfolio()
                    with self.lock:
                        self.pos = dict(rows=rows, eq=eq, err="", t=time.time())
                except Exception as e:
                    with self.lock:
                        self.pos = dict(rows=[], eq=0.0, err=str(e)[:120], t=time.time())
            else:
                with self.lock:
                    self.pos = dict(rows=[], eq=0.0, err="Sin llaves de Bitunix · toca ⚙ para ponerlas", t=time.time())
            self.stop.wait(15)

    def loop_tv(self):
        while not self.stop.is_set():
            if self.cfg["seguir_tv"]:
                try:
                    s = moneda_tradingview()
                    if s and s != self.sym:
                        self.set_symbol(s)
                except Exception:
                    pass
            self.stop.wait(1.5)

    def vigilante(self):
        """Cada 10 s: TP, break even, STRIKE y "soltar" de lo abierto DESDE EL PANEL. El SL siempre queda puesto en Bitunix
        aunque cierres el panel; al volver a abrirlo sigue donde quedó."""
        bot, st = self.bot, self.st
        t_guard = t_seg = 0.0
        while not self.stop.wait(10):
            if not bot.bx_keys():
                continue
            try:
                if bot._bx["trades"]:
                    bot.bx_manage(self.token, self.chat, st)
                if st.get("aim_mode") == "si" and time.time() - t_guard > 300:
                    t_guard = time.time()
                    bot.bx_guard(self.token, self.chat, st)
                if st.get("seguro", True) and time.time() - t_seg > 60:
                    t_seg = time.time()
                    bot.bx_seguro(self.token, self.chat, st)
            except Exception as e:
                self.log(f"⚠️ vigilante: {e}"[:200])

    def loop_window(self):
        """Encuentra la ventana, la pone encima y guarda su posición. Si la cierras, el panel se cierra."""
        seen, missing, t0, t_save = False, None, time.time(), 0.0
        while not self.stop.wait(1.0):
            h = ventana_panel()
            if h:
                if h != self.hwnd:
                    self.hwnd = h
                    if self.cfg["plegado"]:
                        alto_ventana(h, FOLD_H)
                if esta_encima(h) != bool(self.cfg["encima"]):  # Edge lo quita al terminar de crear la ventana: se repone
                    poner_encima(h, self.cfg["encima"])
                seen, missing = True, None
                self.last_rect = rect(h)
                if time.time() - t_save > 10:
                    t_save = time.time()
                    self.save()
            elif seen:
                missing = missing or time.time()
                if time.time() - missing > 4:
                    self.shutdown()
            elif time.time() - t0 > 90:
                self.log("No apareció la ventana del panel")
                self.shutdown()

    def shutdown(self):
        if self.stop.is_set():
            return
        try:
            self.save()
            self.bot.bx_save()
        finally:
            self.stop.set()
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()

    # --- acciones ---
    def set_symbol(self, s, manual=False):
        s = self.bot.norm_symbol(s or "")
        if not s or not re.fullmatch(r"[A-Z0-9]{2,24}", s):
            return False
        with self.lock:
            if manual:
                self.cfg["seguir_tv"] = False  # la escribiste tú: dejar de seguir TradingView
            if s != self.sym:
                self.sym, self.rows, self.rows_sym, self.sel, self.sel_manual = s, {}, None, None, False
        self.save()
        self.want_sig.set()
        return True

    def pick(self, tf):
        with self.lock:
            if tf in self.cfg["tfs"]:
                self.sel, self.sel_manual = tf, True

    def aim(self, tf):
        """Paso 1 de Entrar: revisar y devolver el plan para confirmar (no envía nada)."""
        with self.lock:
            r = self.rows.get(tf)
            s, sym = r and r["sig"], self.sym
        if not s or not s["ok"]:
            return dict(ok=False, why="Esa temporalidad no tiene una señal para entrar ahora.")
        if not self.bot.bx_keys():
            return dict(ok=False, why="Primero pon tus llaves de Bitunix en ⚙ Ajustes.")
        bot, st = self.bot, self.st
        a = dict(sym=sym, tf=tf, ev=s["ev"], ct=s["ct"])
        plan, why = bot.aim_plan(st, sym, a["ev"])
        why = why or bot.aim_check(st, s["tid"], a)
        if why:
            return dict(ok=False, why=why)
        aid = secrets.token_hex(8)
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if time.time() - v["t"] < AIM_TTL}
            self.pending[aid] = dict(a=a, s=s, t=time.time())
        pp, qp = plan["pp"], plan["qp"]
        legs = [dict(name=l["name"], how="a mercado" if l["type"] == "MARKET" else "límite", price=f"{l['price']:.{pp}f}",
                     qty=f"{l['qty']:.{qp}f}") for l in plan["legs"]]
        return dict(ok=True, aid=aid, sym=sym, tf=tf, side="LONG" if plan["side"] == "BUY" else "SHORT", lev=plan["lev"],
                    margin=f"${plan['margin']:.2f}", legs=legs, sl=f"{plan['sl']:.{pp}f}", tps=[f"{t:.{pp}f}" for t in plan["tps"]],
                    risk=limpiar(bot.aim_risk_line(st, plan)).strip(), real=self.cfg["modo"] == "si", ttl=AIM_TTL)

    def confirm(self, aid):
        """Paso 2: se revisa todo otra vez (el precio pudo moverse) y se envía (REAL) o se anota (PRUEBA)."""
        with self.lock:
            p = self.pending.pop(aid, None)
        if not p or time.time() - p["t"] > AIM_TTL:
            return dict(ok=False, msg="La confirmación venció: vuelve a tocar Entrar.")
        a, s, bot, st = p["a"], p["s"], self.bot, self.st
        plan, why = bot.aim_plan(st, a["sym"], a["ev"])
        why = why or bot.aim_check(st, s["tid"], a)
        if not why and self.cfg["modo"] == "si":
            why = bot.auto_arm_error(st)
        if why:
            self.log(f"🎯 {a['sym']} {a['tf']}: no se envió ({why})")
            return dict(ok=False, msg=f"No se envió: {why}")
        if self.cfg["modo"] != "si":
            self.log(bot.bx_text(a["sym"], a["tf"], plan, "PRUEBA · no se envió nada", "🎯 ENTRAR"))
            return dict(ok=True, msg="PRUEBA: no se envió nada a Bitunix. Toca PRUEBA arriba para pasar a REAL.")
        bot.bx_open(self.token, self.chat, st, a["sym"], a["tf"], a["ev"], s["tid"], plan, manual=True)
        self.pos["t"] = 0
        return dict(ok=True, msg="Órdenes enviadas a Bitunix. Míralas en Posiciones.")

    def mode_check(self):
        err = self.bot.auto_arm_error(self.st)
        return dict(ok=not err, why=err, capital=self.cfg["capital"], riesgo=self.cfg["riesgo"], max=self.cfg["max"])

    def set_mode(self, real):
        if real:
            err = self.bot.auto_arm_error(self.st)
            if err:
                return dict(ok=False, why=err)
        with self.lock:
            self.cfg["modo"] = "si" if real else "prueba"
            self.sync_st()
        self.save()
        self.log("💸 Modo REAL activado" if real else "Modo PRUEBA: Entrar solo anota las órdenes")
        return dict(ok=True)

    def settings_get(self):
        keys = cargar_json(KEYS_FILE)
        k = keys.get("key") or os.environ.get("BITUNIX_KEY", "")
        c = self.cfg
        return dict({x: c[x] for x in ("capital", "riesgo", "max", "lev", "perdida", "seguro_lim", "berr", "parcial", "gestion",
                                         "comp", "seguro", "telegram")}, key_hint=(k[:4] + "•••" + k[-4:]) if len(k) > 10 else "")

    def settings_set(self, d):
        try:
            new = {k: float(str(d[k]).replace(",", ".")) for k in ("capital", "riesgo", "max", "lev", "perdida", "seguro_lim", "berr", "parcial")}
        except (KeyError, ValueError):
            return dict(ok=False, why="Revisa los números.")
        if new["capital"] <= 0 or not 0 < new["riesgo"] <= 10 or new["max"] <= 0:
            return dict(ok=False, why="Capital y margen deben ser mayores que 0, y el riesgo entre 0 y 10 %.")
        new["lev"] = int(new["lev"])
        with self.lock:
            self.cfg.update(new, gestion="strike" if d.get("gestion") == "strike" else "bot",
                            **{k: bool(d.get(k)) for k in ("comp", "seguro", "telegram")})
            self.sync_st()
        key, sec = str(d.get("key") or "").strip(), str(d.get("secret") or "").strip()
        if key and sec:
            guardar_json(KEYS_FILE, {"key": key, "secret": sec})
            poner_llaves(key, sec)
            self.log("🔑 Llaves de Bitunix guardadas en esta PC")
        self.save()
        self.pos["t"] = 0
        self.log("⚙ Ajustes guardados" + (" · gestión STRIKE (no está en el backtest)" if self.cfg["gestion"] == "strike" else ""))
        return dict(ok=True)

    def fold(self, on):
        with self.lock:
            self.cfg["plegado"] = bool(on)
        if self.hwnd:
            alto_ventana(self.hwnd, FOLD_H if on else int(self.cfg.get("alto") or WIN_H))
        self.save()

    def top(self, on):
        self.cfg["encima"] = bool(on)
        if self.hwnd:
            poner_encima(self.hwnd, self.cfg["encima"])
        self.save()

    def follow(self, on):
        self.cfg["seguir_tv"] = bool(on)
        self.save()

    # --- lo que ve la interfaz ---
    def snapshot(self, logs=False):
        bot, fmt = self.bot, self.bot.fmt
        with self.lock:
            rows, sel, sym = dict(self.rows), self.sel, self.sym
            pos = dict(self.pos)
            last = self.logs[-1] if self.logs else None
            out_logs = [f"{h} {t}" for _, h, t in self.logs[-120:]] if logs else None
        tfs = []
        for tf in self.cfg["tfs"]:
            r = rows.get(tf)
            s = r and r["sig"]
            tfs.append(dict(tf=tf, has=bool(r), up=bool(r and r["up"]), pts=r["pts"] if r else 0,
                            side=(s["side"] if s else 0), ok=bool(s and s["ok"]), estado=s["estado"] if s else "",
                            entry=fmt(s["e2"] if s["limit"] else s["e1"]) if s else "", limit=bool(s and s["limit"]),
                            sl=fmt(s["sl"]) if s else "", tp1=fmt(s["tp1"]) if s else ""))
        px = next((r["px"] for r in rows.values()), None)
        ups = sum(1 for r in rows.values() if r["up"])
        s = sel and (rows.get(sel) or {}).get("sig")
        prows, eq = pos["rows"], pos["eq"]
        tot, rk = sum(x["pnl"] for x in prows), sum(x["risk"] or 0 for x in prows)
        pr, lim = (rk / eq * 100 if eq else 0.0), self.cfg["seguro_lim"]
        cards = []
        for x in sorted(prows, key=lambda x: -abs(x["pnl"])):
            r = x["r"]
            if r:
                who = "✋ soltada" if r.get("soltada") else f"{r['tf']} · panel · TP{r['stage']}" + (" · BE" if r.get("be_px") else "")
            else:
                who = "servidor o tuya"
            pct = x["pnl"] / x["margin"] * 100 if x["margin"] else 0.0
            cards.append(dict(coin=bot.coin(x["sym"]), long=x["side"] == "LONG", lev=x["lev"], pnl=dinero(x["pnl"]), up=x["pnl"] >= 0,
                              pct=f"{pct:+.1f} %", sub=f"{who} · " + ("sin SL" if x["risk"] is None else f"riesgo ${x['risk']:,.2f}"),
                              warn=x["risk"] is None, soltada=bool(r and r.get("soltada")), tid=x["tid"]))
        return dict(
            v=PANEL_VERSION, sym=sym, px=fmt(px) if px else "", follow=self.cfg["seguir_tv"], mode=self.cfg["modo"],
            keys=bool(bot.bx_keys()), folded=self.cfg["plegado"], top=self.cfg["encima"], loading=self.loading and not rows,
            tfs=tfs, sel=sel, ups=f"{ups}/{len(rows)}" if rows else "",
            sig=dict(tf=sel, side=s["side"], estado=s["estado"], entry=fmt(s["e2"] if s["limit"] else s["e1"]), limit=s["limit"],
                     sl=fmt(s["sl"]), tp1=fmt(s["tp1"])) if s and s["ok"] else None,
            why=(f"{sel}: {s['estado']}" if s else f"{sel}: sin señal") if sel else "Sin señales para entrar en esta moneda",
            pos=dict(n=len(prows), mine=sum(1 for x in prows if x["r"]), tot=dinero(tot), up=tot >= 0, risk=f"{pr:.1f} %",
                     over=self.cfg["seguro"] and pr > lim, near=pr > lim * 0.7, lim=f"{lim:g} %", err=pos["err"], cards=cards),
            toast=dict(text=f"{last[1]} {last[2]}"[:180], age=time.time() - last[0]) if last else None,
            logs=out_logs)


# ----------------------------- SERVIDOR LOCAL (solo esta PC) -----------------------------
class Handler(BaseHTTPRequestHandler):
    app, key, hosts = None, "", set()

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self, need_key=True):
        """Solo 127.0.0.1 con el Host exacto (contra DNS rebinding) y la clave de esta sesión (contra páginas web ajenas)."""
        if self.headers.get("Host") not in self.hosts:
            return False
        return not need_key or secrets.compare_digest(self.headers.get("X-Token", ""), self.key)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            if not self._allowed(False) or not secrets.compare_digest(parse_qs(u.query).get("t", [""])[0], self.key):
                return self._send(403, b"BRIDGE", "text/plain")
            return self._send(200, PAGINA.replace("__TOKEN__", self.key).encode(), "text/html; charset=utf-8")
        if not self._allowed():
            return self._send(403, {"ok": False})
        if u.path == "/api/state":
            return self._send(200, self.app.snapshot(logs="logs" in u.query))
        if u.path == "/api/settings":
            return self._send(200, self.app.settings_get())
        return self._send(404, {"ok": False})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"ok": False})
        n = min(int(self.headers.get("Content-Length") or 0), 65536)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"ok": False})
        a, p = self.app, urlparse(self.path).path
        try:
            if p == "/api/pick":
                a.pick(str(d.get("tf")))
                out = {"ok": True}
            elif p == "/api/aim":
                out = a.aim(str(d.get("tf")))
            elif p == "/api/confirm":
                out = a.confirm(str(d.get("aid")))
            elif p == "/api/mode_check":
                out = a.mode_check()
            elif p == "/api/mode":
                out = a.set_mode(bool(d.get("real")))
            elif p == "/api/settings":
                out = a.settings_set(d)
            elif p == "/api/symbol":
                out = {"ok": a.set_symbol(str(d.get("sym") or ""), manual=True)}
            elif p == "/api/follow":
                a.follow(d.get("on"))
                if d.get("on"):
                    a.want_sig.set()
                out = {"ok": True}
            elif p == "/api/fold":
                a.fold(d.get("on"))
                out = {"ok": True}
            elif p == "/api/top":
                a.top(d.get("on"))
                out = {"ok": True}
            elif p == "/api/refresh":
                a.want_sig.set()
                a.pos["t"] = 0
                out = {"ok": True}
            elif p == "/api/devolver":
                out = {"ok": True, "msg": a.bot.bx_devolver(str(d.get("tid")))}
            elif p == "/api/close":
                a.shutdown()
                out = {"ok": True}
            else:
                return self._send(404, {"ok": False})
        except Exception as e:
            out = {"ok": False, "why": str(e)[:200], "msg": str(e)[:200]}
        return self._send(200, out)


# ----------------------------- INTERFAZ (HTML) -----------------------------
PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>BRIDGE</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0b0d11;--card:#14171d;--card2:#1b1f27;--line:#232833;--txt:#eef0f4;--dim:#9098a8;--mute:#5f6777;
--g1:#2fd36b;--g2:#16a34a;--gt:#4ade80;--gb:rgba(34,197,94,.13);--r1:#f25757;--r2:#dc2626;--rt:#f87171;--rb:rgba(239,68,68,.13);
--am:#fbbf24;--ab:rgba(251,191,36,.12)}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--txt);font:13px/1.35 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;
-webkit-font-smoothing:antialiased;user-select:none;overflow:hidden}
body{padding:12px 12px 10px;display:flex;flex-direction:column}
.num{font-variant-numeric:tabular-nums}
header{display:flex;align-items:center;gap:8px;height:28px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--mute);flex:none}
.dot.on{background:var(--g1);box-shadow:0 0 0 3px rgba(47,211,107,.18),0 0 10px rgba(47,211,107,.6)}
.brand{font:700 12.5px "Segoe UI Variable Display","Segoe UI",system-ui;letter-spacing:.2em}
.pill{font:700 9.5px "Segoe UI",system-ui;letter-spacing:.08em;padding:3px 9px;border-radius:999px;cursor:pointer;border:1px solid transparent;transition:.15s}
.pill.test{color:var(--am);background:var(--ab);border-color:rgba(251,191,36,.28)}
.pill.real{color:#fff;background:linear-gradient(180deg,var(--r1),var(--r2));box-shadow:0 0 12px rgba(239,68,68,.45)}
.pill:hover{filter:brightness(1.15)}
.icons{margin-left:auto;display:flex;gap:2px}
.ib{width:26px;height:26px;border-radius:8px;display:grid;place-items:center;color:var(--dim);cursor:pointer;background:none;border:0;transition:.15s}
.ib:hover{background:var(--card2);color:var(--txt)} .ib.on{color:var(--txt)}
.ib svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
main{flex:1;display:flex;flex-direction:column;min-height:0;margin-top:10px}
.folded main{display:none}
.symrow{display:flex;align-items:center;justify-content:space-between;margin:0 2px 8px}
.sym{font:650 14px "Segoe UI Variable Display","Segoe UI",system-ui;cursor:pointer;display:flex;align-items:center;gap:6px}
.sym .tv{font-size:9px;font-weight:700;letter-spacing:.06em;color:var(--mute);border:1px solid var(--line);border-radius:5px;padding:1px 4px}
.sym .tv.on{color:#7cb4ff;border-color:rgba(124,180,255,.35)}
.px{color:var(--dim);font-size:12px}
.aim{width:100%;border:0;border-radius:13px;padding:11px 10px 10px;cursor:pointer;color:#fff;text-align:center;
font:700 17px "Segoe UI Variable Display","Segoe UI",system-ui;letter-spacing:.01em;
background:linear-gradient(180deg,var(--g1),var(--g2));box-shadow:0 8px 22px -8px rgba(34,197,94,.7),inset 0 1px 0 rgba(255,255,255,.28);
transition:transform .08s,filter .15s,box-shadow .2s}
.aim:hover{filter:brightness(1.08)} .aim:active{transform:scale(.975)}
.aim.short{background:linear-gradient(180deg,var(--r1),var(--r2));box-shadow:0 8px 22px -8px rgba(239,68,68,.7),inset 0 1px 0 rgba(255,255,255,.28)}
.aim:disabled{background:var(--card2);color:var(--mute);box-shadow:inset 0 0 0 1px var(--line);cursor:default;transform:none;filter:none}
.aim small{display:block;font:600 10.5px "Segoe UI",system-ui;opacity:.9;margin-top:2px;letter-spacing:.03em}
.chips{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin-top:10px}
.chip{font:700 10.5px "Segoe UI",system-ui;text-align:center;padding:6px 0 5px;border-radius:9px;background:var(--card);color:var(--mute);
cursor:pointer;border:1px solid transparent;transition:.15s;position:relative}
.chip:hover{border-color:var(--line);color:var(--dim)}
.chip.long{background:var(--gb);color:var(--gt)} .chip.short{background:var(--rb);color:var(--rt)}
.chip.gl{color:rgba(74,222,128,.6)} .chip.gs{color:rgba(248,113,113,.6)}
.chip.sel{border-color:rgba(255,255,255,.6);box-shadow:0 0 0 1px rgba(255,255,255,.15)}
.chip i{position:absolute;top:3px;right:4px;width:4px;height:4px;border-radius:50%;background:currentColor;opacity:.8}
.detail{margin:8px 2px 0;color:var(--dim);font-size:11px;min-height:15px;display:flex;justify-content:space-between;gap:6px}
.detail b{color:var(--txt);font-weight:600}
.card{margin-top:12px;background:var(--card);border-radius:15px;border:1px solid var(--line);display:flex;flex-direction:column;min-height:0;flex:1}
.cardh{display:flex;justify-content:center;align-items:center;gap:6px;color:var(--dim);font-size:11px;padding:9px 12px 4px;cursor:pointer}
.cardh svg{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2.2;transition:.2s}
.cardh.closed svg{transform:rotate(-90deg)}
.tot{padding:2px 14px 8px}
.kv{display:flex;justify-content:space-between;align-items:baseline;color:var(--dim);font-size:12px;margin:3px 0}
.kv b{font:700 15px "Segoe UI Variable Display","Segoe UI",system-ui;color:var(--txt)}
.kv b.small{font-size:13px}
.list{overflow:auto;padding:0 14px 6px;min-height:0;flex:1}
.list::-webkit-scrollbar{width:6px}.list::-webkit-scrollbar-thumb{background:var(--line);border-radius:6px}
.pos{padding:9px 0 8px;border-top:1px solid var(--line)}
.row{display:flex;justify-content:space-between;align-items:center;gap:6px}
.coin{font:700 13.5px "Segoe UI Variable Display","Segoe UI",system-ui;display:flex;align-items:center}
.badge{font:700 9.5px "Segoe UI",system-ui;padding:2px 6px;border-radius:6px;margin-left:7px}
.badge.l{color:var(--gt);background:var(--gb)} .badge.s{color:var(--rt);background:var(--rb)}
.pnl{font:700 13.5px "Segoe UI Variable Display","Segoe UI",system-ui}
.up,.kv b.up{color:var(--gt)} .dn,.kv b.dn{color:var(--rt)} .warn,.kv b.warn{color:var(--am)} .over,.kv b.over{color:var(--rt)}
.sub{color:var(--mute);font-size:10.5px;margin-top:3px}
.mini{font:600 10px "Segoe UI",system-ui;color:var(--txt);background:var(--card2);border:1px solid var(--line);border-radius:6px;padding:1px 6px;cursor:pointer;margin-left:6px}
.empty{color:var(--mute);font-size:11.5px;text-align:center;padding:14px 0 10px}
.err{color:var(--am);font-size:11px;text-align:center;padding:10px 4px}
.toast{margin-top:8px;color:var(--mute);font-size:10.5px;line-height:1.3;min-height:27px;transition:opacity .8s;cursor:pointer;
display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.skel{background:linear-gradient(90deg,var(--card) 0%,var(--card2) 50%,var(--card) 100%);background-size:200% 100%;animation:sh 1.2s infinite}
@keyframes sh{to{background-position:-200% 0}}
/* hojas (confirmar, ajustes, registro) */
.modal{position:fixed;inset:0;background:rgba(4,5,8,.66);backdrop-filter:blur(5px);display:none;align-items:flex-end;z-index:9}
.modal.on{display:flex}
.sheet{width:100%;background:var(--card);border:1px solid var(--line);border-bottom:0;border-radius:18px 18px 0 0;padding:16px 14px 14px;
max-height:94vh;overflow:auto;animation:up .2s cubic-bezier(.2,.8,.2,1)}
@keyframes up{from{transform:translateY(24px);opacity:0}to{transform:none;opacity:1}}
.sheet h3{font:700 15px "Segoe UI Variable Display","Segoe UI",system-ui;margin-bottom:2px;display:flex;align-items:center;gap:8px}
.sheet .lead{color:var(--dim);font-size:11.5px;margin-bottom:12px}
.tbl{width:100%;border-collapse:collapse;font-size:12px;margin:6px 0 10px}
.tbl td{padding:5px 0;border-bottom:1px solid var(--line)} .tbl td:last-child{text-align:right}
.tbl td.k{color:var(--dim)}
.note{font-size:11px;color:var(--dim);background:var(--card2);border-radius:10px;padding:8px 10px;margin:8px 0 12px;line-height:1.4}
.note.red{color:var(--rt);background:var(--rb)}
.btns{display:flex;gap:8px;margin-top:6px}
.btn{flex:1;border:0;border-radius:11px;padding:10px;font:700 13px "Segoe UI",system-ui;cursor:pointer;color:var(--txt);background:var(--card2)}
.btn:hover{filter:brightness(1.15)}
.btn.go{color:#fff;background:linear-gradient(180deg,var(--g1),var(--g2))} .btn.stop{color:#fff;background:linear-gradient(180deg,var(--r1),var(--r2))}
.field{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:7px 0;font-size:12px;color:var(--dim)}
.field input[type=text],.field input[type=password],.field select{width:110px;background:var(--bg);border:1px solid var(--line);color:var(--txt);
border-radius:8px;padding:6px 8px;font:600 12px "Segoe UI",system-ui;outline:none;text-align:right}
.field input:focus,.field select:focus{border-color:#4b8bff}
.field.wide input{width:100%;text-align:left}
.sw{position:relative;width:34px;height:20px;flex:none}
.sw input{opacity:0;width:0;height:0}
.sw span{position:absolute;inset:0;background:var(--line);border-radius:20px;transition:.2s;cursor:pointer}
.sw span:before{content:"";position:absolute;width:14px;height:14px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s}
.sw input:checked+span{background:var(--g2)} .sw input:checked+span:before{transform:translateX(14px)}
.sec{font:700 10px "Segoe UI",system-ui;letter-spacing:.1em;color:var(--mute);margin:14px 0 4px}
.logs{font:11px/1.45 Consolas,monospace;color:var(--dim);white-space:pre-wrap;max-height:60vh;overflow:auto;background:var(--bg);border-radius:10px;padding:8px}
.symin{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:10px;padding:10px;font:700 15px "Segoe UI",system-ui;outline:none;text-transform:uppercase}
</style></head>
<body>
<header>
  <span class="dot" id="dot" title="Conexión con Bitunix"></span><span class="brand">BRIDGE</span>
  <span class="pill test" id="mode" title="Toca para cambiar entre PRUEBA y REAL">PRUEBA</span>
  <div class="icons">
    <button class="ib" id="bset" title="Ajustes"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg></button>
    <button class="ib" id="bpin" title="Siempre encima"><svg viewBox="0 0 24 24"><path d="M12 17v5"/><path d="M9 10.8V5h6v5.8l2.5 3.2h-11z"/><path d="M8 5h8"/></svg></button>
    <button class="ib" id="bfold" title="Plegar"><svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg></button>
  </div>
</header>
<main>
  <div class="symrow"><span class="sym" id="sym" title="Toca para escribir otra moneda">—<span class="tv">TV</span></span><span class="px num" id="px"></span></div>
  <button class="aim" id="aim" disabled>Entrar<small>Cargando…</small></button>
  <div class="chips" id="chips"></div>
  <div class="detail" id="detail"></div>
  <div class="card">
    <div class="cardh" id="ph"><svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg><span id="ptitle">Posiciones</span></div>
    <div class="tot">
      <div class="kv"><span>Total no realizado</span><b class="num" id="tot">—</b></div>
      <div class="kv"><span>Riesgo abierto</span><b class="num small" id="risk">—</b></div>
    </div>
    <div class="list" id="list"></div>
  </div>
  <div class="toast" id="toast" title="Toca para ver el registro"></div>
</main>
<div class="modal" id="modal"><div class="sheet" id="sheet"></div></div>
<script>
const T="__TOKEN__"; let S=null, listOpen=true, busy=false;
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(p,b){const r=await fetch(p,{method:b?"POST":"GET",headers:{"X-Token":T,"Content-Type":"application/json"},body:b?JSON.stringify(b):undefined});return r.json()}
function sheet(h){$("sheet").innerHTML=h;$("modal").classList.add("on")}
function closeSheet(){$("modal").classList.remove("on");busy=false}
$("modal").onclick=e=>{if(e.target.id==="modal")closeSheet()};
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeSheet()});

function render(){
  const s=S;
  document.body.classList.toggle("folded",s.folded);
  $("dot").classList.toggle("on",s.keys);
  const m=$("mode"); m.textContent=s.mode==="si"?"REAL":"PRUEBA"; m.className="pill "+(s.mode==="si"?"real":"test");
  $("bpin").classList.toggle("on",s.top);
  $("bfold").innerHTML=s.folded?'<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>':'<svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg>';
  $("sym").innerHTML=esc(s.sym)+'<span class="tv'+(s.follow?' on':'')+'" title="Sigue la moneda de TradingView">TV</span>';
  $("px").textContent=s.px;
  const a=$("aim");
  if(s.sig){a.disabled=false;a.className="aim"+(s.sig.side<0?" short":"");
    a.innerHTML="Entrar<small>"+(s.sig.side>0?"LONG":"SHORT")+" · "+esc(s.sig.tf)+" · "+esc(s.sig.estado)+"</small>";
    $("detail").innerHTML='<span>Entrada <b class="num">'+esc(s.sig.entry)+'</b>'+(s.sig.limit?' (límite)':'')+'</span><span>SL <b class="num">'+esc(s.sig.sl)+'</b></span><span>TP1 <b class="num">'+esc(s.sig.tp1)+'</b></span>';
  }else{a.disabled=true;a.className="aim";a.innerHTML="Entrar<small>"+(s.loading?"Cargando señales…":esc(s.why))+"</small>";
    $("detail").innerHTML=s.ups?'<span>Tendencial ▲ en <b>'+esc(s.ups)+'</b> temporalidades</span>':"";}
  $("chips").innerHTML=s.tfs.map(t=>{let c="chip";
    if(!t.has&&s.loading)c+=" skel"; else if(t.ok)c+=t.side>0?" long":" short"; else if(t.side)c+=t.side>0?" gl":" gs";
    if(t.tf===s.sel)c+=" sel";
    const tip=t.side?(t.side>0?"LONG":"SHORT")+" · "+t.estado+" · entrada "+t.entry+" · SL "+t.sl:(t.has?(t.up?"Tendencial ▲":"Tendencial ▼")+" · sin señal":"");
    return '<div class="'+c+'" data-tf="'+t.tf+'" title="'+esc(tip)+'">'+t.tf+(t.ok?"<i></i>":"")+'</div>'}).join("");
  const p=s.pos;
  $("ptitle").textContent="Posiciones ("+p.n+")"+(p.mine?" · "+p.mine+" en el panel":"");
  $("ph").classList.toggle("closed",!listOpen);
  const tot=$("tot"); tot.textContent=p.err?"—":p.tot; tot.className="num "+(p.err?"":p.up?"up":"dn");
  const rk=$("risk"); rk.textContent=p.err?"—":p.risk+"  /  "+p.lim; rk.className="num small "+(p.over?"over":p.near?"warn":"");
  let h="";
  if(p.err)h='<div class="err">'+esc(p.err)+'</div>';
  else if(!p.cards.length)h='<div class="empty">Sin posiciones abiertas</div>';
  else h=p.cards.map(c=>'<div class="pos"><div class="row"><span class="coin">'+esc(c.coin)+'<span class="badge '+(c.long?"l":"s")+'">'+(c.long?"L":"S")+" "+esc(c.lev)+'x</span></span><span class="pnl num '+(c.up?"up":"dn")+'">'+esc(c.pnl)+'</span></div>'+
     '<div class="row sub"><span class="'+(c.warn?"warn":"")+'">'+esc(c.sub)+(c.soltada?'<span class="mini" data-tid="'+esc(c.tid)+'">↩ Devolver</span>':'')+'</span><span class="num '+(c.up?"up":"dn")+'">'+esc(c.pct)+'</span></div></div>').join("");
  $("list").innerHTML=h; $("list").style.display=listOpen?"":"none";
  const t=$("toast"); if(s.toast){t.textContent=s.toast.text;t.style.opacity=s.toast.age<25?1:.45}else t.textContent="";
}
async function tick(){try{S=await api("/api/state");render()}catch(e){$("toast").textContent="Reconectando con el panel…"}setTimeout(tick,1000)}

$("chips").onclick=async e=>{const c=e.target.closest(".chip");if(!c)return;await api("/api/pick",{tf:c.dataset.tf});S=await api("/api/state");render()};
$("list").onclick=async e=>{const b=e.target.closest(".mini");if(!b)return;const r=await api("/api/devolver",{tid:b.dataset.tid});sheet('<h3>↩ Devolver</h3><p class="lead">'+esc(r.msg||r.why)+'</p><div class="btns"><button class="btn" onclick="closeSheet()">Listo</button></div>')};
$("ph").onclick=()=>{listOpen=!listOpen;render()};
$("bfold").onclick=async()=>{await api("/api/fold",{on:!S.folded});S.folded=!S.folded;render()};
$("bpin").onclick=async()=>{await api("/api/top",{on:!S.top});S.top=!S.top;render()};
$("toast").onclick=async()=>{const r=await api("/api/state?logs=1");sheet('<h3>Registro</h3><p class="lead">Últimos avisos del panel y de Bitunix</p><div class="logs" id="lg">'+esc((r.logs||[]).join("\n"))+'</div><div class="btns"><button class="btn" onclick="closeSheet()">Cerrar</button></div>');const l=$("lg");l.scrollTop=l.scrollHeight};
$("sym").onclick=e=>{
  if(e.target.classList.contains("tv")){api("/api/follow",{on:!S.follow});return}
  sheet('<h3>Moneda</h3><p class="lead">Escribe la moneda (ej. SOL o SOLUSDT). Deja de seguir TradingView hasta que toques TV.</p><input class="symin" id="si" value="'+esc(S.sym)+'"><div class="btns" style="margin-top:12px"><button class="btn" onclick="closeSheet()">Cancelar</button><button class="btn go" id="sok">Ver</button></div>');
  const i=$("si");i.focus();i.select();const go=async()=>{await api("/api/symbol",{sym:i.value});closeSheet()};$("sok").onclick=go;i.onkeydown=e=>{if(e.key==="Enter")go()}};
$("aim").onclick=async()=>{
  if(busy||!S.sig)return;busy=true;const tf=S.sig.tf;$("aim").innerHTML="Revisando…<small>precio y límites en Bitunix</small>";
  let r;try{r=await api("/api/aim",{tf})}catch(e){r={ok:false,why:"Sin conexión con el panel"}}
  if(!r.ok){sheet('<h3>No se puede entrar</h3><p class="lead">'+esc(tf)+'</p><div class="note">'+esc(r.why)+'</div><div class="btns"><button class="btn" onclick="closeSheet()">Entendido</button></div>');return}
  const long=r.side==="LONG";
  const legs=r.legs.map(l=>'<tr><td class="k">'+esc(l.name)+' '+esc(l.how)+'</td><td class="num">'+esc(l.qty)+(l.how==="límite"?' @ '+esc(l.price):'')+'</td></tr>').join("");
  sheet('<h3><span class="badge '+(long?"l":"s")+'" style="margin:0">'+r.side+' '+r.lev+'x</span>'+esc(r.sym)+' · '+esc(r.tf)+'</h3>'+
    '<p class="lead">'+(r.real?'<b class="dn">REAL</b> · se envía a Bitunix al confirmar':'<b class="warn">PRUEBA</b> · no se enviará nada')+'</p>'+
    '<table class="tbl">'+legs+'<tr><td class="k">Stop loss</td><td class="num dn">'+esc(r.sl)+'</td></tr><tr><td class="k">TP 40/30/30</td><td class="num up">'+r.tps.map(esc).join(" · ")+'</td></tr><tr><td class="k">Margen</td><td class="num">'+esc(r.margin)+'</td></tr></table>'+
    '<div class="note'+(/Pasarías/.test(r.risk)?' red':'')+'">'+esc(r.risk)+'</div>'+
    '<div class="btns"><button class="btn" onclick="closeSheet()">Cancelar</button><button class="btn '+(long?"go":"stop")+'" id="cok">Confirmar</button></div>');
  let left=r.ttl;const cok=$("cok"),tm=setInterval(()=>{left--;if(!$("cok")){clearInterval(tm);return}cok.textContent="Confirmar ("+left+")";if(left<=0){clearInterval(tm);closeSheet()}},1000);
  cok.onclick=async()=>{clearInterval(tm);cok.textContent="Enviando…";const c=await api("/api/confirm",{aid:r.aid});
    sheet('<h3>'+(c.ok?"✓ Listo":"No se envió")+'</h3><div class="note'+(c.ok?'':' red')+'">'+esc(c.msg)+'</div><div class="btns"><button class="btn" onclick="closeSheet()">Cerrar</button></div>')}};
$("mode").onclick=async()=>{
  if(S.mode==="si"){await api("/api/mode",{real:false});return}
  const r=await api("/api/mode_check",{});
  if(!r.ok){sheet('<h3>Modo REAL</h3><div class="note red">'+esc(r.why)+'</div><div class="btns"><button class="btn" onclick="closeSheet()">Entendido</button></div>');return}
  sheet('<h3>¿Activar modo REAL?</h3><p class="lead">Al confirmar Entrar se envían órdenes de verdad a Bitunix.</p><table class="tbl"><tr><td class="k">Capital</td><td class="num">$'+r.capital+'</td></tr><tr><td class="k">Riesgo por operación</td><td class="num">'+r.riesgo+' % = $'+(r.capital*r.riesgo/100).toFixed(2)+'</td></tr><tr><td class="k">Margen máximo</td><td class="num">$'+r.max+'</td></tr></table><div class="btns"><button class="btn" onclick="closeSheet()">Cancelar</button><button class="btn stop" id="rok">Activar REAL</button></div>');
  $("rok").onclick=async()=>{const x=await api("/api/mode",{real:true});if(x.ok)closeSheet();else sheet('<div class="note red">'+esc(x.why)+'</div><div class="btns"><button class="btn" onclick="closeSheet()">Cerrar</button></div>')}};
$("bset").onclick=async()=>{
  const c=await api("/api/settings");
  const num=(k,l)=>'<label class="field"><span>'+l+'</span><input type="text" id="f_'+k+'" value="'+esc(c[k])+'"></label>';
  const sw=(k,l)=>'<label class="field"><span>'+l+'</span><span class="sw"><input type="checkbox" id="f_'+k+'"'+(c[k]?" checked":"")+'><span></span></span></label>';
  sheet('<h3>Ajustes</h3><p class="lead">Se guardan solo en esta PC</p>'+
   '<div class="sec">TU CUENTA</div>'+num("capital","Capital (USDT)")+num("riesgo","Riesgo por operación (%)")+num("max","Margen máximo por operación")+num("lev","Apalancamiento máx. (0 = el de la señal)")+num("perdida","Pérdida máxima del día (%)")+
   '<div class="sec">SEGURIDAD</div>'+sw("seguro","Seguro de riesgo")+num("seguro_lim","Avisar si el riesgo abierto pasa (%)")+sw("telegram","Avisos también por Telegram")+
   '<div class="sec">GESTIÓN DE LA POSICIÓN</div><label class="field"><span>Gestión</span><select id="f_gestion"><option value="bot"'+(c.gestion!=="strike"?" selected":"")+'>Bot (TP1→BE)</option><option value="strike"'+(c.gestion==="strike"?" selected":"")+'>STRIKE</option></select></label>'+
   num("berr","STRIKE · break even a (R)")+num("parcial","STRIKE · parcial al BE (%)")+sw("comp","STRIKE · BE compensado")+
   '<div class="sec">LLAVES DE BITUNIX</div><div class="note">Solo permiso de trading, sin retiros.'+(c.key_hint?' Guardada: <b>'+esc(c.key_hint)+'</b>. Déjalas vacías para no cambiarlas.':'')+'</div>'+
   '<label class="field wide"><input type="password" id="f_key" placeholder="API key" autocomplete="off"></label><label class="field wide"><input type="password" id="f_secret" placeholder="Secret key" autocomplete="off"></label>'+
   '<div id="ferr"></div><div class="btns" style="margin-top:12px"><button class="btn" onclick="closeSheet()">Cancelar</button><button class="btn go" id="fok">Guardar</button></div>');
  $("fok").onclick=async()=>{const d={};["capital","riesgo","max","lev","perdida","seguro_lim","berr","parcial","key","secret"].forEach(k=>d[k]=$("f_"+k).value);
    ["seguro","telegram","comp"].forEach(k=>d[k]=$("f_"+k).checked);d.gestion=$("f_gestion").value;
    const r=await api("/api/settings",d);if(r.ok)closeSheet();else $("ferr").innerHTML='<div class="note red">'+esc(r.why)+'</div>'}};
tick();
</script></body></html>
"""


# ----------------------------- ARRANQUE -----------------------------
def preparar_bot(log):
    """Importa bot.py (mismo motor) y lo deja listo para el panel: archivo de operaciones propio y avisos al registro."""
    import bot
    if tuple(int(x) for x in bot.VERSION.split(".")[:2]) < (6, 4):
        raise RuntimeError(f"Tu bot.py es v{bot.VERSION} y el panel necesita v6.4 o más. ¿Abriste iniciar_bot.bat antes de "
                           "subir la v6.4 a GitHub? Ese archivo baja bot.py de GitHub y reemplaza el de esta carpeta.")
    bot.BX_FILE = os.path.join(AQUI, "bx_panel.json")  # operaciones del panel (no se mezclan con las del bot de Telegram)
    bot._bx.update(trades={}, journal=[], day={})
    bot.bx_load()
    tg_send = bot.send

    def send(token, chat_id, text, buttons=None, silent=False):
        log(text)  # todo aviso del motor (órdenes, TP, BE, soltada, seguro) aparece en el registro
        if token and chat_id and CFG.get("telegram", True):
            try:  # sin botones: en Telegram los botones los atiende el bot del servidor, no este panel
                return tg_send(token, chat_id, f"🖥 <b>{NOMBRE}</b> · " + text, None, silent)
            except Exception as e:
                log(f"Telegram: {e}")
    bot.send = send
    return bot


_MUTEX = []      # mantiene vivo el candado de "un solo panel"


def ya_abierto():
    """Un solo panel a la vez (dos vigilando las mismas operaciones se pisarían). Si ya hay uno, lo trae al frente."""
    if sys.platform != "win32":
        return False
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    _MUTEX.append(k.CreateMutexW(None, False, "Local\\BRIDGE_panel_bridge_tactico"))
    if ctypes.get_last_error() != 183:  # 183 = ERROR_ALREADY_EXISTS
        return False
    h = ventana_panel()
    if h:
        ctypes.windll.user32.ShowWindow(h, 9)          # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(h)
    return True


def abrir_ventana(url, cfg):
    """Ventana pequeña de Edge (o Chrome) en modo aplicación: sin pestañas ni barra de direcciones."""
    exe = navegador()
    if not exe:
        import webbrowser
        webbrowser.open(url)
        return False
    perfil = os.path.join(os.environ.get("LOCALAPPDATA") or AQUI, "BRIDGE", "ventana")
    x, y = None, None
    try:
        x, y = (int(v) for v in str(cfg.get("pos") or "").split(","))
    except ValueError:
        pass
    if x is None:
        import ctypes
        x, y = ctypes.windll.user32.GetSystemMetrics(0) - WIN_W - 30, 90
    alto = FOLD_H if cfg.get("plegado") else int(cfg.get("alto") or WIN_H)
    subprocess.Popen([exe, f"--app={url}", f"--window-size={WIN_W},{alto}", f"--window-position={x},{y}", f"--user-data-dir={perfil}",
                      "--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-extensions",
                      "--disable-features=Translate,msEdgeSidebarV2,msHubApps,EdgeCollections"],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def servir(app, port=0):
    """Servidor local SOLO en 127.0.0.1 con una clave nueva por sesión."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    p = srv.server_address[1]
    Handler.app, Handler.key = app, secrets.token_urlsafe(24)
    Handler.hosts = {f"127.0.0.1:{p}", f"localhost:{p}"}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{p}/?t={Handler.key}"


CFG = {}
_APP = []
_ANTES = []


def LOG(text):
    (_APP[0].log if _APP else _ANTES.append)(text)


def main():
    if ya_abierto():
        return
    CFG.update(cargar_json(PANEL_FILE, DEFAULTS))
    keys = cargar_json(KEYS_FILE)
    poner_llaves(keys.get("key", ""), keys.get("secret", ""))
    try:
        bot = preparar_bot(LOG)
    except Exception as e:
        mensaje_error(str(e))
        return
    token = chat = ""
    try:
        token, chat, _ = bot.load_credentials(False)
    except SystemExit:
        pass  # sin Telegram: los avisos quedan solo en el registro
    app = App(bot, CFG, token, chat)
    _APP.append(app)
    for t in _ANTES:
        app.log(t)
    app.log(f"{NOMBRE} v{PANEL_VERSION} · motor bot.py v{bot.VERSION} · " +
            ("llaves de Bitunix OK" if bot.bx_keys() else "sin llaves de Bitunix: toca ⚙ para ponerlas"))
    srv, url = servir(app)
    for fn in (app.loop_signals, app.loop_positions, app.loop_tv, app.vigilante):
        threading.Thread(target=fn, daemon=True).start()
    app.want_sig.set()
    if abrir_ventana(url, CFG):
        app.loop_window()  # vive mientras la ventana esté abierta
    else:
        app.stop.wait()


if __name__ == "__main__":
    main()
