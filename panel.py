#!/usr/bin/env python3
"""
BRIDGE · panel flotante para TradingView (Windows) · v1.0

Una ventana pequeña que queda ENCIMA de TradingView (como STRIKE):
  - detecta la moneda que tienes abierta en TradingView (por el título de la ventana)
  - muestra la tabla del Bridge Táctico en todas las temporalidades (dirección, puntos, señal activa)
  - 🎯 APUNTAR: abre la señal en Bitunix (E1/E2/E3, SL y TP) después de confirmar
  - tus posiciones en Bitunix con PnL no realizado, SL y riesgo abierto
  - vigila las operaciones que abriste desde aquí: TP, break even, gestión STRIKE, "soltar" si cambias algo a mano

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

PANEL_VERSION = "1.0"
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
PANEL_FILE = os.path.join(AQUI, "panel.json")          # ajustes (sin llaves)
KEYS_FILE = os.path.join(AQUI, "panel_llaves.json")    # llaves de Bitunix solo para el panel
NOMBRE = "BRIDGE"

# Colores (tema oscuro de TradingView)
BG, CARD, LINE, TXT, DIM = "#131722", "#1e222d", "#2a2e39", "#d1d4dc", "#787b86"
GREEN, RED, AMBER, BLUE = "#26a69a", "#ef5350", "#f5a623", "#2962ff"

DEFAULTS = dict(capital=500.0, riesgo=1.0, max=10.0, lev=0, perdida=5.0, modo="prueba", gestion="bot", berr=1.0, parcial=25.0,
                comp=True, seguro=True, seguro_lim=5.0, seguir_tv=True, moneda="BTCUSDT", telegram=True, encima=True,
                tfs=["1m", "3m", "5m", "15m", "1h", "2h", "4h", "8h", "1d"], geo="")


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


# ----------------------------- VENTANA -----------------------------
class Panel:
    def __init__(self, root, bot, cfg, token="", chat=""):
        self.root, self.bot, self.cfg, self.token, self.chat = root, bot, cfg, token, chat
        self.q = queue.Queue()
        self.pool = ThreadPoolExecutor(4)
        self.rows, self.positions, self.eq = {}, [], 0.0
        self.sym = cfg["moneda"]
        self.busy_sig = self.busy_pos = False
        self.last_sig = self.last_pos = 0.0
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
        self.cfg["moneda"], self.cfg["geo"] = self.sym, self.root.geometry()
        guardar_json(PANEL_FILE, self.cfg)

    def log(self, text):
        self.q.put(("log", text))

    # --- interfaz ---
    def build(self):
        r = self.root
        r.title(f"{NOMBRE} · Bridge Táctico")
        r.configure(bg=BG)
        r.attributes("-topmost", bool(self.cfg["encima"]))
        r.geometry(self.cfg["geo"] or "560x700")
        r.minsize(520, 520)
        r.protocol("WM_DELETE_WINDOW", self.close)
        f = lambda size=10, w="normal": ("Segoe UI", size, w)
        self.f = f

        top = tk.Frame(r, bg=BG)
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text=NOMBRE, bg=BG, fg=TXT, font=f(15, "bold")).pack(side="left")
        self.mode_btn = tk.Label(top, text="", font=f(9, "bold"), padx=8, pady=2, cursor="hand2")
        self.mode_btn.pack(side="left", padx=8)
        self.mode_btn.bind("<Button-1>", lambda e: self.toggle_mode())
        for txt, cmd in (("⚙", self.settings), ("📌", self.toggle_top), ("⟳", lambda: self.refresh(force=True))):
            b = tk.Label(top, text=txt, bg=BG, fg=DIM, font=f(13), cursor="hand2", padx=4)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, c=cmd: c())

        coin = tk.Frame(r, bg=CARD)
        coin.pack(fill="x", padx=10, pady=4)
        self.sym_var = tk.StringVar(value=self.sym)
        e = tk.Entry(coin, textvariable=self.sym_var, width=14, bg=BG, fg=TXT, insertbackground=TXT, relief="flat",
                     font=f(12, "bold"))
        e.pack(side="left", padx=8, pady=8)
        e.bind("<Return>", lambda ev: self.set_symbol(self.sym_var.get(), manual=True))
        self.tv_var = tk.BooleanVar(value=self.cfg["seguir_tv"])
        tk.Checkbutton(coin, text="📺 seguir TradingView", variable=self.tv_var, command=self.toggle_tv, bg=CARD, fg=DIM,
                       selectcolor=BG, activebackground=CARD, activeforeground=TXT, font=f(9)).pack(side="left")
        self.px_lbl = tk.Label(coin, text="", bg=CARD, fg=TXT, font=f(11, "bold"))
        self.px_lbl.pack(side="right", padx=8)

        # tabla de temporalidades
        self.table = tk.Frame(r, bg=CARD)
        self.table.pack(fill="x", padx=10, pady=4)
        heads = ("TF", "DIR", "PTS", "SEÑAL", "ENTRADA", "SL", "TP1", "ESTADO", "")
        for j, h in enumerate(heads):
            tk.Label(self.table, text=h, bg=CARD, fg=DIM, font=f(8, "bold")).grid(row=0, column=j, padx=3, pady=(6, 2), sticky="w")
        self.cells = {}
        for i, tf in enumerate(self.cfg["tfs"], 1):
            cs = [tk.Label(self.table, text="", bg=CARD, fg=TXT, font=f(9)) for _ in range(8)]
            for j, c in enumerate(cs):
                c.grid(row=i, column=j, padx=3, pady=1, sticky="w")
            cs[0].configure(text=tf.upper(), font=f(9, "bold"))
            btn = tk.Button(self.table, text="🎯 Apuntar", font=f(8, "bold"), bg=LINE, fg=DIM, relief="flat", state="disabled",
                            activebackground=BLUE, activeforeground="white", cursor="hand2",
                            command=lambda t=tf: self.apuntar(t))
            btn.grid(row=i, column=8, padx=(3, 6), pady=1)
            self.cells[tf] = (cs, btn)
        self.sig_info = tk.Label(r, text="Cargando…", bg=BG, fg=DIM, font=f(8), anchor="w")
        self.sig_info.pack(fill="x", padx=12)

        # posiciones
        ph = tk.Frame(r, bg=BG)
        ph.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(ph, text="POSICIONES EN BITUNIX", bg=BG, fg=DIM, font=f(8, "bold")).pack(side="left")
        self.pos_sum = tk.Label(ph, text="", bg=BG, fg=TXT, font=f(9, "bold"))
        self.pos_sum.pack(side="right")
        self.pos_box = tk.Frame(r, bg=CARD)
        self.pos_box.pack(fill="x", padx=10, pady=2)

        # registro
        tk.Label(r, text="REGISTRO", bg=BG, fg=DIM, font=f(8, "bold"), anchor="w").pack(fill="x", padx=12, pady=(8, 0))
        self.logbox = tk.Text(r, height=7, bg=CARD, fg=TXT, relief="flat", font=("Consolas", 9), wrap="word", state="disabled")
        self.logbox.pack(fill="both", expand=True, padx=10, pady=(2, 10))
        self.paint_mode()
        self.log(f"{NOMBRE} v{PANEL_VERSION} · motor bot.py v{self.bot.VERSION} · " +
                 ("llaves de Bitunix OK" if self.bot.bx_keys() else "sin llaves de Bitunix: ⚙ para ponerlas"))

    def paint_mode(self):
        real = self.cfg["modo"] == "si"
        self.mode_btn.configure(text="REAL" if real else "PRUEBA", bg=RED if real else AMBER, fg="white" if real else BG)

    # --- ciclo: cola de la interfaz + refrescos ---
    def tick(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.write_log(data)
                elif kind == "rows":
                    self.paint_rows(*data)
                elif kind == "pos":
                    self.paint_positions(*data)
                elif kind == "ui":
                    data()
        except queue.Empty:
            pass
        now = time.time()
        if self.tv_var.get() and now - getattr(self, "_tv_t", 0) > 1.5:
            self._tv_t = now
            s = moneda_tradingview()
            if s and s != self.sym:
                self.set_symbol(s)
        if not self.busy_sig and now - self.last_sig > 30:
            self.refresh()
        if not self.busy_pos and now - self.last_pos > 15 and self.bot.bx_keys():
            self.refresh_positions()
        self.root.after(300, self.tick)

    def write_log(self, text):
        line = time.strftime("%H:%M ") + limpiar(text).replace("\n", " · ")
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n")
        n = int(self.logbox.index("end-1c").split(".")[0])
        if n > 300:
            self.logbox.delete("1.0", f"{n - 300}.0")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def set_symbol(self, s, manual=False):
        s = self.bot.norm_symbol(s.strip().upper()) if s else ""
        if not s:
            return
        if manual and self.tv_var.get():
            self.tv_var.set(False)  # la escribiste tú: dejar de seguir TradingView
            self.toggle_tv()
        self.sym = s
        self.sym_var.set(s)
        self.rows = {}
        for tf, (cs, btn) in self.cells.items():
            cs[1].configure(text="…", fg=DIM)
            for c in cs[2:]:
                c.configure(text="")
            btn.configure(state="disabled", bg=LINE, fg=DIM)
        self.px_lbl.configure(text="")
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
        fmt = self.bot.fmt
        px = next((r["px"] for r in rows.values()), None)
        self.px_lbl.configure(text=fmt(px) if px else "")
        ups = sum(1 for r in rows.values() if r["up"])
        for tf, (cs, btn) in self.cells.items():
            r = rows.get(tf)
            if not r:
                cs[1].configure(text="—", fg=DIM)
                for c in cs[2:]:
                    c.configure(text="")
                btn.configure(state="disabled", bg=LINE, fg=DIM)
                continue
            cs[1].configure(text="▲" if r["up"] else "▼", fg=GREEN if r["up"] else RED)
            cs[2].configure(text=str(r["pts"]), fg=TXT if r["pts"] else DIM)
            s = r["sig"]
            if not s:
                cs[3].configure(text="ESPERANDO", fg=DIM, font=self.f(9))
                for c in cs[4:]:
                    c.configure(text="")
                btn.configure(state="disabled", bg=LINE, fg=DIM)
                continue
            col = GREEN if s["side"] == 1 else RED
            cs[3].configure(text=("LONG" if s["side"] == 1 else "SHORT"), fg=col, font=self.f(9, "bold"))
            cs[4].configure(text=fmt(s["e2"] if s["limit"] else s["e1"]), fg=TXT)
            cs[5].configure(text=fmt(s["sl"]), fg=RED)
            cs[6].configure(text=fmt(s["tp1"]), fg=GREEN)
            cs[7].configure(text=s["estado"], fg=(AMBER if s["estado"] == "AHORA" else TXT) if s["ok"] else DIM)
            btn.configure(state="normal" if s["ok"] else "disabled", bg=BLUE if s["ok"] else LINE, fg="white" if s["ok"] else DIM)
        self.sig_info.configure(text=f"Tendencial: {ups}/{len(rows)} temporalidades ▲ · actualizado {time.strftime('%H:%M:%S')}"
                                     + (f" · ⚠️ {len(errs)} con error" if errs else ""))
        for e in errs[:2]:
            self.log(f"⚠️ {sym} {e}"[:200])

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
        if err:
            tk.Label(self.pos_box, text=f"⚠️ {err[:90]}", bg=CARD, fg=AMBER, font=f(9), anchor="w").pack(fill="x", padx=8, pady=6)
            return
        if not rows:
            tk.Label(self.pos_box, text="Sin posiciones abiertas", bg=CARD, fg=DIM, font=f(9), anchor="w").pack(fill="x", padx=8, pady=6)
        tot, rk = sum(x["pnl"] for x in rows), sum(x["risk"] or 0 for x in rows)
        pr, lim = (rk / eq * 100 if eq else 0.0), self.cfg["seguro_lim"]
        self.pos_sum.configure(text=f"{tot:+.2f} USDT · riesgo {pr:.1f} % / {lim:g} %",
                               fg=RED if self.cfg["seguro"] and pr > lim else (GREEN if tot >= 0 else RED))
        for x in rows:
            line = tk.Frame(self.pos_box, bg=CARD)
            line.pack(fill="x", padx=8, pady=2)
            r = x["r"]
            who = ("✋ soltada" if r.get("soltada") else f"🖥 panel {r['tf']} · TP {r['stage']}/3" + (" · 🛟" if r.get("be_px") else "")) \
                if r else "📡 servidor o tuya"
            side = "L" if x["side"] == "LONG" else "S"
            tk.Label(line, text=f"{self.bot.coin(x['sym'])} {side} {x['lev']}x", bg=CARD, fg=GREEN if side == "L" else RED,
                     font=f(9, "bold"), width=12, anchor="w").pack(side="left")
            pct = x["pnl"] / x["margin"] * 100 if x["margin"] else 0.0
            tk.Label(line, text=f"{x['pnl']:+.2f} ({pct:+.1f} %)", bg=CARD, fg=GREEN if x["pnl"] >= 0 else RED, font=f(9),
                     width=15, anchor="w").pack(side="left")
            risk = "⚠️ sin SL" if x["risk"] is None else f"riesgo ${x['risk']:.2f}"
            tk.Label(line, text=f"{risk} · {who}", bg=CARD, fg=DIM, font=f(8), anchor="w").pack(side="left")
            if r and r.get("soltada"):
                tk.Button(line, text="↩️ Devolver", font=f(8), bg=LINE, fg=TXT, relief="flat",
                          command=lambda t=x["tid"]: self.bg(lambda: self.bot.bx_devolver(t), self.after_devolver)).pack(side="right")

    def after_devolver(self, msg):
        self.log(msg)
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

        def check():
            plan, why = bot.aim_plan(st, sym, a["ev"])
            why = why or bot.aim_check(st, s["tid"], a)
            return plan, why, (bot.aim_risk_line(st, plan) if plan and not why else "")

        def ask(out):
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
            self.bg(lambda: send(), lambda o: self.log(f"⚠️ 🎯 {o}") if isinstance(o, Exception) else None)

        def send():
            plan, why, _ = check()  # el precio pudo moverse mientras pensabas
            if not why and self.cfg["modo"] == "si":
                why = bot.auto_arm_error(st)
            if why:
                self.log(f"🎯 {sym} {tf}: no se envió ({why})")
            elif self.cfg["modo"] != "si":
                self.log(limpiar(bot.bx_text(sym, tf, plan, "PRUEBA · no se envió nada (modo REAL arriba)", "🎯 APUNTAR")))
            else:
                bot.bx_open(self.token, self.chat, st, sym, tf, a["ev"], s["tid"], plan, manual=True)
                self.last_pos = 0
        self.bg(check, ask)

    # --- modo, ajustes ---
    def toggle_mode(self):
        if self.cfg["modo"] == "si":
            self.cfg["modo"] = "prueba"
            self.sync_st(); self.save(); self.paint_mode()
            self.log("Modo PRUEBA: 🎯 solo muestra las órdenes")
            return

        def done(err):
            if isinstance(err, Exception) or err:
                messagebox.showwarning(NOMBRE, f"No se activó el modo REAL:\n{err}", parent=self.root)
                return
            if messagebox.askyesno(NOMBRE, "Modo REAL: al confirmar 🎯 se envían órdenes de verdad a Bitunix con tu capital "
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
        self.log("📌 siempre encima" if self.cfg["encima"] else "📌 ventana normal")

    def toggle_tv(self):
        self.cfg["seguir_tv"] = self.tv_var.get()
        self.save()

    def settings(self):
        w = tk.Toplevel(self.root)
        w.title(f"{NOMBRE} · Ajustes")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.transient(self.root)
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
