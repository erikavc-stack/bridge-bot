# Bridge Táctico → Telegram · v5.0

Bot gratuito que vigila **tus favoritos en 1m, 3m, 5m, 15m, 1h, 2h, 4h, 8h y 1d** (cada usuario elige con `/config`;
los nuevos empiezan con 15m, 2h, 4h y 8h) con la misma lógica del indicador Bridge Táctico v8.7
(Tendencial + Squeeze/ADX + WaveTrend) y te escribe en Telegram:

- 🟢/🔴 **Nueva señal** LONG/SHORT 2/3 o 3/3 con entrada E1, DCA E2/E3, SL, TP1‑TP3,
  **cantidad de monedas y margen de cada entrada** (listo para copiar en Bitunix)
- ✅ **TP1 alcanzado**  ·  🏁 / ⛔ / 🔄 **Cierre** con el resultado en $ y en R
- 📡 **/radar**: todas tus monedas × temporalidades de un vistazo

Ejemplo de aviso (formato tipo "Radar Me La Pela"):

```
🟢 LONG SOLUSDT.P 5M 🟢

📡 OBJETIVO DETECTADO:
├ 💰 Entrada: $120.70
├ 💰 DCA: E2: $120.44 · E3: $120.18
├ 🛟 Stop Loss: $119.70 (-0.83%)
├ ⚡ Apalancamiento: 50x
├ ⭐ Calidad: 2/3 (Tend ✓ · Mom·ADX ✓ · WT ✗)
└ 🟢 Winrate: 68% (25 ops · +0.24 R/op)

🎯 TAKE PROFITS:
├ TP1 (40%) · TP2 (30%) · TP3 (30%)
└ 1er TP (1h): Tendencial de 1h como objetivo

📦 TAMAÑO (riesgo $40): cantidad y margen de E1/E2/E3
📊 TEMPORALIDADES: 1M ▲ · 3M ▲ · 5M ▲ · 15M ▼ · 1H ▲ · 4H ▲
```

El **winrate** es el % de operaciones de esa moneda en esa temporalidad que cerraron con **ganancia neta**
(después de comisiones; la primera vez se calcula con el historial y luego se va actualizando con cada cierre).
🟢 ≥60 % · 🟡 50-59 % · 🔴 <50 %. Con `/winrate` ves la tabla completa.

## Comandos (desde Telegram)

| Comando | Qué hace |
|---|---|
| `/config` | elegir tus temporalidades con botones |
| `/usuarios` · `/expulsar ID` | ver / quitar usuarios (administrador) |
| `/limite si` · `/limite no` | 5m y 4h con órdenes límite (activado) |
| `/radar` | todas tus monedas y temporalidades: ▲▼ tendencia, ◦ tocando la Tendencial, 🟢/🔴 operación activa |
| `/radar cada 30` · `/radar off` | radar automático en Telegram (apagado por defecto) |
| `/estado` | detalle con entrada y SL de cada operación activa |
| `/winrate` | acierto de cada moneda en cada temporalidad |
| `/btc` | BTC y ETH de cerca: tendencia y señales activas en cada temporalidad (3m a 1d) |
| `/stats` | tu diario: resultado en R y $, acierto, factor, caída máxima, rachas, curva y calendario (`/stats 7`, `/stats 90`, `/stats todo`) |
| `/top 30` | además de tus favoritas vigila las 30 monedas con más volumen de Binance (`/top 0` = solo favoritas) |
| `/agregar SOL XRP` | añade monedas a favoritos |
| `/quitar SOL` | las quita |
| `/lista` | favoritos, temporalidades y ajustes |
| `/tf 1m 3m 5m 15m` | elige qué temporalidades vigilar |
| `/capital 2000` | tu capital (para calcular cantidades) |
| `/riesgo 2` | % que arriesgas por operación |
| `/maxops 5` | limitar tus operaciones abiertas a la vez (por defecto `0` = sin límite: llegan todas, también varias de la misma moneda, y el aviso lo indica) |
| `/actualizar` | el servidor de Oracle se reinicia e instala la última versión de GitHub |
| `/h4 si` · `/h4 no` | modo preciso 1m/3m (activado): solo a favor de la Tendencial de 15m y 4h |
| `/1m si` · `/1m no` | atajo para activar/quitar 1m en tus temporalidades (mejor usa `/config`; 1m pierde por comisiones) |
| `/nube` | qué equipo envía las señales y su versión; sin ninguno, GitHub avisa 15m, 1h y 4h solo al administrador (5-30 min de retraso) |
| `/eth` · `/diario` | lo mismo que `/btc` y `/stats` |
| `/stop` | (amigos) dejar de recibir señales |
| `/ayuda` | lista de comandos |

## Agregar muchas monedas de una vez (PC)
Edita `favoritos.txt` (en la carpeta del bot) y escribe las monedas separadas por comas: `AVAX, SUI, 1000PEPE`
(lo que va después de `#` es un comentario). Al reiniciar se **añaden** las nuevas; borrar una del archivo no la quita
(usa `/quitar`). En Oracle el archivo no se actualiza solo: usa `/agregar`. Si Binance avisa que vamos cerca del límite de peticiones,
el bot cambia solo a otra fuente de datos de Binance unos segundos (sin bloqueo de IP).

## Puesta en marcha en tu PC (≈5 min)

1. **Crea tu bot**: en Telegram abre **@BotFather** → `/newbot` → ponle nombre → copia el **token**.
   No lo compartas con nadie.
2. **Instala Python** (una sola vez) desde python.org marcando **"Add Python to PATH"**.
   No necesita librerías extra.
3. Descomprime esta carpeta (ej. `Documentos\bridge-bot`) y haz **doble clic en `iniciar_bot.bat`**.
4. La ventana te pide el token: pégalo (clic derecho) y pulsa Enter.
5. Abre tu bot en Telegram y pulsa **INICIAR**. La ventana pregunta "¿Eres tú?" → Enter.
   Te llega "Bridge Táctico conectado" y el radar. Listo.

Queda guardado en `config.json` (contiene tu token: no lo compartas). Para cambiar de bot, borra ese archivo.
Mientras la ventana esté abierta (y la PC encendida) revisa cada vela al cerrar.
Para que arranque solo al prender la PC: `Win + R` → `shell:startup` → pega ahí un acceso directo a `iniciar_bot.bat`.

## Servidor gratis 24/7 en Oracle Cloud (scalping con la PC apagada)

1. Crea tu cuenta en **oracle.com/cloud/free** (pide tarjeta solo para verificar; no cobra si usas lo gratis).
   Como **región de origen** elige **Brazil East (São Paulo)**: no se puede cambiar después.
2. En el menú: **Compute → Instances → Create instance**.
   - **Image**: Canonical Ubuntu (22.04 o 24.04).
   - **Shape**: Ampere **VM.Standard.A1.Flex** con 1 OCPU y 6 GB (gratis). Si dice "Out of capacity",
     usa **VM.Standard.E2.1.Micro** (también gratis).
   - Red: si no te deja activar la **IP pública**, crea antes la red con **Networking → Virtual cloud networks →
     Actions → Start VCN Wizard → "Create VCN with Internet Connectivity"** y elige su *public subnet*.
   - Abre **Show advanced options → Management → Paste cloud-init script**.
3. Abre `oracle_instalar.sh` con el Bloc de notas, escribe en las 2 primeras líneas tu **token** y tu **chat_id**
   (están en `config.json`), copia todo el texto, pégalo en ese cuadro y **cierra el Bloc de notas SIN guardar**.
   ⚠️ Nunca subas a GitHub `config.json`, el `state.json` de la PC ni el instalador con tu token (el repositorio es público).
   Si alguna vez se filtró el token: `/revoke` en @BotFather.
4. **Create**. En 5-15 min te llega "⏳ Bot iniciando [ORACLE]" y el mensaje fijado pasa a
   "🟢 Bot activo [ORACLE] ★".

Desde ese momento Oracle envía todas las señales (1m a 1d) día y noche. Tu PC, si la enciendes, se queda
en espera sola y toma el control si Oracle se cae. Oracle baja el `bot.py` nuevo de GitHub al reiniciarse o con
`/actualizar` (`favoritos.txt` no se actualiza).
Tus amigos deben aprobarse de nuevo en el bot de Oracle (envían /start otra vez).

## BRIDGE · panel flotante encima de TradingView (Windows)

Doble clic en el acceso directo **BRIDGE** del escritorio (o en **`iniciar_panel.bat`**) → aparece el panel **BRIDGE**
(ventana pequeña y moderna de Microsoft Edge en modo aplicación, sin pestañas, siempre encima de TradingView).
Si ya está abierto, solo lo trae al frente (nunca hay dos). Al cerrar la ventana se cierra el panel.

- **Encabezado** ● BRIDGE [PRUEBA/REAL] ⚙ 📌 ⌃: ● verde = llaves OK · 📌 siempre encima · ⌃ lo pliega a una barra.
- **Moneda automática**: la lee del título de la ventana de TradingView (etiqueta **TV** en azul). Toca la moneda para
  escribir otra (deja de seguir TradingView hasta que toques TV).
- **Temporalidades 1m…1D**: verde = LONG para entrar, rojo = SHORT; letra de color tenue = activa pero ya no se puede
  (más de 3 velas o TP1 tocado). Se elige sola la señal más reciente; toca otra para cambiarla.
- **Botón Entrar** (verde LONG / rojo SHORT) = **entrada manual**, cuando TÚ quieres entrar: hoja con E1/E2/E3, SL, TP,
  margen y tu riesgo abierto antes/después → **Confirmar** (90 s) → entra en Bitunix. No entra si el precio salió de la
  zona SL–TP1 o ya recorrió media distancia. Las entradas **automáticas** (sin tocar nada) las hace el bot con `/auto si`.
- **PRUEBA / REAL** (toca la etiqueta): en PRUEBA solo lo anota. REAL pide confirmación y necesita llaves, capital y HEDGE.
- **Posiciones**: total no realizado, riesgo abierto (rojo si pasa tu límite) y cada posición con L/S y apalancamiento,
  PnL en $ y %, temporalidad, TP alcanzados, BE y riesgo; "servidor o tuya" = no la abrió el panel; ✋ soltada → ↩️.
- **Vigilante**: mientras el panel está abierto gestiona lo que abriste desde él (TP, BE, gestión STRIKE, soltar, seguro de
  riesgo, pérdida máxima del día) y te avisa por Telegram con 🖥 BRIDGE. El **SL siempre queda puesto en Bitunix** aunque
  cierres el panel; al abrirlo de nuevo sigue donde quedó. Lo que abre el bot del servidor lo sigue vigilando el servidor.
- **⚙ Ajustes**: capital, riesgo, margen y apalancamiento máximos, pérdida diaria, seguro, gestión bot/STRIKE
  (BE a N R, parcial, compensado) y tus llaves de Bitunix.

Las llaves del panel se guardan en `panel_llaves.json` (solo en tu PC, está en `.gitignore`) y **no** cambian qué equipo
manda en el bot de Telegram. Necesita `bot.py` v6.4 o más en la misma carpeta (el panel lo comprueba al abrir).

## v6.4 · Entrar, posiciones, seguro de riesgo y gestión tipo STRIKE (noviembre 2026)

Funciones inspiradas en STRIKE, hechas sobre el bot (todo desde Telegram, el servidor vigila 24/7):

| Comando / botón | Qué hace | Por defecto |
|---|---|---|
| 🎯 **Entrar en Bitunix** (botón en cada señal, solo administrador) | Entrada manual: lo pulsas → ves el plan con tu capital/riesgo de `/auto` y el riesgo abierto antes y después → **✅ Confirmar** (90 s) → entra en Bitunix con E1/E2/E3, SL y TP, gestionada igual que una automática. No entra si la señal tiene más de 3 velas, si el precio salió de la zona SL–TP1 o si ya recorrió media distancia al TP1. | `/entrar prueba` |
| `/entrar si · prueba · no` (también `/apuntar`) | Real (necesita llaves, capital, riesgo y HEDGE) · solo muestra · sin botón | prueba |
| `/posiciones` | Todas tus posiciones en Bitunix: PnL no realizado, SL, riesgo hasta el SL, quién la gestiona (🤖 auto · 🎯 apuntada · ✋ soltada · 👤 tuya) y el **riesgo abierto total** en % de la cuenta | — |
| `/seguro 5 · si · no` | Aviso si lo que perderías con todos tus SL abiertos pasa ese % de la cuenta, y si una posición tuya no tiene SL | activado, 5 % |
| `/auto gestion strike` | En vez de "TP1 → BE": al llegar a **`/auto berr` R (1) desde el precio medio REAL** → SL a BE, cancela E2/E3 y cierra **`/auto parcial` % (25)** a mercado. Con **`/auto compensado si`**, cuando el parcial se ejecuta el SL baja (sube en SHORT) lo justo para que, si salta, el resultado total quede ≈ 0 con comisiones; nunca pasa del SL original | `/auto gestion bot` |

**✋ Soltar**: si mueves el SL, cierras una parte o cancelas una E2/E3 a mano en Bitunix, el bot deja de tocar esa operación
(no mueve SL, no pone TP, no la cierra por señal contraria) y te avisa con el botón **↩️ Devolver al bot**, que la vuelve a
gestionar adoptando tu SL y la cantidad actual. Si se cierra estando soltada, igual te llega el resultado y se cancelan sus E2/E3.

⚠️ La gestión STRIKE (BE a 1 R + parcial + compensado) **no está en el backtest**: el simulador y el winrate siguen usando la
gestión del bot. Pruébala con `/auto prueba` o margen mínimo antes de usarla en serio. Las operaciones apuntadas cuentan para
`/auto ops`. Pruebas sin red: `python prueba_auto.py`.

## v6.3 · Operaciones automáticas en Bitunix (noviembre 2026)

Cada señal que te llega y pasa el filtro entra sola en Bitunix con **tu** capital y **tus** límites.

**Qué hace el bot en Bitunix**
- Entra con E1 a mercado (o sin E1 en modo límite) y E2/E3 límite, **cada una con su SL en la misma orden**.
- Cada 10 s compara la operación con lo que hay realmente en Bitunix:
  - **SL siempre confirmado**: si una posición queda sin stop, lo pone; si en 45 s no puede confirmarlo,
    **cierra la posición a mercado y apaga el modo real** (y te avisa).
  - **TP parciales 40/30/30** como órdenes límite sobre la cantidad REAL abierta (si entra E2/E3, se rehacen).
  - **TP1 → SL a break even** (entrada +0.08 % por comisiones) y se cancelan las E2/E3 que falten (`/auto be`).
  - Señal contraria → cierra a mercado. Señal cancelada sin llenarse → cancela las entradas.
  - Al cerrar: te manda el resultado REAL de Bitunix (PnL, comisiones, funding) y lo guarda para el reporte semanal.
- Los mensajes de Bitunix dicen **REAL**. Los avisos normales del bot (TP1, cierre, /stats) siguen siendo la simulación
  con velas de Binance: pueden diferir un poco de lo que pasó en Bitunix.

**Filtro y límites** (todo con `/auto`)
| Comando | Qué hace | Por defecto |
|---|---|---|
| `/auto capital 500` · `/auto riesgo 1` | capital que administra y % de riesgo por operación (obligatorios) | — |
| `/auto tf 2h 4h 8h` | temporalidades que entran solas | 15m, 2h, 4h, 8h (factor ≥ 1.5) |
| `/auto ops 1` | máximo de operaciones automáticas a la vez | 1 |
| `/auto max 10` | margen máximo por operación (USDT) | 10 |
| `/auto lev 20` | apalancamiento máximo (0 = el de la señal) | el de la señal |
| `/auto perdida 5` | si la cuenta cae ese % en el día, no abre más hasta mañana | 5 % |
| `/auto be si` · `no` · `15m 4h` | SL a break even tras TP1 | todas |
| `/auto prueba` · `/auto si` · `/auto no` | muestra sin enviar · opera de verdad · apagado | apagado |

No entra si: la señal llegó con retraso, ya tienes posición u órdenes de esa moneda en Bitunix, el precio de Bitunix
difiere más de 3 % del de la señal, o la cantidad queda bajo el mínimo de Bitunix.

**Puesta en marcha (en este orden)**
1. Sube `bot.py` a GitHub y escribe `/actualizar` (Oracle instala la versión nueva).
2. En Bitunix crea una API key **solo con permiso de trading (sin retiros)**. Si te deja, limítala a la IP de Oracle
   (la ves en el servidor con `curl -s ifconfig.me`).
3. Pon las llaves en Oracle (ver abajo). ⚠️ Nunca en `bot.py`, en GitHub ni por Telegram.
4. `/bitunix` → debe decir **HEDGE**. Si no: cierra todo en Bitunix y escribe `/bitunix hedge`.
5. `/auto capital …` y `/auto riesgo …`, luego `/auto prueba` unos días para ver qué haría.
6. `/auto si` con el margen mínimo (`/auto max 10`, `/auto ops 1`) y **mira en Bitunix la primera operación completa**.

**Llaves en Oracle, lo más fácil (v6.4, sin SSH)**: en Telegram escribe `/llaves` y, en los 3 minutos siguientes, envía en un
solo mensaje `APIKEY SECRETKEY`. El bot **borra tu mensaje al instante**, prueba las llaves con Bitunix y las guarda solo en el
equipo que responde (mira el [NOMBRE] en la respuesta: debe ser el servidor). Nunca repite las llaves. Después limita la llave a
la IP que te muestra (Bitunix → API Management → Vincular dirección IP) para que solo funcione desde el servidor.

**Llaves en Oracle por SSH** (alternativa): necesitas entrar al servidor por SSH, con la llave privada (`.key`) que Oracle te dejó descargar al
crear la instancia (si no la guardaste, se puede agregar una nueva desde la consola de Oracle). Una vez dentro, copia
estas dos líneas: te pide las llaves sin mostrarlas en pantalla y reinicia el bot:
```
sudo -u bot python3 -c "import json,getpass;p='/home/bot/app/config.json';c=json.load(open(p));c['bitunix_key']=getpass.getpass('API key: ').strip();c['bitunix_secret']=getpass.getpass('Secret key: ').strip();json.dump(c,open(p,'w'))"
sudo systemctl restart bridge-bot
```
El equipo con llaves manda (🔑 en el mensaje fijado) y es el único que opera. La actualización diaria de Oracle solo
cambia `bot.py`: las llaves se quedan.

## v6.0 · Reglas por temporalidad + mensaje nuevo (noviembre 2026)

Backtest con las 30 monedas de más volumen (datos reales de Binance, comisiones incluidas):

| Temporalidad | Regla nueva | Factor antes → ahora |
|---|---|---|
| 5m | a favor de la Tendencial de 1h y 4h + vela de confirmación | 0.78 → 1.09 (pocas señales, 42 días) |
| 15m | a favor de 4h y 1D + vela de confirmación | 0.93 → 1.47 (caída máxima 75 R → 13 R, 62 días) |
| 1h | a favor de 8h | 0.94 → 1.14 (igual en las dos mitades, 187 días) |
| 4h | solo desde el 2º toque a la Tendencial tras su giro | 1.35 → 2.06 |
| 2h / 8h | sin cambio | 1.90 / 1.66 |
| 1m / 3m | sin cambio: pierden con todo lo probado (0.8) | llevan aviso |

- **Mensaje nuevo**: tabla alineada con E1/E2/E3, SL y TP (precio, cantidad y %), winrate con semáforo 🟢🟡🔴, nº de toque.
- **Indicador v10**: mismas reglas + zonas verde (ganancia) y roja (riesgo) de la operación activa.
- El winrate se recalcula con las reglas nuevas.

## v5.6 · TP3 a 1.5 R y aviso de señal repetida (octubre 2026)

- **TP3 más cerca (1.5 R en vez de 2 R)**: las operaciones se quedaban abiertas días esperando el TP3.
  Backtest 30 monedas 2h+4h+8h: factor 1.74 → 1.80, ganancia 101.6 R → 109.4 R, caída máxima 12 R → 9 R,
  llegan a TP3 el 47 % (antes 39 %) y duran menos (mediana 64 → 54 velas). En 15m también mejora (1.02 → 1.05).
- **🔁 Señal repetida**: si sale otra señal del mismo lado con la operación anterior abierta, el bot avisa y el indicador marca ↻.
  No se abre otra: abrir de nuevo cerrando la anterior se probó y salió peor (factor 1.60-1.64).
- **Probado y descartado**: cerrar el resto por tiempo tras TP1/TP2 (factor 1.48-1.65), SL a la entrada tras TP2 (igual).
- El winrate se recalcula con la regla nueva (las primeras vueltas dicen "calculando historial").

## v5.5 · Entradas más precisas: vela de confirmación en 2h y 4h (octubre 2026)

En 2h y 4h la señal ya no entra al cierre de la vela de la señal: **espera la vela siguiente** y solo entra si esa vela
cierra a favor (cuerpo a favor y cierre más allá del cierre anterior). Mientras espera, el panel del indicador muestra ⏳.

| 2h + 4h + 8h (30 monedas) | Acierto | Factor | Caída máxima | Peor racha | Ganancia total |
|---|---|---|---|---|---|
| v5.4 | 51 % | 1.51 | 27 R | 20 pérdidas | 111 R |
| **v5.5 (confirmación en 2h/4h)** | **54 %** | **1.74** | **12 R** | **9 pérdidas** | 102 R |

- Menos señales y ~9 % menos ganancia total, pero la mitad de caída máxima y rachas mucho más cortas.
- 8h no cambia (ahí la confirmación empeoraba).
- **/confirmar no** vuelve a la entrada inmediata · **/confirmar si** la activa (recomendado).
- **Probado para las entradas y descartado**: entrar solo cerca de la Tendencial, mecha de rechazo, momentum subiendo 2 velas,
  WaveTrend con recorrido, cierre por encima del máximo anterior: igual o peor que la base.

## v5.4 · Más precisión: CALIDAD A y sin fin de semana (octubre 2026)

Backtest con las 30 monedas de más volumen (datos reales de Binance, comisiones incluidas):

| 2h + 4h + 8h | Winrate | Factor | Ganancia total | Monedas en positivo |
|---|---|---|---|---|
| Todas las señales | 37-51 % | 0.75-1.38 | — | — |
| **Calidad A** (Tendencial mayor y BTC a favor) | 49 % | 1.38 | 97.6 R | 22 de 30 |
| **Calidad A sin fin de semana** (v5.4) | 51 % | **1.51** | **111.3 R** | **23 de 30** |

- **Calidad A**: la Tendencial de la temporalidad mayor (2h→8h, 4h→1D, 8h→1D) y la de BTC en esa temporalidad van a favor.
  En 2h/4h/8h solo llegan señales A. En 15m/1h no mejora: ahí solo se muestra la calidad (A/B/C).
- **Sin fin de semana**: en 2h/4h/8h no se abren señales en velas de sábado o domingo (UTC, poca liquidez).
- **/calidad todas** vuelve a enviar todo · **/calidad A** activa el filtro (recomendado).
- **Winrate siempre visible**: cada señal dice el winrate de la temporalidad con todas las monedas y el de esa moneda.
- **1h**: pierde en el backtest con cualquier filtro probado (mejor caso factor 0.96). La señal de 1h trae un aviso; mejor usar 2h/4h/8h.
- **Probado y descartado**: filtrar por el winrate pasado de cada moneda (las que se descartaban ganaban igual o más),
  volumen alto, volatilidad, cerrar por tiempo si no llega a TP1 (mejora unas temporalidades y empeora otras).

## v5.0 · Mensajes más claros + Tendencial sin parpadeo (octubre 2026)

- **Señal nueva**: arriba la dirección en grande (🟢 LONG = COMPRA · 🔴 SHORT = VENTA), moneda, temporalidad, calidad y hora.
  Luego, en bloques: ✅ qué confirmaciones se cumplen · 📍 ENTRADA (E1/E2/E3 con precio, % de distancia, cantidad y % del tamaño,
  precio medio con todo lleno) · 🛑 STOP LOSS con lo que pierdes en $ · ⚡ apalancamiento y margen total · 🎯 TP1-TP3 con
  % de distancia, cuánto cierra cada uno y su R · 🧲 Tendencial de una temporalidad mayor que puede frenar el precio ·
  📈 Tendencial de todas las temporalidades (cuántas a favor) · winrate · confluencia · operaciones abiertas · 📋 qué hacer.
- **Confluencia**: dice claramente **LONG (COMPRA)** o **SHORT (VENTA)**, qué significa, el plan de la señal más reciente
  (con cantidades para TU capital) y una línea por cada temporalidad (calidad, entrada, estado y winrate).
- Todos los avisos (orden llenada, TP1, SL, cierre, debilidad, cancelada) llevan moneda · temporalidad · dirección arriba;
  el cierre dice GANADA o PERDIDA, el motivo y el precio de salida.
- **Indicador v8.7**: la Tendencial ya no parpadea en la vela en curso (se dibuja la de la última vela cerrada; si está a punto de
  girar aparece "?" y el aviso ⏳). La tabla MTF muestra la dirección de la última vela **cerrada** de cada temporalidad.
- **¿La Tendencial cambia muy rápido?** Medido en 30 monedas: en velas cerradas gira ~1.3 veces cada 100 velas, dura de mediana
  ~55-60 velas y solo ~1 % de los giros se deshace en 5 velas o menos. Lo que confundía era (1) el parpadeo dentro de la vela en
  curso (arreglado) y (2) que en 1m-5m "60 velas" son solo 1-5 horas.
- **Ideas de otros indicadores probadas (30 monedas, con comisiones)**: "cierre en margen" (la vela debe cerrar dentro de la zona),
  EMA 55 en vez de 200, WaveTrend ±53/±60, zona de toque 1.0/1.5/2.0 ATR. Ninguna mejora en todas las temporalidades
  (por ejemplo, cierre en margen sube 8h 1.81 → 2.05 pero baja 1h 0.96 → 0.84). Se mantiene la configuración actual.

## v4.9.4 · Auditoría completa (octubre 2026)

- **Menos memoria y menos peticiones a Binance** en el servidor (los picos de las 00:00 UTC y cada 4 h pasaban el límite
  y el bot tenía que usar datos de respaldo).
- Las **confluencias y giros de BTC/ETH** que se forman durante un reinicio corto (`/actualizar`) ya no se pierden.
- `/winrate`, `/radar`, `/stats` y `/estado` largos ya no se quedan sin respuesta (se cortaban en medio del formato).
- Los usuarios **pausados** (sin temporalidades) ya no reciben confluencias ni avisos de BTC/ETH.
- `/maxops` del administrador ya no se aplica a los amigos. `/stats nan` ya no rompe nada.
- `favoritos.txt`: los comentarios con `#` ya no se convierten en monedas falsas.
- `/nube` y el mensaje de inicio muestran la **versión** que corre.
- **Indicador v8.6**: el filtro de 15m usa todo el historial (antes las señales de 1m-5m solo salían en los últimos 3 días
  y el historial/winrate del panel era muy corto), "TF en contra" solo con la vela mayor **cerrada** (no avisa por un giro
  que se deshace), confluencia sin 1m y sin repetir antes de 4 h (igual que el bot), avisos de **TP1/TP2/SL y E2/E3
  llenada al momento** (antes al cierre de la vela: hasta 4 h tarde en 4h), "Debilidad" solo con posición abierta,
  y el apalancamiento máximo mostrado en oro/acciones es 10x.

## v4.9 · Confluencias 🔥 y BTC/ETH de cerca (octubre 2026)

- **🔥 CONFLUENCIA**: aviso cuando una moneda favorita tiene **LONG (o SHORT) activo en 3 o más temporalidades**
  a la vez (de 3m, 5m, 15m, 1h, 2h, 4h, 8h y 1d). Trae la última señal (entrada, SL, TP, apalancamiento),
  cuántas Tendenciales van a favor, el winrate de cada temporalidad y cómo va BTC.
  - Solo avisa si la temporalidad que se suma trae una señal **reciente** (máx. 3 velas) y no repite el mismo aviso antes de 4 h
    (salvo que se sumen más temporalidades).
  - Backtest 50 monedas con comisiones: las señales que salen con 2+ temporalidades más ya activas del mismo lado
    ganaron más que el total: **factor 1.22 vs 0.94** (47 días, 15m-1d) y **1.10 vs 1.03** (174 días, 1h-1d).
    Que la Tendencial esté a favor en muchas temporalidades, por sí solo, casi no cambia el resultado.
- **₿ BTC y ETH de cerca**: se revisan siempre en todas las temporalidades; aviso de confluencia **desde 2** temporalidades
  (contexto: con 2 el backtest no mostró ventaja clara) y aviso cuando su **Tendencial de 1h, 4h o 1d cambia** de dirección.
  `/btc` muestra su estado completo.
- Cada señal normal dice si hay **confluencia** (en qué otras temporalidades está activa). La línea de BTC se quitó en v4.9.2
  (probado: filtrar las altcoins según BTC no mejoró el resultado).
- En `/config` hay dos botones nuevos: **🔥 Confluencias** y **₿ BTC/ETH de cerca** (vienen activados).
- Variables opcionales: `CONF_TFS` (temporalidades), `CONF_MIN` (mínimo, 3).
- **Indicador v8.5**: la misma confluencia en TradingView: alerta 🔥, pie de la tabla ("🔥 3 TF LONG") y fila AVISO.

### Probado y descartado: salidas y filtros (50 monedas, con comisiones)

| Idea | Resultado (factor total 0.97 de base) | Decisión |
|---|---|---|
| Cerrar si la Tendencial se gira en contra | empeora: 0.88 (y 0.89 si solo antes de TP1) | no |
| Cerrar si no llega a TP1 en N velas | depende de la temporalidad y no se sostiene: 2h mejoró en 6 meses, pero empeoró en los 6 meses anteriores (1.05 → 0.97) | no |
| No operar con mercado lateral (ADX < 15/20/25) | empeora: 0.95 / 0.90 / 0.90 | no |

Lo que de verdad reduce pérdidas: no operar (o riesgo bajo) en 1m, 3m, 5m y 1d, y priorizar 8h, 2h y 4h y las confluencias 🔥.

## v4.8 · Diario /stats + indicador v8.4 (octubre 2026)

Mejoras que no cambian las señales:

- **📒 /stats (bot)**: cada operación que te llegó y se cerró queda en tu diario.
  Muestra operaciones, acierto, resultado en R y en $ (con tu riesgo), factor, R medio,
  **caída máxima**, rachas, **curva de capital** en una línea, **calendario de 14 días** 🟩🟥
  y el resultado por temporalidad y por moneda. Empieza a llenarse desde esta versión.
  La hora del calendario es la de Brasil (cámbiala con la variable `TZ_HOURS`, p. ej. `2` para España en verano).
- **Indicador v8.4**:
  - **TP MÍN** (línea cian + fila del panel): el precio desde el que cerrar ya es ganancia suficiente
    para tu winrate. Con 60 % de acierto hace falta 0.67 veces la distancia del SL (40/60); mínimo 0.3.
    Al superarlo: **✅ RENTABLE** + alerta.
  - **Fila AVISO**: ⏳ debilidad · ✅ rentable · 🎯 TP cerca · ⚠ temporalidades mayores en contra · ⟲ posible cambio.
  - **Señal anticipada ⟲** (sin operación): WaveTrend cruza en zona extrema + momentum en contra + ADX cayendo.
    No es entrada; sirve para prepararse.
  - **Alertas nuevas**: TP2, TP3, stop loss, cierre en la entrada, señal contraria, TF mayor en contra y TP mínimo superado.

### Probado y descartado (backtest 50 monedas, datos reales de Binance, con comisiones)

| Idea | Resultado | Decisión |
|---|---|---|
| SL según las "vulneraciones" de la Tendencial (70 % promedio + 30 % máximo × 1.5) | empeora 15m, 1h, 2h y 4h; en 8h con 500 días baja el factor 1.42 → 1.35 | no |
| TP en la Tendencial contraria de una temporalidad mayor | solo 2h sube (1.05 → 1.08) y únicamente en el último tercio; en el resto igual o peor | no |
| "Bolas asimétricas" (señal tocando una Tendencial mayor a favor) | 1h y 2h algo mejor (pocas operaciones), 4h y 8h peor | no |

## v4.7 · Nuevas temporalidades 2h, 8h y 1d (octubre 2026)

Actívalas con `/config` (tercera fila de botones). Backtest con 48 monedas de Binance y comisiones
(factor de beneficio: más de 1 = gana · 2h ≈ 1 año · 8h ≈ 16 meses · 1d ≈ 4 años; validado con 20 monedas reservadas):

| TF | Modo | Factor (48 monedas) | Monedas reservadas |
|---|---|---|---|
| **8h** | DCA 20/30/50 | **1.42** (la mejor) | 1.23 |
| **2h** | DCA 20/30/50 | 1.06 | 1.00 |
| 1d | DCA + SL a la entrada tras TP1 | 0.86 (pierde) | 0.80 |

Los amigos nuevos empiezan con 15m, 2h, 4h y 8h. Las operaciones de 8h y 1d duran semanas: el bot recuerda sus
avisos 120 velas (antes 7 días) para no perder el TP o el cierre.

## v4.6 · Auditoría con datos reales y correcciones (octubre 2026)

**Resultados reales (historial del bot, con comisiones):** factor de beneficio 1m 0.93 · 3m 0.72 · 5m 0.73 ·
15m 0.97 · 1h 0.98 · 4h 0.96. Es decir, el sistema está **casi en empate** y en 3m/5m pierde. Opera con riesgo bajo.

**Backtest nuevo (48 monedas, Binance, 15m ≈ 2 meses · 1h ≈ 6 meses · 4h ≈ 8 meses):**
- Filtro "solo a favor de la tendencia de 4h/1h" en 15m y 1h → **empeora** (descartado).
- Filtrar monedas por su winrate pasado → **empeora** (descartado).
- **SL a la entrada tras TP1 + cancelar E2/E3 pendientes** → 15m mejora (factor 0.92 → 1.01 en 48 monedas,
  validado también en 17 monedas que no se usaron para elegirlo); 4h mejora poco; 1h empeora → activado en **15m y 4h**.

**Correcciones:**
- **Dos bots a la vez (PC + Oracle):** el latido va en su propio hilo cada 60 s, se usa el reloj de Telegram
  y Oracle ★ recupera el mensaje fijado si la PC lo tomó. Ya no deberían llegar señales repetidas.
- Señales atrasadas solo si siguen vivas (sin TP ni SL tocados, con el precio de ahora) y sin perseguir el precio.
- El seguimiento al minuto revisa el **SL antes que el TP1** y avisa "⛔ STOP LOSS TOCADO" al momento.
- Telegram: si pide esperar (429) o falla la conexión, reintenta; los avisos de cierre se reintentan.
- El servidor guarda el estado al reiniciarse y no se salta cierres de vela si una revisión dura más de 1 min.
- `/quitar` ya no se deshace al reiniciar; una fuente de datos mala se evita 20 min (antes, para siempre).

**Control de riesgo** (cambiado en v4.9.1: ahora llegan todas por defecto y varias de la misma moneda, avisando
"⚠️ Ya tienes … abierta (en Bitunix se suman)"): con `/maxops 5` limitas tus operaciones abiertas a la vez.

**Oro, plata, petróleo y acciones (XAU, XAG, CL, SPCX, CRCL…):** apalancamiento máximo 10x, sin entradas desde el
viernes 18:00 UTC hasta que reabre el mercado, aviso "⚠️ CIERRE DE FIN DE SEMANA" el viernes a las 17:00 UTC
para operaciones abiertas, y aviso cuando la bolsa de EE. UU. está cerrada (el precio puede saltar en la apertura).

## v4.0 · Novedades

**Compartir con amigos.** Pásales el enlace de tu bot (t.me/TU_BOT). Cuando pulsan INICIAR te llega
"👤 Nuevo usuario" con botones **✅ Aprobar / ❌ Rechazar**. Cada uno recibe las señales en su chat,
con las cantidades calculadas con **su** capital (`/capital 500`, `/riesgo 1`). `/usuarios` muestra la lista,
`/expulsar ID` quita a alguien, y ellos pueden salir con `/stop`.

**Cada usuario elige sus temporalidades** con `/config` (botones ✅/▫️ para 1m, 3m, 5m, 15m, 1h, 4h).
El bot calcula solo las temporalidades que alguien tiene activadas.

**Modo límite en 5m y 4h** (lo único que mejoró en la auditoría): sin entrada a mercado,
órdenes límite en E2 (40 %) y E3 (60 %); en 5m solo a favor de la Tendencial de 15m.
Te avisa al minuto cuando la orden se **llena** 📥, cuando hay que **cancelarla** ❌ (el precio tocó TP1 sin llenarla)
y cuando toca **TP1** ✅. `/limite no` vuelve a la entrada a mercado.

**Debilidad (v4.4):** si una operación que recibiste pierde fuerza (WaveTrend gira en contra en zona extrema)
te llega "⚠️ DEBILIDAD" con el precio: asegura ganancias (cierra una parte o sube el SL a la entrada).

**Avisos que se perdían / llegaban tarde:**
- Ya no llega un "TP1 alcanzado" de una señal que no recibiste: los avisos de seguimiento solo van a quien recibió la señal.
- Si una señal se calculó con algo de retraso pero sigue activa (sin TP), igual se envía con "⏱ Señal de hace N min".
- Con la PC encendida, **la PC envía todo al instante** (3 s después del cierre de cada vela). El mensaje fijado
  🟢 "Bot activo [PC]" lo indica (Oracle muestra ★). GitHub solo avisa cuando la PC está apagada (15m, 1h y 4h, con 5-30 min de
  retraso porque GitHub no es puntual) y ya no repite lo que manda la PC.
- Si Binance limita las consultas, el bot ya no se queda "pegado" en la fuente de respaldo.

## v3.6 · Auditoría (24 monedas, comisiones de Bitunix, contabilidad corregida)

**Error corregido:** la ganancia del TP1/TP2 se multiplicaba por el tamaño FINAL de la posición aunque se
hubiera cobrado cuando solo estaba E1 (20 %). Eso inflaba los resultados. Ahora cada TP cierra un % de lo que
realmente está abierto, y una operación cuenta como **ganada solo si cerró con ganancia neta** (antes: si tocó TP1).
El indicador de TradingView (v7.5) y el bot usan exactamente el mismo cálculo.

Resultado real por temporalidad (configuración actual):

| TF | Winrate real | R por operación | Lectura |
|---|---|---|---|
| 1m | ~20-40 % | negativo | sin ventaja (comisiones) |
| 3m | ~48-58 % | ~0 | inestable |
| 5m | ~45-49 % | +0.04 a +0.09 | ventaja pequeña |
| 15m | ~46-48 % | ~0 | sin ventaja clara |
| 1h | ~48-51 % | ~0 a +0.06 | sin ventaja clara |
| 4h | ~45 % | +0.10 a +0.12 | ventaja moderada |

Probado y descartado (no mejora fuera de muestra): mover SL a breakeven, cancelar DCA tras TP1,
filtros a favor / en contra de temporalidad mayor, y filtrar por el winrate pasado de cada moneda.
Mejor variante encontrada: **entradas solo con orden límite en E2/E3 (40/60)** en 4h (+0.23 R/op) y en 5m
a favor de 15m (+0.20 R/op); no aplicada por elección del usuario.

En cada aviso ahora ves el **winrate real y la ganancia media en R** de esa moneda/temporalidad
(🟢 gana · 🟡 ~0 · 🔴 pierde). Ojo: con pocas operaciones ese dato es sobre todo ruido.

## v3.2 · modo preciso en 3m y 5m (backtest 8 monedas, 2-5 semanas, comisiones de Bitunix)

En 3m y 5m el bot ahora solo avisa si **la Tendencial de 15m y la de 4h van en la misma dirección**,
reparte la entrada **10 / 30 / 60** (más peso en el mejor precio) y acepta stops desde **0.45 %**.

| R por operación | antes | **modo preciso** |
|---|---|---|
| 3m | +0.25 R (105 ops, acierto 70 %) | **+0.54 R** (70 ops, acierto 71 %) |
| 5m | +0.25 R (140 ops, acierto 71 %) | **+0.46 R** (82 ops, acierto 82 %) |
| 1m | sin ventaja (pierde por comisiones) | avisos apagados (`/1m si` para activarlos) |

Menos señales, pero casi el doble de ganancia media por operación y positivo en 7 de 8 monedas.

## Qué cambió en v3 (backtest 8 monedas, comisiones reales de Bitunix)

- **DCA 20 / 30 / 50** (antes 30/30/40): E3 es el mejor precio, por eso lleva más peso.
- **Comisiones reales**: E1 y stop a mercado (taker 0.06 %), E2/E3 y TP con orden límite (maker 0.02 %).
- **Filtro 4h opcional** (`/h4 si`): en 1m‑5m solo opera a favor de la Tendencial de 4h.

Resultado medio por operación (R = lo que arriesgas), ambos con comisiones de Bitunix:

| | DCA 30/30/40 | **DCA 20/30/50 (v3)** |
|---|---|---|
| 3m | +0.07 R | **+0.19 R** |
| 5m | +0.18 R | **+0.26 R** (acierto 73 %) |
| 15m | +0.17 R | **+0.25 R** (acierto 77 %) |
| 5m con `/h4 si` | — | **+0.36 R** (acierto 81 %, ~60 % de las señales) |

1m sigue sin ser rentable por comisiones: úsalo solo para afinar la entrada de una señal de 5m/15m.
El "SL mínimo 0.6 %" descarta señales cuyo stop es tan corto que la comisión se come la ganancia.
Para que estos números se cumplan, pon **E2, E3 y los TP como órdenes límite**.

## Opción nube (solo 15m o más)
GitHub Actions no puede revisar cada minuto, así que solo sirve para 15m o más.
Sube `bot.py`, `favoritos.txt`, `.gitignore` y `.github/workflows/bot.yml` a un repositorio **público**
(nunca `config.json` ni el `state.json` de la PC), guarda `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` en
*Settings → Secrets and variables → Actions* y ejecuta *Actions → Run workflow*. La nube vigila tus favoritos
(o la variable `SYMBOLS`), solo avisa al administrador, sin confluencias, y puede usar precios de Binance spot.
(Tu chat id aparece en la ventana del bot de PC la primera vez, o en `config.json`.)

## Notas
- Datos: Binance Futuros (perpetuos); si la moneda no está, Binance spot y luego Bitunix (p. ej. OKB).
- Cualquiera que encuentre tu bot puede pedir acceso con /start; tú apruebas o rechazas. Los aprobados usan los
  comandos de usuario; los de administración (/agregar, /top, /usuarios, /actualizar…) solo funcionan desde tu chat.
- Es una herramienta de apoyo, no una garantía ni asesoría financiera. Opera siempre con stop.
