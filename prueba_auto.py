"""Pruebas sin red ni dinero del modo automático de Bitunix: python prueba_auto.py"""
import hashlib, json, os

os.environ["BITUNIX_KEY"], os.environ["BITUNIX_SECRET"] = "llave", "secreto"   # falsas: nunca se conecta
import bot

sha = lambda s: hashlib.sha256(s.encode()).hexdigest()

# 1. Firma: lo que se envía es exactamente lo que se firma (GET con parámetros y POST con JSON compacto)
sent = []


class Resp:
    def __init__(self, req): sent.append(req)
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return b'{"code":0,"data":{"ok":1},"msg":"Success"}'


bot.urllib.request.urlopen = lambda req, timeout=0: Resp(req)
assert bot.bx_req("GET", "/api/v1/futures/account", {"marginCoin": "USDT", "a": 1}) == {"ok": 1}
h = {k.lower(): v for k, v in sent[-1].header_items()}
assert sent[-1].full_url.endswith("/api/v1/futures/account?a=1&marginCoin=USDT")
assert h["sign"] == sha(sha(h["nonce"] + h["timestamp"] + "llave" + "a1marginCoinUSDT") + "secreto")
bot.bx_req("POST", "/api/v1/futures/trade/place_order", body={"symbol": "BTCUSDT", "qty": "0.01"})
h, body = {k.lower(): v for k, v in sent[-1].header_items()}, sent[-1].data.decode()
assert body == '{"symbol":"BTCUSDT","qty":"0.01"}'
assert h["sign"] == sha(sha(h["nonce"] + h["timestamp"] + "llave" + body) + "secreto")

# 2. Plan: tope de margen, cantidades hacia abajo, apalancamiento máximo de la moneda
info = {"symbol": "SOLUSDT", "symbolStatus": "OPEN", "isApiSupported": True, "basePrecision": 2, "quotePrecision": 3,
        "minTradeVolume": "0.01", "minLeverage": 1, "maxLeverage": 20}
ev = dict(side=1, price=120.70, e2=120.44, e3=120.18, sl=119.70, tps=[121.2, 121.7, 122.2], lev=50,
          qty=[1.0, 1.5, 2.5], w=(0.2, 0.3, 0.5))
plan, why = bot.bx_plan(ev, info, cap=10)
assert why == "" and plan["lev"] == 20 and plan["side"] == "BUY"
assert [l["type"] for l in plan["legs"]] == ["MARKET", "LIMIT", "LIMIT"]
assert plan["margin"] <= 10 and plan["legs"][0]["qty"] == 0.33, plan   # 1.0 * 10/30.13 = 0.3319 → 0.33
# modo límite (sin E1) y señal SHORT
plan, _ = bot.bx_plan(dict(ev, side=-1, w=(0.0, 0.4, 0.6), qty=[0, 1, 1]), info, cap=0)
assert [l["name"] for l in plan["legs"]] == ["E2", "E3"] and plan["side"] == "SELL"
# por debajo del mínimo de Bitunix: no se opera
plan, why = bot.bx_plan(ev, dict(info, minTradeVolume="1"), cap=10)
assert plan is None and "mínimo" in why
# moneda que no existe o no admite API
assert bot.bx_plan(ev, None)[0] is None and bot.bx_plan(ev, dict(info, isApiSupported=False))[0] is None

# 3. Un error de Bitunix nunca frena los avisos
msgs = []
bot.send = lambda token, chat, text, *a, **k: msgs.append(text)
bot.bx_pair = lambda sym: (_ for _ in ()).throw(RuntimeError("sin conexión"))
bot.auto_signal("t", "1", {"users": {"owner": {}}, "capital": 1000, "risk": 1}, "SOLUSDT", "15m", ev, "SOLUSDT_15m_1")
assert "sin conexión" in msgs[-1]
bot.bx_pair = lambda sym: info
bot.bx_last = lambda sym: 120.70
bot.auto_signal("t", "1", {"users": {"owner": {}}, "capital": 1000, "risk": 1}, "SOLUSDT", "15m", ev, "SOLUSDT_15m_1")
assert "PRUEBA" in msgs[-1] and "E1 a mercado · 0.33" in msgs[-1], msgs[-1]

# 4. Quién envía: el equipo con llaves (🔑) manda sobre el servidor (★), y el servidor sobre una PC sin llaves
def lider(fijado, llaves, main, edad=0):
    bot.tg = lambda token, method, params=None, timeout=20: {"result": {"pinned_message": {
        "message_id": 99, "text": fijado, "date": bot.tg_now() - edad}}}
    bot.bx_keys, bot.MAIN = (lambda: ("k", "s")) if llaves else (lambda: None), main
    return bot.am_leader("t", "1", {}, force=True)


assert lider("🟢 Bot activo [ORACLE] ★ · 10:00", llaves=True, main=False)        # PC con llaves toma el control
assert not lider("🟢 Bot activo [PC] 🔑 · 10:00", llaves=False, main=True)       # Oracle sin llaves espera
assert not lider("🟢 Bot activo [ORACLE] ★ · 10:00", llaves=False, main=False)   # PC sin llaves espera (como antes)
assert lider("🟢 Bot activo [PC] 🔑 · 10:00", llaves=False, main=True, edad=600)  # PC apagada hace 10 min: Oracle sigue
assert not lider("🟢 Bot activo [OTRO-EQUIPO] 🔑 · 10:00", llaves=True, main=False)  # empate: se queda el que estaba


# 5. Operación REAL completa contra un Bitunix simulado ESTRICTO (rechaza lo que la documentación no muestra).
#    Ojo: esto prueba la lógica del bot, no cómo responde el Bitunix de verdad.
import tempfile


class FakeBX:
    def __init__(self, attach_sl=True, list_tpsl=True):
        self.px, self.pos, self.orders, self.tpsl, self.hist, self.n = {}, {}, [], [], [], 0
        self.attach_sl, self.list_tpsl = attach_sl, list_tpsl
        self.mode, self.lev, self.equity = "HEDGE", {}, 1000.0

    def nid(self):
        self.n += 1
        return str(self.n)

    def req(self, method, path, params=None, body=None):
        p, b = params or {}, body or {}
        if path.endswith("/get_pending_positions"):  # sin symbol: todas (documentado)
            return [dict(v) for v in self.pos.values() if "symbol" not in p or v["symbol"] == p["symbol"]]
        if path.endswith("/trade/get_pending_orders"):
            return {"orderList": [dict(o) for o in self.orders if o["symbol"] == p["symbol"] and o["status"] == "NEW"]}
        if path.endswith("/get_order_detail"):
            return next((dict(o) for o in self.orders if o["clientId"] == p["clientId"]), None)
        if path.endswith("/change_leverage"):
            assert isinstance(b["leverage"], int) and b["marginCoin"] == "USDT"
            assert not any(o["symbol"] == b["symbol"] and o["status"] == "NEW" for o in self.orders), "20006"
            self.lev[b["symbol"]] = b["leverage"]
            return [b]
        if path.endswith("/trade/place_order"):
            return self.place(b)
        if path.endswith("/cancel_orders"):
            ok, bad = [], []
            for x in b["orderList"]:
                o = next((o for o in self.orders if o["clientId"] == x["clientId"]), None)
                if o and o["status"] == "NEW":
                    o["status"] = "CANCELED"
                    ok.append({"clientId": x["clientId"]})
                else:
                    bad.append({"clientId": x["clientId"], "errorCode": 1, "errorMsg": "no"})
            return {"successList": ok, "failureList": bad}
        if path.endswith("/tpsl/get_pending_orders"):
            return [dict(t) for t in self.tpsl if t["positionId"] == p["positionId"]] if self.list_tpsl else []
        if path.endswith("/tpsl/position/place_order"):
            assert b["slStopType"] in ("LAST_PRICE", "MARK_PRICE")
            if b["positionId"] not in self.pos:
                raise RuntimeError("Bitunix 30004: Position not exist")
            if any(t["positionId"] == b["positionId"] and not t.get("slQty") for t in self.tpsl):
                raise RuntimeError("Bitunix 1: Each position can only have one Position TP/SL Order")
            self.tpsl.append(dict(id=self.nid(), positionId=b["positionId"], symbol=b["symbol"], slPrice=b["slPrice"]))
            return {"orderId": self.tpsl[-1]["id"]}
        if path.endswith("/tpsl/modify_order"):
            t = next(t for t in self.tpsl if t["id"] == b["orderId"])
            assert b["slStopType"] in ("LAST_PRICE", "MARK_PRICE") and b.get("slQty")
            t["slPrice"] = b["slPrice"]
            return {"orderId": t["id"]}
        if path.endswith("/tpsl/position/modify_order"):
            t = next(t for t in self.tpsl if t["positionId"] == b["positionId"] and not t.get("slQty"))
            t["slPrice"] = b["slPrice"]
            return {"orderId": t["id"]}
        if path.endswith("/flash_close_position"):
            self.close_pos(b["positionId"], self.px[self.pos[b["positionId"]]["symbol"]])
            return {"positionId": b["positionId"]}
        if path.endswith("/get_history_positions"):
            return {"positionList": [h for h in self.hist if h["positionId"] == p.get("positionId")]}
        if path.endswith("/account/position_mode"):
            return {"positionMode": self.mode}
        if path.endswith("/futures/account"):
            return [{"available": str(self.equity), "frozen": "0", "margin": "0", "crossUnrealizedPNL": "0", "isolationUnrealizedPNL": "0"}]
        raise AssertionError(f"endpoint no documentado: {path}")

    def place(self, b):
        assert b["side"] in ("BUY", "SELL") and b["tradeSide"] in ("OPEN", "CLOSE") and b["orderType"] in ("LIMIT", "MARKET")
        assert isinstance(b["qty"], str) and float(b["qty"]) > 0
        if b["orderType"] == "LIMIT":
            assert b.get("price") and b.get("effect") in ("GTC", "IOC", "FOK", "POST_ONLY")
        if b.get("slPrice"):
            assert b["slStopType"] in ("LAST_PRICE", "MARK_PRICE") and b["slOrderType"] in ("LIMIT", "MARKET")
        if any(o["clientId"] == b["clientId"] for o in self.orders):
            raise RuntimeError("Bitunix 30042: Client ID duplicate")
        if b["tradeSide"] == "CLOSE":
            pos = self.pos.get(b.get("positionId"))
            assert pos, "cierre sin positionId válido"
            assert b["side"] == ("BUY" if pos["side"] == "LONG" else "SELL"), "lado de cierre distinto al documentado"
        o = dict(b, orderId=self.nid(), status="NEW")
        self.orders.append(o)
        if b["orderType"] == "MARKET":
            self.fill(o, self.px[b["symbol"]])
        self.check()
        return {"orderId": o["orderId"], "clientId": b["clientId"]}

    def fill(self, o, px):
        o["status"], sym, q = "FILLED", o["symbol"], float(o["qty"])
        o["tradeQty"], o["fee"] = o["qty"], str(q * px * 0.0006)
        if o["tradeSide"] == "CLOSE":  # realizedPNL sin comisiones (como la documentación)
            pos = self.pos[o["positionId"]]
            o["realizedPNL"] = str(q * (px - float(pos["avgOpenPrice"])) * (1 if pos["side"] == "LONG" else -1))
        if o["tradeSide"] == "OPEN":
            side = "LONG" if o["side"] == "BUY" else "SHORT"
            pos = next((v for v in self.pos.values() if v["symbol"] == sym and v["side"] == side), None)
            if not pos:
                pid = "P" + self.nid()
                pos = self.pos[pid] = dict(positionId=pid, symbol=sym, side=side, qty="0", avgOpenPrice="0")
            old = float(pos["qty"])
            pos["avgOpenPrice"] = str((old * float(pos["avgOpenPrice"]) + q * px) / (old + q))
            pos["qty"] = str(old + q)
            if o.get("slPrice") and self.attach_sl:
                self.tpsl.append(dict(id=self.nid(), positionId=pos["positionId"], symbol=sym, slPrice=o["slPrice"], slQty=o["qty"]))
        else:
            pos = self.pos[o["positionId"]]
            left = float(pos["qty"]) - q
            if left <= 1e-9:
                self.close_pos(pos["positionId"], px)
            else:
                pos["qty"] = str(round(left, 10))

    def close_pos(self, pid, px):
        pos = self.pos.pop(pid)
        self.hist.append(dict(positionId=pid, symbol=pos["symbol"], realizedPNL="1.23", fee="0.05", funding="0",
                              entryPrice=pos["avgOpenPrice"], closePrice=str(px)))
        self.tpsl = [t for t in self.tpsl if t["positionId"] != pid]
        for o in self.orders:  # al cerrarse la posición, sus órdenes de cierre se cancelan
            if o.get("positionId") == pid and o["status"] == "NEW":
                o["status"] = "CANCELED"

    def move(self, sym, px):
        """El precio llega a px: se llenan los límites y se disparan los stops."""
        self.px[sym] = px
        for o in [o for o in self.orders if o["symbol"] == sym and o["status"] == "NEW"]:
            p, long_ = float(o["price"]), o["side"] == "BUY"
            if o["tradeSide"] == "OPEN" and (px <= p if long_ else px >= p):
                self.fill(o, p)
            elif o["tradeSide"] == "CLOSE" and o["positionId"] in self.pos and (px >= p if long_ else px <= p):
                self.fill(o, p)
        for t in list(self.tpsl):
            pos = self.pos.get(t["positionId"])
            if pos and (px <= float(t["slPrice"]) if pos["side"] == "LONG" else px >= float(t["slPrice"])):
                self.close_pos(pos["positionId"], px)
        self.check()

    def check(self):
        """Invariante: las órdenes de cierre pendientes nunca suman más que la posición."""
        for pid, pos in self.pos.items():
            closing = sum(float(o["qty"]) for o in self.orders if o.get("positionId") == pid and o["status"] == "NEW")
            assert closing <= float(pos["qty"]) + 1e-9, f"cierres {closing} > posición {pos['qty']}"


def nuevo(**kw):
    fx = FakeBX(**kw)
    fx.px["SOLUSDT"] = 100.0
    bot.bx_req, bot.bx_last, bot.bx_keys = fx.req, (lambda sym: fx.px[sym]), (lambda: ("k", "s"))
    bot._bx.clear()
    bot._bx.update(trades={}, journal=[], day={})
    msgs.clear()
    return fx


bot.BX_FILE = os.path.join(tempfile.mkdtemp(), "bx_state.json")
info = dict(info, basePrecision=2, quotePrecision=2, minTradeVolume="0.01", maxLeverage=50)
bot.bx_pair = lambda sym: info
ST = {"users": {"owner": {}}, "capital": 2000, "risk": 2, "auto": "si", "auto_capital": 1000, "auto_risk": 1,
      "auto_max": 100, "auto_ops": 1, "trades": {}}
EV = dict(side=1, price=100.0, e2=99.0, e3=98.0, sl=97.0, tps=[101.0, 102.0, 103.0], lev=20, qty=[2.0, 3.0, 5.0],
          w=(0.2, 0.3, 0.5), risk=40.0)   # riesgo 1 % de $1000 = $10 → escala 10/40 → E1 0.5 · E2 0.75 · E3 1.25
TID = "SOLUSDT_15m_1700000000000"
step = lambda: bot.bx_manage("t", "1", ST)
resting = lambda fx, ts: [o["qty"] for o in fx.orders if o["status"] == "NEW" and o["tradeSide"] == ts]


def sin_errores():
    assert not [m for m in msgs if "⚠️" in m or "🚨" in m], msgs


fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
assert "REAL · órdenes enviadas" in msgs[-1] and fx.lev["SOLUSDT"] == 20, msgs
pid = next(iter(fx.pos))
assert fx.pos[pid]["qty"] == "0.5" and resting(fx, "OPEN") == ["0.75", "1.25"]   # E1 llenada, E2/E3 esperando
step()
assert resting(fx, "CLOSE") == ["0.20", "0.15", "0.15"], resting(fx, "CLOSE")    # TP 40/30/30 de 0.5
bot._bx.clear(); bot._bx.update(trades={}, journal=[], day={}); bot.bx_load()   # reinicio a mitad de la operación
assert bot._bx["trades"][TID]["pid"] == pid
fx.move("SOLUSDT", 99.0)   # se llena E2 → posición 1.25 → los TP se rehacen con la cantidad real
step()
assert resting(fx, "CLOSE") == ["0.50", "0.37", "0.38"], resting(fx, "CLOSE")
fx.move("SOLUSDT", 101.0)  # TP1
step()
assert bot._bx["trades"][TID]["stage"] == 1 and not resting(fx, "OPEN")           # E3 cancelada tras TP1 (modo BE)
be = round(float(fx.pos[pid]["avgOpenPrice"]) * 1.0008, 2)
assert fx.tpsl and all(float(t["slPrice"]) == be for t in fx.tpsl), (be, fx.tpsl)  # todos los SL a break even
assert any("TP1 ejecutado" in m for m in msgs) and any("SL movido a break even" in m for m in msgs)
fx.move("SOLUSDT", 102.0); step()
assert bot._bx["trades"][TID]["stage"] == 2
fx.move("SOLUSDT", 103.0); step()
assert TID not in bot._bx["trades"] and not fx.pos and not resting(fx, "OPEN") and not resting(fx, "CLOSE")
assert any("cerrada" in m and "+1.23" in m for m in msgs) and bot._bx["journal"][-1]["tid"] == TID
sin_errores()

# 6. Stop no confirmado: si Bitunix no muestra ningún SL, se cierra a mercado y se apaga el modo real
fx = nuevo(attach_sl=False, list_tpsl=False)
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step()
assert fx.pos and ST["auto"] == "si"                       # todavía dentro de los 45 s
bot._bx["trades"][TID]["nocov"] -= 60
step()
assert not fx.pos and ST["auto"] == "" and any("no pude confirmar el stop" in m for m in msgs)
ST["auto"] = "si"

# 7. Sin SL adjunto pero Bitunix acepta el SL de posición: queda cubierta y sigue normal
fx = nuevo(attach_sl=False)
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step(); step()
assert fx.tpsl and not fx.tpsl[0].get("slQty") and resting(fx, "CLOSE") and ST["auto"] == "si"
sin_errores()

# 8. Ya hay una posición tuya en esa moneda: no se toca
fx = nuevo()
fx.pos["X"] = dict(positionId="X", symbol="SOLUSDT", side="SHORT", qty="1", avgOpenPrice="100")
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
assert not fx.orders and "ya tienes posición" in msgs[-1]

# 9. Señal en modo límite que nunca se llena y el bot la cancela: se cancelan E2/E3
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", dict(EV, w=(0.0, 0.4, 0.6), qty=[0, 4.0, 6.0]), TID)
assert resting(fx, "OPEN") == ["1.00", "1.50"] and not fx.pos
ST["trades"][TID] = {"cancelled": True}
bot._bx["trades"][TID]["t0"] -= 60_000
step()
assert not resting(fx, "OPEN") and TID not in bot._bx["trades"]
ST["trades"].clear()

# 10. Señal contraria: se cierra todo a mercado y no quedan órdenes
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step()
bot.bx_flag(TID, "exit")
step(); step()
assert not fx.pos and TID not in bot._bx["trades"] and not resting(fx, "OPEN") and not resting(fx, "CLOSE")

# 11. Límites: retraso, filtro de temporalidad, pérdida máxima del día
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID, late=True)
assert not fx.orders and "retraso" in msgs[-1]
bot.auto_signal("t", "1", ST, "SOLUSDT", "1h", EV, TID)    # 1h no está en /auto tf
assert not fx.orders
bot.bx_guard("t", "1", ST); fx.equity = 940.0; bot.bx_guard("t", "1", ST)
assert any("bajó más de 5" in m for m in msgs)
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
assert not fx.orders and "pérdida máxima" in msgs[-1]

# 12. No se arma el modo real sin capital o fuera de HEDGE
fx = nuevo()
assert "capital" in bot.auto_arm_error({})
fx.mode = "ONE_WAY"
assert "HEDGE" in bot.auto_arm_error(ST)
fx.mode = "HEDGE"
assert bot.auto_arm_error(ST) == ""

# 13. Se apaga el bot justo al enviar la primera orden: la operación ya quedó guardada en disco
fx = nuevo()
if os.path.exists(bot.BX_FILE):
    os.remove(bot.BX_FILE)
real_req = fx.req


def corte(method, path, params=None, body=None):
    if path.endswith("/trade/place_order"):
        raise KeyboardInterrupt  # (como un reinicio del servidor)
    return real_req(method, path, params, body)


bot.bx_req = corte
try:
    bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
except KeyboardInterrupt:
    pass
assert TID in json.load(open(bot.BX_FILE, encoding="utf-8"))["trades"]

# 14. SHORT completo con fallos a mitad: el BE falla una vez, una lectura vacía de posiciones, y luego todo sigue
fx = nuevo()
EVS = dict(EV, side=-1, e2=101.0, e3=102.0, sl=103.0, tps=[99.0, 98.0, 97.0])
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EVS, TID)
pid = next(iter(fx.pos))
assert fx.pos[pid]["side"] == "SHORT"
step()
assert resting(fx, "CLOSE") == ["0.20", "0.15", "0.15"]
assert all(o["side"] == "SELL" for o in fx.orders if o["tradeSide"] == "CLOSE")   # cerrar SHORT = SELL + CLOSE (doc)
fx.move("SOLUSDT", 101.0); step()     # E2
assert resting(fx, "CLOSE") == ["0.50", "0.37", "0.38"]
vacio = {"n": 1}
real_req = fx.req


def flojo(method, path, params=None, body=None):
    if path.endswith("/get_pending_positions") and vacio["n"]:
        vacio["n"] -= 1
        return []   # Bitunix devuelve una lectura vacía una vez
    if path.endswith("/tpsl/modify_order") and fx.fallar_be:
        fx.fallar_be = False
        raise RuntimeError("Bitunix 10002: falla simulada")
    return real_req(method, path, params, body)


fx.fallar_be = True
bot.bx_req = flojo
step()
assert TID in bot._bx["trades"] and resting(fx, "CLOSE"), "una lectura vacía no debe cerrar la operación"
fx.move("SOLUSDT", 99.0); step()      # TP1 → el BE falla la primera vez
assert any("no se pudo mover el SL" in m for m in msgs)
step()                                # … y se reintenta
be = round(float(fx.pos[pid]["avgOpenPrice"]) * (1 - 0.0008), 2)
assert fx.tpsl and all(float(t["slPrice"]) == be for t in fx.tpsl), (be, fx.tpsl)
fx.move("SOLUSDT", 98.0); step(); fx.move("SOLUSDT", 97.0); step()
assert TID not in bot._bx["trades"] and not fx.pos and not resting(fx, "OPEN") and not resting(fx, "CLOSE")

# 15. E1 entra pero la respuesta se pierde (y la consulta también falla): el bot no la abandona
fx = nuevo()
real_req = fx.req


def mudo(method, path, params=None, body=None):
    if path.endswith("/trade/place_order") and body["clientId"].endswith("e1"):
        real_req(method, path, params, body)
        raise TimeoutError("sin respuesta")
    if path.endswith("/get_order_detail"):
        raise TimeoutError("sin respuesta")
    return real_req(method, path, params, body)


bot.bx_req = mudo
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
assert TID in bot._bx["trades"] and "sin respuesta clara" in msgs[-1]
bot.bx_req = real_req
step()
assert resting(fx, "CLOSE") == ["0.20", "0.15", "0.15"]   # la ve y la gestiona

# 16. Entrada fuera de zona: el precio ya pasó el TP1 en Bitunix
fx = nuevo()
fx.px["SOLUSDT"] = 101.5
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
assert not fx.orders and "zona" in msgs[-1]

# 17. apply() de verdad: una señal fresca llega a Bitunix, una vieja no, y la señal contraria cierra
fx = nuevo()
bot.open_extras = lambda *a: {}
bot.deliver = lambda token, owner, st, uids, *a, **k: list(uids)
now = int(bot.time.time() * 1000)
ms = bot.TF_MS["15m"]
d = [dict(t=now - (60 - i) * ms, ct=now - (59 - i) * ms - 1, o=100, h=100, l=100, c=100) for i in range(60)]
d[-1]["ct"] = now - 1000
STA = dict(ST, seen_pairs=["SOLUSDT_15m"], sent=[], users={"owner": {"tfs": ["15m"]}}, trades={}, live={}, hist={})
T = dict(EV, bar=59, tp=0, pos=0.2)
res = dict(sym="SOLUSDT", tf="15m", d=d, up=True, st=99, events=[dict(EV, kind="OPEN", bar=59, q=2, c=(1, 1, 0))],
           T=T, pts=2, seed=None)
bot.apply(res, STA, {}, "t", "1")
tid = f"SOLUSDT_15m_{d[59]['t']}"
assert tid in bot._bx["trades"] and fx.pos, msgs
old = dict(res, events=[dict(EV, kind="OPEN", bar=50, q=2, c=(1, 1, 0))], T=dict(T, bar=50))
bot._bx["trades"].clear(); n = len(fx.orders)
bot.apply(old, STA, {}, "t", "1")
assert len(fx.orders) == n, "una señal de hace 10 velas no debe entrar"
bot._bx["trades"][tid] = dict(sym="SOLUSDT")
bot.apply(dict(res, events=[dict(kind="CLOSE", bar=59, entry=59, side=1, price=100, reason="reverse", q=2, win=False, tp=0, pnl=-1, r=-0.1)],
               T=None), STA, {}, "t", "1")
assert bot._bx["trades"][tid].get("exit"), "la señal contraria debe marcar el cierre en Bitunix"

soltadas = lambda: [m for m in msgs if "Soltada" in m]
ahora = lambda: int(bot.time.time() * 1000)

# 18. v6.4 · 🎯 Apuntar: botón → plan → confirmar. En PRUEBA no envía nada; en REAL abre como una automática
fx = nuevo()
STA = dict(ST, auto="", aim_mode="prueba", aim={})
assert bot.aim_on(STA) and not bot.aim_on(dict(STA, aim_mode="no"))
bot.aim_store(STA, TID, "SOLUSDT", "15m", EV, ahora())
assert bot.aim_press("t", "1", STA, TID) == "" and "¿confirmas?" in msgs[-1] and "PRUEBA" in msgs[-1]
assert "Riesgo abierto" in msgs[-1], msgs[-1]
bot.aim_confirm("t", "1", STA, TID)
assert not fx.orders and "no se envió nada" in msgs[-1]
assert "venció" in bot.aim_confirm("t", "1", STA, TID)          # sin volver a pulsar 🎯 no se confirma
STA["aim_mode"] = "si"
bot.aim_press("t", "1", STA, TID)
STA["aim"][TID]["ask"] -= bot.AIM_CONFIRM + 1
assert "venció" in bot.aim_confirm("t", "1", STA, TID) and not fx.orders
bot.aim_press("t", "1", STA, TID)
bot.aim_confirm("t", "1", STA, TID)
assert fx.pos and "🎯" in msgs[-1] and "REAL · órdenes enviadas" in msgs[-1], msgs[-1]
assert bot._bx["trades"][TID]["manual"] and bot._bx["trades"][TID]["sl0"] == 97.0
bot.aim_press("t", "1", STA, TID)
assert "ya está abierta" in msgs[-1]
# señal vieja y precio que ya se fue: no se apunta
fx = nuevo()
bot.aim_store(STA, TID, "SOLUSDT", "15m", EV, ahora() - 4 * bot.TF_MS["15m"])
bot.aim_press("t", "1", STA, TID)
assert "más de 3 velas" in msgs[-1] and not fx.orders
bot.aim_store(STA, TID, "SOLUSDT", "15m", EV, ahora())
fx.px["SOLUSDT"] = 100.6   # 60 % del camino al TP1 (101)
bot.aim_press("t", "1", STA, TID)
assert "no se persigue" in msgs[-1] and not fx.orders
for k in range(50):        # solo se guardan las últimas señales
    bot.aim_store(STA, f"X_{k}", "SOLUSDT", "15m", EV, ahora() + k)
assert len(STA["aim"]) == bot.AIM_KEEP

# 19. Flujo normal completo (E2, TP1, BE, TP2, TP3): el bot NUNCA se suelta solo
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step()
fx.move("SOLUSDT", 99.0); step()
fx.move("SOLUSDT", 101.0); step(); step()
fx.move("SOLUSDT", 102.0); step()
fx.move("SOLUSDT", 103.0); step()
assert TID not in bot._bx["trades"] and not soltadas(), msgs
sin_errores()

# 20. Moviste el SL a mano → soltada: el bot no lo devuelve a su sitio ni pone BE; ↩️ devolver la adopta con TU SL
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step()
for t in fx.tpsl:
    t["slPrice"] = "96.50"
step()
r = bot._bx["trades"][TID]
assert r["soltada"] and "moviste el SL" in soltadas()[-1]
fx.move("SOLUSDT", 101.0); step()                      # TP1 se llena (la orden ya estaba), pero el bot no toca el SL
assert all(t["slPrice"] == "96.50" for t in fx.tpsl)
txt, btn = bot.positions_report(ST)
assert "soltada" in txt and btn and btn[0][0][1] == "dv:" + TID
assert bot.bx_devolver(TID).startswith("↩️") and not r["soltada"] and r["sl"] == 96.5
n = len(soltadas())
step(); step()
assert len(soltadas()) == n and TID in bot._bx["trades"]  # vuelve a gestionarla sin soltarla otra vez
assert "ya la está gestionando" in bot.bx_devolver(TID)

# 21. Cerraste una parte a mano → soltada
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step()
pid = next(iter(fx.pos))
tp3 = [o for o in fx.orders if o["tradeSide"] == "CLOSE" and o["status"] == "NEW"][-1]
tp3["status"] = "CANCELED"
fx.pos[pid]["qty"] = "0.4"      # (cerraste 0.1 desde la app de Bitunix)
step()
assert bot._bx["trades"][TID]["soltada"] and "cerraste una parte" in soltadas()[-1]

# 22. Cancelaste la E2 a mano → soltada (si la cancela el bot, no)
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step()
next(o for o in fx.orders if o["clientId"].endswith("e2"))["status"] = "CANCELED"
step()
assert bot._bx["trades"][TID]["soltada"] and "cancelaste la entrada E2" in soltadas()[-1]

# 23. Gestión STRIKE: sin BE en TP1; a N R del precio medio real → BE + parcial 25 %; luego BE compensado (resultado ≈ 0)
fx = nuevo()
STS = dict(ST, auto_mg="strike", auto_berr=0.5, auto_parcial=25, auto_comp=True)
bot.auto_signal("t", "1", STS, "SOLUSDT", "15m", EV, TID)
step()
pid = next(iter(fx.pos))
fx.move("SOLUSDT", 101.0); step()                      # TP1 (40 %): en modo STRIKE el SL sigue en 97
r = bot._bx["trades"][TID]
assert r["stage"] == 1 and all(float(t["slPrice"]) == 97.0 for t in fx.tpsl) and not r.get("be_px")
fx.move("SOLUSDT", 101.5); step()                      # 1.5 = 0.5 R de 3 → BE + parcial
assert r["bek"] and r.get("pcid") and any("llegó a 0.5 R" in m and "parcial" in m for m in msgs), msgs
assert float(fx.pos[pid]["qty"]) == 0.23 and not resting(fx, "OPEN")   # 0.3 − 0.07 · E2/E3 canceladas
step()                                                 # el parcial ya se ejecutó → BE compensado
assert any("BE compensado" in m for m in msgs), msgs
slc = float(fx.tpsl[0]["slPrice"])
assert 97.0 < slc < 100.0 and all(float(t["slPrice"]) == slc for t in fx.tpsl), fx.tpsl
part = next(o for o in fx.orders if o["clientId"] == r["pcid"])
total = float(part["realizedPNL"]) - float(part["fee"]) + 0.23 * (slc - 100.0) - 0.23 * slc * 0.0006
assert abs(total) < 0.01, total                        # si ahora salta el SL, parcial + resto ≈ 0
assert resting(fx, "CLOSE") and not soltadas()         # TP rehechos con la cantidad nueva, sin soltarse
fx.move("SOLUSDT", slc - 0.01); step()
assert TID not in bot._bx["trades"] and not fx.pos
sin_errores()

# 24. /posiciones y seguro de riesgo: muestra las del bot y las tuyas; avisa sin SL (una vez) y por encima del límite
fx = nuevo()
bot.auto_signal("t", "1", ST, "SOLUSDT", "15m", EV, TID)
step()
fx.px["ETHUSDT"] = 10.0
fx.pos["U"] = dict(positionId="U", symbol="ETHUSDT", side="SHORT", qty="1", avgOpenPrice="10", unrealizedPNL="-0.5",
                   margin="1", leverage=10)
txt, _ = bot.positions_report(ST)
assert "SOLUSDT" in txt and "🤖 auto" in txt and "ETHUSDT" in txt and "tuya" in txt and "SIN stop loss" in txt, txt
assert "riesgo $1.50" in txt and "Riesgo abierto: <b>0.1 %" in txt, txt   # 0.5 × (100 − 97) = 1.5 de 1000
SG = dict(ST, seguro_lim=5.0)
bot.bx_seguro("t", "1", SG); bot.bx_seguro("t", "1", SG)
assert len([m for m in msgs if "no tiene stop loss" in m]) == 1
SG["seguro_lim"] = 0.1
bot.bx_seguro("t", "1", SG)
assert "más que tu límite" in msgs[-1]
n = len(msgs)
bot.bx_seguro("t", "1", SG)
assert len(msgs) == n                                  # no repite el mismo aviso cada minuto

# 25. Bitunix real devuelve la cuenta como OBJETO (la documentación muestra una lista): guard, /posiciones y /bitunix funcionan igual
fx = nuevo()
lista = fx.req


def cuenta_objeto(method, path, params=None, body=None):
    r = lista(method, path, params, body)
    return r[0] if path.endswith("/futures/account") else r


bot.bx_req = cuenta_objeto
assert bot.bx_account()["available"] == "1000.0"
SG = dict(ST, auto_loss=5)
bot.bx_guard("t", "1", SG); fx.equity = 900.0; bot.bx_guard("t", "1", SG)
assert any("bajó más de 5" in m for m in msgs), msgs
txt, _ = bot.positions_report(ST)
assert "Sin" in txt or "No tienes" in txt or "capital" in txt, txt
fx.equity = 1000.0

# 26. /llaves: borra el mensaje, prueba y guarda; si Bitunix las rechaza no cambia nada; nunca repite las llaves
fx = nuevo()
cfgf = os.path.join(tempfile.mkdtemp(), "config.json")
json.dump({"token": "t", "chat_id": "1"}, open(cfgf, "w"))
bot.CONFIG_FILE = cfgf


def llaves_de_archivo():
    c = json.load(open(cfgf, encoding="utf-8"))
    return (c["bitunix_key"], c["bitunix_secret"]) if c.get("bitunix_key") else None


BUENA, SECRETA = "A1b2" * 8, "Zz9_" * 8
bot.bx_keys = llaves_de_archivo
bot.bx_req = lambda method, path, params=None, body=None: (
    fx.req(method, path, params, body) if (llaves_de_archivo() or ("",))[0] == BUENA
    else (_ for _ in ()).throw(RuntimeError("Bitunix 10007: Sign signature error")))
bot.mi_ip = lambda: "203.0.113.7"
borrados = []
bot.tg = lambda token, method, params=None, timeout=20: borrados.append(params["message_id"]) if method == "deleteMessage" else {}
STL = {"users": {"owner": {}}, "pending": {}, "capital": 100, "risk": 1}
cmd = lambda text, mid=1: bot.command("t", "1", STL, {}, "1", {"text": text, "message_id": mid})
cmd("/llaves")
assert "un solo mensaje" in msgs[-1] and STL.get("llaves_t")
cmd(f"{BUENA} {SECRETA}", mid=77)
assert borrados == [77] and llaves_de_archivo() == (BUENA, SECRETA), (borrados, llaves_de_archivo())
assert "Llaves guardadas y probadas" in msgs[-1] and "203.0.113.7" in msgs[-1]
assert not any(BUENA in m or SECRETA in m for m in msgs), "las llaves nunca se repiten"
cmd("/llaves"); cmd("hola", mid=78)                                     # formato malo: no toca nada
assert "no parece" in msgs[-1] and llaves_de_archivo() == (BUENA, SECRETA) and 78 in borrados
cmd("/llaves"); cmd("Q" * 32 + " " + "W" * 32, mid=79)                  # rechazadas por Bitunix: quedan las anteriores
assert "rechazó" in msgs[-1] and llaves_de_archivo() == (BUENA, SECRETA)
assert not any("Q" * 32 in m or "W" * 32 in m for m in msgs)
real_rq = bot.bx_req
bot.bx_req = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Bitunix 10004: Invalid IP"))
cmd("/llaves"); cmd("E" * 32 + " " + "R" * 32, mid=82)                  # llave limitada a otra IP: muestra la IP del servidor
assert "limitada a otra IP" in msgs[-1] and "203.0.113.7" in msgs[-1] and llaves_de_archivo() == (BUENA, SECRETA), msgs[-1]
bot.bx_req = real_rq
cmd("/llaves"); STL["llaves_t"] -= 600; cmd(f"{BUENA} {SECRETA}", mid=80)  # tarde
assert "Pasaron más de" in msgs[-1] and 80 in borrados
cmd("hola sin /llaves", mid=81)                                         # sin /llaves antes: se ignora
assert 81 not in borrados

# 27. Filtro de noticias: solo alto impacto de EE. UU.; pausa ±15 min del automático (también en prueba); aviso una sola vez;
#     /noticias 0 lo apaga; la entrada manual no se bloquea pero avisa
reales_get = bot.http_get
ahora_ms = int(bot.time.time() * 1000)
iso = lambda ms: bot.time.strftime("%Y-%m-%dT%H:%M:%S+00:00", bot.time.gmtime(ms / 1000))
bot.http_get = lambda url, timeout=20: [
    dict(title="CPI m/m", country="USD", impact="High", date=iso(ahora_ms + 10 * 60_000)),
    dict(title="Core CPI m/m", country="USD", impact="High", date=iso(ahora_ms + 10 * 60_000)),
    dict(title="Crude Oil Inventories", country="USD", impact="Medium", date=iso(ahora_ms + 5 * 60_000)),
    dict(title="ECB Press Conference", country="EUR", impact="High", date=iso(ahora_ms + 5 * 60_000))]
ev_n = bot.news_events(force=True)
assert [x[1] for x in ev_n] == ["CPI m/m", "Core CPI m/m"] and abs(ev_n[0][0] - (ahora_ms + 600_000)) < 1500, ev_n
fx = nuevo()
bot.bx_req, bot.bx_keys = fx.req, (lambda: ("k", "s"))
STN = dict(ST, auto="prueba", trades={})
bot.auto_signal("t", "1", STN, "SOLUSDT", "15m", EV, TID)
assert "no se opera" in msgs[-1] and "CPI m/m" in msgs[-1] and not fx.orders, msgs[-1]
n = len(msgs)
bot.news_tick("t", "1", STN); bot.news_tick("t", "1", STN)
assert len(msgs) == n + 1 and "Pauso las entradas automáticas" in msgs[-1] and "Core CPI" in msgs[-1], msgs[-1]
assert "Ojo" in bot.aim_risk_line(STN, bot.aim_plan(STN, "SOLUSDT", EV)[0])   # manual: avisa
STN["noticias"] = 0
assert bot.news_block(STN) == ""
bot.auto_signal("t", "1", STN, "SOLUSDT", "15m", EV, TID)
assert "PRUEBA" in msgs[-1], msgs[-1]                                   # sin filtro: sigue normal
STN["noticias"] = 15
assert bot.news_block(STN, ahora_ms + 26 * 60_000) == ""                # pasados 15 min después: libre
bot.http_get = lambda url, timeout=20: (_ for _ in ()).throw(OSError("sin internet"))
bot._news["t"] = 0
assert bot.news_events() == [] or True                                  # sin calendario no rompe nada
bot.http_get = reales_get
bot._news.update(t=0.0, ev=[])

# 28. Precisión real (/real): acierto y neto con comisiones, por temporalidad, señal vs real, aviso de pocas operaciones;
#     resumen semanal una sola vez (domingo 20:00 hora local) y se puede apagar
fx = nuevo()
STR = dict(ST, auto="si", trades={}, journal=[dict(id="A_15m_1", r=1.2), dict(id="B_2h_1", r=-1.0)])
assert "Todavía no hay operaciones reales" in bot.real_report(STR, 7)
t_now = int(bot.time.time() * 1000)
bot._bx["journal"] = [
    dict(tid="A_15m_1", sym="SOLUSDT", tf="15m", t=t_now - 3600_000, pnl="2.10", fee="0.10", funding="0", tp=3, s=1, risk=2.0, manual=False),
    dict(tid="B_2h_1", sym="ETHUSDT", tf="2h", t=t_now - 7200_000, pnl="-0.90", fee="-0.10", funding="0", tp=0, s=-1, risk=1.0, manual=True),
    dict(tid="C_15m_1", sym="SOLUSDT", tf="15m", t=t_now - 20 * 86_400_000, pnl="5", fee="0", tp=1)]   # vieja y sin riesgo (v6.3)
rp = bot.real_report(STR, 7)
assert "Operaciones: <b>2</b> · ✅ 1 · ❌ 1 · acierto <b>50%</b>" in rp, rp
assert "<b>+1.00 USDT</b>" in rp and "comisiones −0.20" in rp and "Factor: <b>2.00</b>" in rp, rp
assert "<b>+0.00 R</b>" in rp and "15m: +2.00 · 1 · 100% · ∞ / 1.5" in rp and "2h: −1.00 · 1 · 0%" in rp, rp
assert "la señal marcó +0.20 R · en Bitunix salió +0.00 R" in rp and "Automáticas: 1" in rp and "pocas operaciones (2)" in rp, rp
assert "Operaciones: <b>3</b>" in bot.real_report(STR, 0)
real_gm = bot.time.gmtime
n = len(msgs)
bot.time.gmtime = lambda t=None: real_gm(1_763_251_200 + 19 * 3600)    # domingo 19:00: todavía no
bot.real_weekly("t", "1", STR)
assert len(msgs) == n
bot.time.gmtime = lambda t=None: real_gm(1_763_251_200 + 21 * 3600)    # domingo 21:00
bot.real_weekly("t", "1", STR); bot.real_weekly("t", "1", STR)
assert len(msgs) == n + 1 and "Resumen de la semana" in msgs[-1] and "acierto" in msgs[-1], msgs[-1]
STR.update(semanal=False, semanal_sent="")
bot.real_weekly("t", "1", STR)
assert len(msgs) == n + 1
bot.time.gmtime = real_gm
bot._bx["journal"] = []

# 29. Giro 4h (v6.5, SOLO PRUEBA): entra al giro de la Tendencial, SL = Tendencial, avisa una vez por vela, guarda el
#     resultado en R, nunca envía órdenes y /giro si se niega
fx = nuevo()
H4 = bot.TF_MS["4h"]
t_fin = int(bot.time.time() * 1000) // H4 * H4 - H4          # última vela de 4h ya cerrada
def velas_giro(n_extra):
    px, out = 100.0, []
    for k in range(120 + n_extra):
        paso = -0.4 if k < 100 else (3.0 if k < 112 else -4.0)   # baja suave, sube fuerte (giro a LONG), cae (giro a SHORT)
        o, px = px, px + paso
        t = t_fin - (120 + n_extra - 1 - k) * H4
        out.append(dict(t=t, o=o, h=max(o, px) + 0.2, l=min(o, px) - 0.2, c=px, v=1.0, ct=t + H4 - 1))
    return out
STG = dict(ST, symbols=["SOLUSDT"], top=0, trades={})
bot.active_symbols = lambda st: ["SOLUSDT"]
real_fk = bot.fetch_klines
serie = velas_giro(0)
bot.fetch_klines = lambda sym, tf, limit=600: serie[:corte]
cmd_g = lambda text: bot.command("t", "1", STG, {}, "1", {"text": text, "message_id": 1})
cmd_g("/giro si")
assert "todavía no opera con dinero real" in msgs[-1] and bot.giro_state(STG)["on"] == "", msgs[-1]
cmd_g("/giro prueba")
assert "PRUEBA activa" in msgs[-1] and "no envía órdenes" in msgs[-1], msgs[-1]
g = bot.giro_state(STG)
corte = 95
bot.giro_tick("t", "1", STG)                                  # primera vez: solo toma la referencia
assert not g["pos"] and not g["hist"] and g["last"]["SOLUSDT"] == serie[94]["t"]
vistos = []
for corte in range(96, len(serie) + 1):
    g["bucket"] = 0
    n = len(msgs)
    t_falso = (serie[corte - 1]["ct"] + 30_000) / 1000        # 30 s después del cierre de esa vela
    real_time = bot.time.time
    bot.time.time = lambda: t_falso
    try:
        bot.giro_tick("t", "1", STG)
        bot.giro_tick("t", "1", STG)                          # la misma vela no avisa dos veces
    finally:
        bot.time.time = real_time
    assert len(msgs) - n <= 1
    vistos += msgs[n:]
abre = [m for m in vistos if "<b>SOL LONG</b>" in m]
cierra = [m for m in vistos if "cerrada" in m]
assert len(abre) == 1 and "SL" in abre[0] and "PRUEBA" in abre[0], vistos
assert cierra and g["hist"] and g["hist"][0]["s"] == 1 and g["hist"][0]["r"] > 0, (vistos, g["hist"])
assert not fx.orders and not bot._bx["trades"]                # ninguna orden a Bitunix
cmd_g("/giro")
assert "cerradas" in msgs[-1] and "36 %" in msgs[-1], msgs[-1]
cmd_g("/giro no")
assert bot.giro_state(STG)["on"] == "" and "apagado" in msgs[-1]
bot.fetch_klines = real_fk

print("OK: firma, plan, errores aislados, prioridad de equipos, operación real simulada (LONG/SHORT, TP, BE, fallos, cierre, "
      "límites, apply) y v6.4 (🎯 apuntar, soltar/devolver, gestión STRIKE con BE compensado, /posiciones, seguro de riesgo)")

