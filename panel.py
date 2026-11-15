#!/usr/bin/env python3
"""
BRIDGE · panel flotante para TradingView (Windows) · v1.1

Un widget pequeño que queda ENCIMA de TradingView (como STRIKE):
  - detecta la moneda que tienes abierta en TradingView (por el título de la ventana)
  - etiquetas de temporalidad 1m…1D: verde = LONG para apuntar, rojo = SHORT (clic para elegir otra)
  - botón APUNTAR: abre la señal elegida en Bitunix (E1/E2/E3, SL y TP) después de confirmar
  - tus posiciones en Bitunix: total no realizado, riesgo abierto y cada posición con su PnL
  - vigila las operaciones que abriste desde aquí: TP, break even, gestión STRIKE, "soltar" si cambias algo a mano
  - se mueve arrastrando el encabezado · ▾ lo pliega · clic derecho: tabla completa y registro, siempre encima, cerrar

Usa EXACTAMENTE el motor de bot.py (señales, plan, gestión), así que necesita bot.py v6.4 o más en esta carpeta.
Sin librerías extra: doble clic en iniciar_panel.bat.

Las llaves de Bitunix del panel se guardan en panel_llaves.json (solo en esta PC, nunca en GitHub) y NO cambian
qué equipo manda en el bot de Telegram. Crea la llave en Bitunix solo con permiso de trading (sin retiros).
"""
import html
import json
import os
import queue
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import tkinter as tk
from tkinter import messagebox

PANEL_VERSION = "1.1"
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
PANEL_FILE = os.path.join(AQUI, "panel.json")          # ajustes (sin llaves)
KEYS_FILE = os.path.join(AQUI, "panel_llaves.json")    # llaves de Bitunix solo para el panel
NOMBRE = "BRIDGE"

# Colores (oscuro, como TradingView)
BG, CARD, LINE, TXT, DIM = "#16181d", "#22252d", "#2e323c", "#e6e8ec", "#8a8f9c"
GREEN, RED, AMBER, BLUE = "#4ade80", "#f87171", "#fbbf24", "#2962ff"
GREEN_B, RED_B = "#22c55e", "#ef4444"                       # botón Apuntar
GREEN_D, RED_D, AMBER_D = "#163a26", "#3d1a1a", "#3a2e10"   # fondos de etiquetas

DEFAULTS = dict(capital=500.0, riesgo=1.0, max=10.0, lev=0, perdida=5.0, modo="prueba", gestion="bot", berr=1.0, parcial=25.0,
                comp=True, seguro=True, seguro_lim=5.0, seguir_tv=True, moneda="BTCUSDT", telegram=True, encima=True,
                tfs=["1m", "3m", "5m", "15m", "1h", "2h", "4h", "8h", "1d"], pos="", plegado=False)


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


# ----------------------------- TRADINGVIEW: MONEDA ABIERTA -----------------------------
SYM_RE = re.compile(r"(?:^|[\s:])([A-Z0-9]{2,20}?(?:USDT|USDC))(?:\.P|PERP)?(?=\s|$|,)")


def moneda_de_titulo(title):
    """'ETHUSDT.P ▲ 1.823,25 +3.1% / Sem nome' → 'ETHUSDT' · 'BINANCE:SOLUSDT.P 141.5 …' → 'SOLUSDT'."""
    m = SYM_RE.search((title or "").upper())
    return m.group(1) if m else None


def ventanas_tradingview():
    """Títulos de las ventanas de TradingView (app de escritorio o navegador), de la de más arriba a la de más abajo."""
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
                if exe == "tradingview.exe" or "tradingview" in b.value.lower():
                    out.append(b.value)
        return True

    u.EnumWindows(cb, 0)
    return out


def moneda_tradingview():
    for t in ventanas_tradingview():
        s = moneda_de_titulo(t)
        if s:
            return s
    return None


# ----------------------------- SEÑALES (motor del bot) -----------------------------
def limpiar(text):
    """HTML de Telegram → texto plano para el registro del panel."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def fila_tf(bot, st, sym, tf):
    """Una fila de la tabla: dirección de la Tendencial, puntos y la señal activa (si hay) de esa temporalidad."""
    res = bot.compute(sym, tf, st, want=set())  # want vacío: sin historial largo (rápido)
    d, T = res["d"], res["T"]
    row = dict(tf=tf, up=res["up"], pts=res["pts"], px=d[-1]["c"], sig=None)
    if not T:
        return row
    ev = next((e for e in res["events"] if e["kind"] == "OPEN" and e["bar"] == T["bar"]), None)
    age = len(d) - 1 - T["bar"]
    tid = f"panel_{sym}_{tf}_{d[T['bar']]['t']}"
    sig = dict(side=T["side"], e1=T["e1"], e2=T["e2"], sl=T["sl"], tp1=T["tps"][0], tp=T["tp"], age=age, tid=tid, ev=ev,
               ct=d[T["bar"]]["ct"], limit=bool(ev and ev.get("limit")), filled=T["pos"] > 0, q=T.get("q"))
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


# ----------------------------- VENTANA (widget compacto, como STRIKE) -----------------------------
W_PX = 240          # ancho del widget
MAX_POS = 7         # posiciones visibles (las de mayor PnL absoluto)
TOAST_S = 25        # segundos que se ve el último aviso


def elegir_tf(rows, tfs, actual=None):
    """Temporalidad para el botón Apuntar: la que eligió el usuario si tiene señal válida; si no, la señal válida más
    reciente (en empate, la temporalidad mayor)."""
    ok = [(r["sig"]["age"], -tfs.index(tf), tf) for tf, r in rows.items() if r.get("sig") and r["sig"]["ok"]]
    if actual and any(t == actual for _, _, t in ok):
        return actual
    return min(ok)[2] if ok else None


class Panel:
    def __init__(self, root, bot, cfg, token="", chat=""):
        self.root, self.bot, self.cfg, self.token, self.chat = root, bot, cfg, token, chat
        self.q = queue.Queue()
        self.pool = ThreadPoolExecutor(4)
        self.rows, self.logs = {}, []
        self.sym = cfg["moneda"]
        self.sel, self.sel_manual = None, False
        self.busy_sig = self.busy_pos = False
        self.last_sig = self.last_pos = 0.0
        self.toast_t = 0.0
        self.tabla = None
        self.st = {"users": {"owner": {}}, "trades": {}, "aim": {}}
        self.sync_st()
        self.build()
        self.root.after(200, self.tick)

    # --- estado que entiende bot.py ---
    def sync_st(self):
        c, st = self.cfg, self.st
        st.update(capital=c["capital"], risk=c["riesgo"], auto_capital=c["capital"], auto_risk=c["riesgo"], auto_max=c["max"],
                  auto_lev=int(c["lev"]), auto_loss=c["perdida"], auto_mg=c["gestion"], auto_berr=c["berr"],
                  auto_parcial=c["parcial"], auto_comp=c["comp"], seguro=c["seguro"], seguro_lim=c["seguro_lim"],
                  aim_mode=c["modo"])
        st["users"]["owner"].update(capital=c["capital"], risk=c["riesgo"])

    def save(self):
        self.cfg["moneda"] = self.sym
        self.cfg["pos"] = f"+{self.root.winfo_x()}+{self.root.winfo_y()}"
        guardar_json(PANEL_FILE, self.cfg)

    def log(self, text):
        self.q.put(("log", text))

    # --- interfaz ---
    def build(self):
        r, f = self.root, (lambda size=9, w="normal": ("Segoe UI", size, w))
        self.f = f
        r.title(NOMBRE)
        r.overrideredirect(True)                       # sin barra de Windows: se mueve arrastrando el encabezado
        r.attributes("-topmost", bool(self.cfg["encima"]))
        r.configure(bg=LINE)                           # borde de 1 px
        r.geometry(self.cfg.get("pos") or f"+{r.winfo_screenwidth() - W_PX - 40}+120")
        self.box = tk.Frame(r, bg=BG, width=W_PX)
        self.box.pack(padx=1, pady=1)

        # encabezado: ● BRIDGE [PRUEBA]  ⚙ ▾ ×
        h = tk.Frame(self.box, bg=BG)
        h.pack(fill="x", padx=8, pady=(7, 5))
        self.dot = tk.Label(h, text="●", bg=BG, fg=DIM, font=f(9))
        self.dot.pack(side="left")
        title = tk.Label(h, text=NOMBRE, bg=BG, fg=TXT, font=f(10, "bold"))
        title.pack(side="left", padx=(4, 6))
        self.mode_btn = tk.Label(h, text="", font=f(7, "bold"), padx=6, cursor="hand2")
        self.mode_btn.pack(side="left")
        self.mode_btn.bind("<Button-1>", lambda e: self.toggle_mode())
        for txt, cmd in (("×", self.close), ("▾", self.toggle_fold), ("⚙", self.settings)):
            b = tk.Label(h, text=txt, bg=BG, fg=DIM, font=f(11 if txt != "⚙" else 10), cursor="hand2", padx=3)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, c=cmd: c())
            if txt == "▾":
                self.fold_btn = b
        for w in (h, title, self.dot):
            w.bind("<ButtonPress-1>", self._grab)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", lambda e: self.save())
            w.bind("<Button-3>", self.menu)

        self.body = tk.Frame(self.box, bg=BG)
        self.body.pack(fill="x")
        b = self.body

        # moneda · precio · 📺
        c = tk.Frame(b, bg=BG)
        c.pack(fill="x", padx=8)
        self.sym_var = tk.StringVar(value=self.sym)
        e = tk.Entry(c, textvariable=self.sym_var, width=12, bg=BG, fg=TXT, insertbackground=TXT, relief="flat",
                     font=f(9, "bold"), highlightthickness=0)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.set_symbol(self.sym_var.get(), manual=True))
        self.tv_btn = tk.Label(c, text="📺", bg=BG, font=f(8), cursor="hand2")
        self.tv_btn.pack(side="right")
        self.tv_btn.bind("<Button-1>", lambda e: self.toggle_tv())
        self.px_lbl = tk.Label(c, text="", bg=BG, fg=DIM, font=f(8))
        self.px_lbl.pack(side="right", padx=4)

        # etiquetas de temporalidad (verde = LONG, rojo = SHORT; marco blanco = la elegida)
        chips = tk.Frame(b, bg=BG)
        chips.pack(fill="x", padx=6, pady=(4, 2))
        self.chips = {}
        for i, tf in enumerate(self.cfg["tfs"]):
            ch = tk.Label(chips, text=tf, bg=CARD, fg=DIM, font=f(7, "bold"), width=4, pady=1, cursor="hand2",
                          highlightthickness=1, highlightbackground=BG)
            ch.grid(row=i // 5, column=i % 5, padx=1, pady=1)
            ch.bind("<Button-1>", lambda e, t=tf: self.pick(t))
            self.chips[tf] = ch

        # botón grande
        self.aim_btn = tk.Button(b, text="Apuntar", font=f(12, "bold"), bg=CARD, fg=DIM, relief="flat", bd=0, pady=6,
                                 activebackground=GREEN_D, activeforeground="white", cursor="hand2", disabledforeground=DIM,
                                 state="disabled", command=lambda: self.sel and self.apuntar(self.sel))
        self.aim_btn.pack(fill="x", padx=8, pady=(4, 2))
        self.sig_lbl = tk.Label(b, text="Cargando…", bg=BG, fg=DIM, font=f(7), anchor="w", justify="left", wraplength=W_PX - 16)
        self.sig_lbl.pack(fill="x", padx=8)

        # posiciones
        tk.Frame(b, bg=LINE, height=1).pack(fill="x", padx=8, pady=(6, 4))
        self.pos_head = tk.Label(b, text="Posiciones", bg=BG, fg=DIM, font=f(7), anchor="center")
        self.pos_head.pack(fill="x")
        tot = tk.Frame(b, bg=BG)
        tot.pack(fill="x", padx=8, pady=(2, 0))
        tk.Label(tot, text="Total no realizado", bg=BG, fg=DIM, font=f(8)).grid(row=0, column=0, sticky="w")
        tk.Label(tot, text="Riesgo abierto", bg=BG, fg=DIM, font=f(8)).grid(row=1, column=0, sticky="w")
        tot.columnconfigure(1, weight=1)
        self.tot_lbl = tk.Label(tot, text="—", bg=BG, fg=TXT, font=f(9, "bold"))
        self.tot_lbl.grid(row=0, column=1, sticky="e")
        self.risk_lbl = tk.Label(tot, text="—", bg=BG, fg=TXT, font=f(9, "bold"))
        self.risk_lbl.grid(row=1, column=1, sticky="e")
        self.pos_box = tk.Frame(b, bg=BG)
        self.pos_box.pack(fill="x", padx=8, pady=(4, 2))

        # último aviso
        self.toast = tk.Label(b, text="", bg=BG, fg=DIM, font=f(7), anchor="w", justify="left", wraplength=W_PX - 16)
        self.toast.pack(fill="x", padx=8, pady=(0, 6))
        self.paint_mode()
        self.paint_tv()
        if self.cfg.get("plegado"):
            self.toggle_fold(save=False)
        self.log(f"{NOMBRE} v{PANEL_VERSION} · motor bot.py v{self.bot.VERSION} · " +
                 ("llaves de Bitunix OK" if self.bot.bx_keys() else "sin llaves de Bitunix: ⚙ para ponerlas"))

    def _grab(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def menu(self, e):
        m = tk.Menu(self.root, tearoff=0, bg=CARD, fg=TXT, activebackground=BLUE)
        m.add_command(label="Tabla completa y registro", command=self.open_table)
        m.add_command(label="Actualizar ahora", command=lambda: self.refresh(force=True))
        m.add_command(label=("✓ " if self.cfg["encima"] else "") + "Siempre encima", command=self.toggle_top)
        m.add_command(label="Ajustes", command=self.settings)
        m.add_separator()
        m.add_command(label="Cerrar", command=self.close)
        m.tk_popup(e.x_root, e.y_root)

    def paint_mode(self):
        real = self.cfg["modo"] == "si"
        self.mode_btn.configure(text="REAL" if real else "PRUEBA", bg=RED_D if real else AMBER_D, fg=RED if real else AMBER)

    def paint_tv(self):
        self.tv_btn.configure(fg=TXT if self.cfg["seguir_tv"] else LINE)

    def toggle_fold(self, save=True):
        """▾ pliega el widget a solo el encabezado (▸ lo abre)."""
        self.folded = not getattr(self, "folded", False)
        if self.folded:
            self.body.pack_forget()
        else:
            self.body.pack(fill="x")
        self.fold_btn.configure(text="▸" if self.folded else "▾")
        self.cfg["plegado"] = self.folded
        if save:
            self.save()

    # --- ciclo ---
    def tick(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.add_log(data)
                elif kind == "rows":
                    self.paint_rows(*data)
                elif kind == "pos":
                    self.paint_positions(*data)
                elif kind == "ui":
                    data()
        except queue.Empty:
            pass
        now = time.time()
        if self.cfg["seguir_tv"] and now - getattr(self, "_tv_t", 0) > 1.5:
            self._tv_t = now
            s = moneda_tradingview()
            if s and s != self.sym:
                self.set_symbol(s)
        if not self.busy_sig and now - self.last_sig > 30:
            self.refresh()
        keys = bool(self.bot.bx_keys())
        self.dot.configure(fg=GREEN if keys else DIM)
        if not self.busy_pos and now - self.last_pos > 15:
            if keys:
                self.refresh_positions()
            else:
                self.last_pos = now
                self.paint_positions([], 0.0, "sin llaves de Bitunix · ⚙")
        if self.toast_t and now - self.toast_t > TOAST_S:
            self.toast.configure(fg=LINE)
        self.root.after(300, self.tick)

    def add_log(self, text):
        line = time.strftime("%H:%M ") + limpiar(text).replace("\n", " · ")
        self.logs = (self.logs + [line])[-300:]
        self.toast.configure(text=line[:160], fg=DIM)
        self.toast_t = time.time()
        if self.tabla:
            self.tabla.add_log(line)

    def set_symbol(self, s, manual=False):
        s = self.bot.norm_symbol(s.strip().upper()) if s else ""
        if not s:
            return
        if manual and self.cfg["seguir_tv"]:
            self.toggle_tv()  # la escribiste tú: dejar de seguir TradingView
        self.sym, self.rows, self.sel, self.sel_manual = s, {}, None, False
        self.sym_var.set(s)
        self.px_lbl.configure(text="")
        for ch in self.chips.values():
            ch.configure(bg=CARD, fg=DIM, highlightbackground=BG)
        self.paint_button()
        self.sig_lbl.configure(text="Cargando…")
        self.save()
        self.refresh(force=True)

    def refresh(self, force=False):
        if self.busy_sig and not force:
            return
        self.busy_sig, self.last_sig = True, time.time()
        sym = self.sym

        def work():
            rows, errs = {}, []
            futs = {tf: self.pool.submit(fila_tf, self.bot, self.st, sym, tf) for tf in self.cfg["tfs"]}
            for tf, fu in futs.items():
                try:
                    rows[tf] = fu.result(timeout=60)
                except Exception as e:
                    errs.append(f"{tf}: {e}")
            self.q.put(("rows", (sym, rows, errs)))
        threading.Thread(target=work, daemon=True).start()

    def paint_rows(self, sym, rows, errs):
        self.busy_sig = False
        if sym != self.sym:
            return  # llegó tarde: ya cambiaste de moneda
        self.rows = rows
        px = next((r["px"] for r in rows.values()), None)
        self.px_lbl.configure(text=self.bot.fmt(px) if px else "")
        if not self.sel_manual or self.sel not in rows:
            self.sel = elegir_tf(rows, self.cfg["tfs"], self.sel)
        self.paint_chips()
        self.paint_button()
        if self.tabla:
            self.tabla.paint(rows)
        for e in errs[:2]:
            self.log(f"⚠️ {sym} {e}"[:200])

    def paint_chips(self):
        for tf, ch in self.chips.items():
            s = (self.rows.get(tf) or {}).get("sig")
            if s and s["ok"]:
                bg, fg = (GREEN_D, GREEN) if s["side"] == 1 else (RED_D, RED)
            elif s:
                bg, fg = CARD, GREEN if s["side"] == 1 else RED   # activa pero ya no se puede apuntar
            else:
                bg, fg = CARD, DIM
            ch.configure(bg=bg, fg=fg, highlightbackground=TXT if tf == self.sel else BG)

    def paint_button(self):
        s = self.sel and (self.rows.get(self.sel) or {}).get("sig")
        fmt = self.bot.fmt
        if s and s["ok"]:
            long_ = s["side"] == 1
            self.aim_btn.configure(state="normal", text=f"Apuntar {'LONG' if long_ else 'SHORT'} {self.sel}",
                                   bg=GREEN_B if long_ else RED_B, fg="white", activebackground=GREEN_D if long_ else RED_D)
            entry = f"límite {fmt(s['e2'])}" if s["limit"] else fmt(s["e1"])
            self.sig_lbl.configure(text=f"{s['estado']} · entrada {entry} · SL {fmt(s['sl'])} · TP1 {fmt(s['tp1'])}", fg=DIM)
        else:
            self.aim_btn.configure(state="disabled", text="Apuntar", bg=CARD)
            if not self.rows:
                return
            ups = sum(1 for r in self.rows.values() if r["up"])
            why = f"{self.sel}: {s['estado']}" if s else (f"{self.sel}: sin señal" if self.sel else "Sin señales para apuntar")
            self.sig_lbl.configure(text=f"{why} · Tendencial ▲ en {ups}/{len(self.rows)}", fg=DIM)

    def pick(self, tf):
        self.sel, self.sel_manual = tf, True
        self.paint_chips()
        self.paint_button()

    # --- posiciones ---
    def refresh_positions(self):
        self.busy_pos, self.last_pos = True, time.time()

        def work():
            try:
                rows, eq = self.bot.bx_portfolio()
                self.q.put(("pos", (rows, eq, "")))
            except Exception as e:
                self.q.put(("pos", ([], 0.0, str(e))))
        threading.Thread(target=work, daemon=True).start()

    def paint_positions(self, rows, eq, err):
        self.busy_pos = False
        for w in self.pos_box.winfo_children():
            w.destroy()
        f = self.f
        mine = sum(1 for x in rows if x["r"])
        self.pos_head.configure(text=f"Posiciones ({len(rows)})" + (f" · {mine} en el panel" if mine else ""))
        if err:
            self.tot_lbl.configure(text="—", fg=DIM)
            self.risk_lbl.configure(text="—", fg=DIM)
            tk.Label(self.pos_box, text=err[:80], bg=BG, fg=AMBER, font=f(7), anchor="w").pack(fill="x")
            return
        tot, rk = sum(x["pnl"] for x in rows), sum(x["risk"] or 0 for x in rows)
        pr, lim = (rk / eq * 100 if eq else 0.0), self.cfg["seguro_lim"]
        self.tot_lbl.configure(text=f"{'+' if tot >= 0 else '−'}${abs(tot):,.2f}", fg=GREEN if tot >= 0 else RED)
        self.risk_lbl.configure(text=f"{pr:.1f} %", fg=RED if self.cfg["seguro"] and pr > lim else (AMBER if pr > lim * 0.7 else TXT))
        rows = sorted(rows, key=lambda x: -abs(x["pnl"]))
        for x in rows[:MAX_POS]:
            r, long_ = x["r"], x["side"] == "LONG"
            card = tk.Frame(self.pos_box, bg=BG)
            card.pack(fill="x", pady=2)
            top = tk.Frame(card, bg=BG)
            top.pack(fill="x")
            tk.Label(top, text=self.bot.coin(x["sym"]), bg=BG, fg=TXT, font=f(9, "bold")).pack(side="left")
            tk.Label(top, text=f"{'L' if long_ else 'S'} {x['lev']}x", bg=GREEN_D if long_ else RED_D, fg=GREEN if long_ else RED,
                     font=f(6, "bold"), padx=3).pack(side="left", padx=4)
            tk.Label(top, text=f"{'+' if x['pnl'] >= 0 else '−'}${abs(x['pnl']):,.2f}", bg=BG, fg=GREEN if x["pnl"] >= 0 else RED,
                     font=f(9, "bold")).pack(side="right")
            sub = tk.Frame(card, bg=BG)
            sub.pack(fill="x")
            if r:
                who = "✋ soltada" if r.get("soltada") else f"{r['tf']} · panel · TP{r['stage']}" + (" · BE" if r.get("be_px") else "")
            else:
                who = "servidor o tuya"
            risk = "sin SL ⚠️" if x["risk"] is None else f"riesgo ${x['risk']:,.2f}"
            tk.Label(sub, text=f"{who} · {risk}", bg=BG, fg=AMBER if x["risk"] is None else DIM, font=f(7)).pack(side="left")
            pct = x["pnl"] / x["margin"] * 100 if x["margin"] else 0.0
            tk.Label(sub, text=f"{pct:+.1f} %", bg=BG, fg=GREEN if pct >= 0 else RED, font=f(7)).pack(side="right")
            if r and r.get("soltada"):
                d = tk.Label(sub, text="↩️", bg=BG, fg=TXT, font=f(7), cursor="hand2")
                d.pack(side="right", padx=4)
                d.bind("<Button-1>", lambda e, t=x["tid"]: self.bg(lambda: self.bot.bx_devolver(t), self.after_devolver))
        if len(rows) > MAX_POS:
            tk.Label(self.pos_box, text=f"… y {len(rows) - MAX_POS} más (clic derecho → tabla)", bg=BG, fg=DIM, font=f(7)).pack(fill="x")
        if not rows:
            tk.Label(self.pos_box, text="Sin posiciones abiertas", bg=BG, fg=DIM, font=f(7)).pack(fill="x")

    def after_devolver(self, msg):
        self.log(str(msg))
        self.last_pos = 0

    # --- 🎯 APUNTAR ---
    def bg(self, fn, done=None):
        """Trabajo con red fuera de la interfaz; 'done' se ejecuta en la interfaz con el resultado."""
        def work():
            try:
                out = fn()
            except Exception as e:
                out = e
            if done:
                self.q.put(("ui", lambda: done(out)))
        threading.Thread(target=work, daemon=True).start()

    def apuntar(self, tf):
        r = self.rows.get(tf)
        s = r and r["sig"]
        if not s or not s["ok"]:
            return
        if not self.bot.bx_keys():
            messagebox.showinfo(NOMBRE, "Primero pon tus llaves de Bitunix en ⚙ Ajustes.", parent=self.root)
            return
        sym, bot, st = self.sym, self.bot, self.st
        a = dict(sym=sym, tf=tf, ev=s["ev"], ct=s["ct"])
        self.aim_btn.configure(state="disabled", text="Revisando…")

        def check():
            plan, why = bot.aim_plan(st, sym, a["ev"])
            why = why or bot.aim_check(st, s["tid"], a)
            return plan, why, (bot.aim_risk_line(st, plan) if plan and not why else "")

        def ask(out):
            self.paint_button()
            if isinstance(out, Exception):
                messagebox.showerror(NOMBRE, str(out)[:300], parent=self.root)
                return
            plan, why, risk = out
            if why:
                messagebox.showwarning(NOMBRE, f"{sym} {tf}: no se puede apuntar\n\n{why}", parent=self.root)
                return
            real = self.cfg["modo"] == "si"
            text = limpiar(bot.bx_text(sym, tf, plan, "REAL" if real else "PRUEBA (no se enviará nada)", "🎯 APUNTAR") + risk)
            if not messagebox.askyesno(NOMBRE, text + "\n\n¿Confirmas?", parent=self.root,
                                       icon="warning" if real else "question"):
                self.log(f"🎯 {sym} {tf}: cancelado")
                return
            self.bg(send, lambda o: self.log(f"⚠️ 🎯 {o}") if isinstance(o, Exception) else None)

        def send():
            plan, why, _ = check()  # el precio pudo moverse mientras pensabas
            if not why and self.cfg["modo"] == "si":
                why = bot.auto_arm_error(st)
            if why:
                self.log(f"🎯 {sym} {tf}: no se envió ({why})")
            elif self.cfg["modo"] != "si":
                self.log(limpiar(bot.bx_text(sym, tf, plan, "PRUEBA · no se envió nada (toca PRUEBA para pasar a REAL)",
                                             "🎯 APUNTAR")))
            else:
                bot.bx_open(self.token, self.chat, st, sym, tf, a["ev"], s["tid"], plan, manual=True)
                self.last_pos = 0
        self.bg(check, ask)

    # --- modo, ajustes, tabla ---
    def toggle_mode(self):
        if self.cfg["modo"] == "si":
            self.cfg["modo"] = "prueba"
            self.sync_st(); self.save(); self.paint_mode()
            self.log("Modo PRUEBA: Apuntar solo muestra las órdenes")
            return

        def done(err):
            if isinstance(err, Exception) or err:
                messagebox.showwarning(NOMBRE, f"No se activó el modo REAL:\n{err}", parent=self.root)
                return
            if messagebox.askyesno(NOMBRE, "Modo REAL: al confirmar Apuntar se envían órdenes de verdad a Bitunix con tu capital "
                                   f"(${self.cfg['capital']:g}, riesgo {self.cfg['riesgo']:g} %, margen máx. ${self.cfg['max']:g}).\n\n"
                                   "¿Activarlo?", parent=self.root, icon="warning"):
                self.cfg["modo"] = "si"
                self.sync_st(); self.save(); self.paint_mode()
                self.log("💸 Modo REAL activado")
        self.bg(lambda: self.bot.auto_arm_error(self.st), done)

    def toggle_top(self):
        self.cfg["encima"] = not self.cfg["encima"]
        self.root.attributes("-topmost", self.cfg["encima"])
        self.save()

    def toggle_tv(self):
        self.cfg["seguir_tv"] = not self.cfg["seguir_tv"]
        self.paint_tv()
        self.save()
        self.log("📺 sigue la moneda de TradingView" if self.cfg["seguir_tv"] else "📺 moneda fija (no sigue TradingView)")

    def open_table(self):
        if self.tabla and self.tabla.win.winfo_exists():
            self.tabla.win.lift()
            return
        self.tabla = Tabla(self)
        self.tabla.paint(self.rows)

    def settings(self):
        w = tk.Toplevel(self.root)
        w.title(f"{NOMBRE} · Ajustes")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        f, c = self.f, self.cfg
        keys = cargar_json(KEYS_FILE)
        fields = [("capital", "Capital que administra (USDT)", c["capital"]), ("riesgo", "Riesgo por operación (%)", c["riesgo"]),
                  ("max", "Margen máximo por operación (USDT)", c["max"]), ("lev", "Apalancamiento máximo (0 = el de la señal)", c["lev"]),
                  ("perdida", "Pérdida máxima del día (%)", c["perdida"]), ("seguro_lim", "Seguro de riesgo: avisar sobre (% cuenta)", c["seguro_lim"]),
                  ("berr", "STRIKE · break even a (R)", c["berr"]), ("parcial", "STRIKE · parcial al llegar al BE (%)", c["parcial"])]
        vars_ = {}
        for i, (k, label, v) in enumerate(fields):
            tk.Label(w, text=label, bg=BG, fg=TXT, font=f(9), anchor="w").grid(row=i, column=0, sticky="w", padx=10, pady=3)
            vars_[k] = tk.StringVar(value=f"{v:g}" if isinstance(v, float) else str(v))
            tk.Entry(w, textvariable=vars_[k], width=10, bg=CARD, fg=TXT, insertbackground=TXT, relief="flat").grid(row=i, column=1, padx=10)
        i = len(fields)
        gest = tk.StringVar(value=c["gestion"])
        tk.Label(w, text="Gestión de la posición", bg=BG, fg=TXT, font=f(9)).grid(row=i, column=0, sticky="w", padx=10, pady=3)
        tk.OptionMenu(w, gest, "bot", "strike").grid(row=i, column=1, padx=10, sticky="w")
        bools = {}
        for j, (k, label) in enumerate((("comp", "STRIKE · BE compensado por el parcial (resultado ≈ 0)"),
                                        ("seguro", "Seguro de riesgo activado"), ("telegram", "Avisos también por Telegram")), i + 1):
            bools[k] = tk.BooleanVar(value=c[k])
            tk.Checkbutton(w, text=label, variable=bools[k], bg=BG, fg=TXT, selectcolor=CARD, activebackground=BG,
                           font=f(9)).grid(row=j, column=0, columnspan=2, sticky="w", padx=6)
        j += 1
        tk.Label(w, text="Llaves de Bitunix (solo trading, sin retiros) · se guardan solo en esta PC",
                 bg=BG, fg=AMBER, font=f(8, "bold")).grid(row=j, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 2))
        kv, sv = tk.StringVar(value=keys.get("key", "")), tk.StringVar(value=keys.get("secret", ""))
        for jj, (label, var) in enumerate((("API key", kv), ("Secret key", sv)), j + 1):
            tk.Label(w, text=label, bg=BG, fg=TXT, font=f(9)).grid(row=jj, column=0, sticky="w", padx=10)
            tk.Entry(w, textvariable=var, show="•", width=34, bg=CARD, fg=TXT, insertbackground=TXT, relief="flat").grid(
                row=jj, column=1, padx=10, pady=2)

        def ok():
            try:
                new = {k: float(v.get().replace(",", ".")) for k, v in vars_.items()}
            except ValueError:
                messagebox.showerror(NOMBRE, "Revisa los números.", parent=w)
                return
            if new["capital"] <= 0 or not 0 < new["riesgo"] <= 10 or new["max"] <= 0:
                messagebox.showerror(NOMBRE, "Capital y margen > 0 · riesgo entre 0 y 10 %.", parent=w)
                return
            new["lev"] = int(new["lev"])
            c.update(new, gestion=gest.get(), **{k: v.get() for k, v in bools.items()})
            if kv.get().strip() and sv.get().strip():
                guardar_json(KEYS_FILE, {"key": kv.get().strip(), "secret": sv.get().strip()})
                poner_llaves(kv.get(), sv.get())
            self.sync_st(); self.save()
            self.log("⚙ Ajustes guardados" + (" · gestión STRIKE (no está en el backtest)" if c["gestion"] == "strike" else ""))
            self.last_pos = 0
            w.destroy()
        tk.Button(w, text="Guardar", command=ok, bg=BLUE, fg="white", relief="flat", font=f(10, "bold"), padx=14).grid(
            row=jj + 1, column=0, columnspan=2, pady=12)

    def close(self):
        try:
            self.save()
            self.bot.bx_save()
        finally:
            self.root.destroy()


class Tabla:
    """Ventana aparte (clic derecho → Tabla completa): todas las temporalidades con entrada/SL/TP1 y el registro."""
    def __init__(self, panel):
        self.p, f = panel, panel.f
        self.win = w = tk.Toplevel(panel.root)
        w.title(f"{NOMBRE} · {panel.sym}")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        t = tk.Frame(w, bg=CARD)
        t.pack(fill="x", padx=8, pady=8)
        for j, h in enumerate(("TF", "DIR", "PTS", "SEÑAL", "ENTRADA", "SL", "TP1", "ESTADO", "")):
            tk.Label(t, text=h, bg=CARD, fg=DIM, font=f(8, "bold")).grid(row=0, column=j, padx=3, pady=(6, 2), sticky="w")
        self.cells = {}
        for i, tf in enumerate(panel.cfg["tfs"], 1):
            cs = [tk.Label(t, text="", bg=CARD, fg=TXT, font=f(9)) for _ in range(8)]
            for j, c in enumerate(cs):
                c.grid(row=i, column=j, padx=3, pady=1, sticky="w")
            cs[0].configure(text=tf.upper(), font=f(9, "bold"))
            btn = tk.Button(t, text="Apuntar", font=f(8, "bold"), bg=LINE, fg=DIM, relief="flat", state="disabled",
                            command=lambda x=tf: panel.apuntar(x))
            btn.grid(row=i, column=8, padx=(3, 6), pady=1)
            self.cells[tf] = (cs, btn)
        self.logbox = tk.Text(w, height=10, width=70, bg=CARD, fg=TXT, relief="flat", font=("Consolas", 9), wrap="word")
        self.logbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.logbox.insert("end", "\n".join(panel.logs) + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def add_log(self, line):
        if not self.win.winfo_exists():
            return
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def paint(self, rows):
        if not self.win.winfo_exists():
            return
        fmt, f = self.p.bot.fmt, self.p.f
        self.win.title(f"{NOMBRE} · {self.p.sym}")
        for tf, (cs, btn) in self.cells.items():
            r = rows.get(tf)
            s = r and r["sig"]
            cs[1].configure(text=("▲" if r["up"] else "▼") if r else "…", fg=(GREEN if r["up"] else RED) if r else DIM)
            cs[2].configure(text=str(r["pts"]) if r else "", fg=TXT if r and r["pts"] else DIM)
            if not s:
                cs[3].configure(text="ESPERANDO" if r else "", fg=DIM, font=f(9))
                for c in cs[4:]:
                    c.configure(text="")
                btn.configure(state="disabled", bg=LINE, fg=DIM)
                continue
            cs[3].configure(text="LONG" if s["side"] == 1 else "SHORT", fg=GREEN if s["side"] == 1 else RED, font=f(9, "bold"))
            cs[4].configure(text=fmt(s["e2"] if s["limit"] else s["e1"]), fg=TXT)
            cs[5].configure(text=fmt(s["sl"]), fg=RED)
            cs[6].configure(text=fmt(s["tp1"]), fg=GREEN)
            cs[7].configure(text=s["estado"], fg=(AMBER if s["estado"] == "AHORA" else TXT) if s["ok"] else DIM)
            btn.configure(state="normal" if s["ok"] else "disabled", bg=BLUE if s["ok"] else LINE, fg="white" if s["ok"] else DIM)


# ----------------------------- VIGILANTE (gestión de lo abierto desde el panel) -----------------------------
def vigilante(panel):
    """Cada 10 s: TP, break even, STRIKE y "soltar" de las operaciones abiertas DESDE EL PANEL (las del servidor las vigila
    el servidor). El SL siempre queda puesto en Bitunix aunque cierres el panel; al volver a abrirlo sigue donde quedó."""
    bot, st = panel.bot, panel.st
    t_guard = t_seg = 0.0
    while True:
        time.sleep(10)
        if not bot.bx_keys():
            continue
        try:
            if bot._bx["trades"]:
                bot.bx_manage(panel.token, panel.chat, st)
            if st.get("aim_mode") == "si" and time.time() - t_guard > 300:
                t_guard = time.time()
                bot.bx_guard(panel.token, panel.chat, st)
            if st.get("seguro", True) and time.time() - t_seg > 60:
                t_seg = time.time()
                bot.bx_seguro(panel.token, panel.chat, st)
        except Exception as e:
            panel.log(f"⚠️ vigilante: {e}"[:200])


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


CFG = {}
_PANEL = []      # el panel, cuando ya existe
_ANTES = []      # avisos que llegaron antes de abrir la ventana


def LOG(text):
    (_PANEL[0].log if _PANEL else _ANTES.append)(text)


def main():
    global CFG
    CFG.update(cargar_json(PANEL_FILE, DEFAULTS))
    keys = cargar_json(KEYS_FILE)
    poner_llaves(keys.get("key", ""), keys.get("secret", ""))
    root = tk.Tk()
    try:
        bot = preparar_bot(LOG)
    except Exception as e:
        root.withdraw()
        messagebox.showerror(NOMBRE, str(e))
        return
    token = chat = ""
    try:
        token, chat, _ = bot.load_credentials(False)
    except SystemExit:
        pass  # sin Telegram: los avisos quedan solo en el registro
    panel = Panel(root, bot, CFG, token, chat)
    _PANEL.append(panel)
    for t in _ANTES:
        panel.log(t)
    threading.Thread(target=vigilante, args=(panel,), daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
