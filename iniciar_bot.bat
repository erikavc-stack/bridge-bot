@echo off
REM ===== Bridge Tactico - bot de Telegram v4.9 =====
REM No hay que editar nada: la primera vez te pide el token de @BotFather
REM y detecta tu chat cuando le envias /start a tu bot (se guarda en config.json).
REM Valores iniciales (despues se cambian desde Telegram con /capital /riesgo):
set CAPITAL=2000
set RISK_PCT=2
set MIN_DIST=0.6
chcp 65001 >nul
title Bridge Tactico - bot de Telegram
cd /d %~dp0
set PY=
python --version >nul 2>nul && set PY=python
if not defined PY py --version >nul 2>nul && set PY=py
if not defined PY (
  echo No encuentro Python. Instalalo desde https://www.python.org/downloads/
  echo y marca la casilla "Add Python to PATH". Luego vuelve a abrir este archivo.
  pause
  exit /b
)
:inicio
REM Trae la ultima version de GitHub (si no hay internet o llega incompleta, sigue con la que tienes)
REM v6.4: si la de GitHub es MAS VIEJA que la de esta carpeta (aun no la subiste), se queda la tuya
powershell -NoProfile -Command "try{iwr -UseBasicParsing https://raw.githubusercontent.com/erikavc-stack/bridge-bot/main/bot.py -OutFile bot_nuevo.py -TimeoutSec 30}catch{}"
if exist bot_nuevo.py %PY% -m py_compile bot_nuevo.py && findstr /c:"def main" bot_nuevo.py >nul && %PY% -c "import re,sys;v=lambda f:tuple(int(x) for x in re.search(r'VERSION = .([0-9.]+).',open(f,encoding='utf-8').read()).group(1).split('.'));sys.exit(v('bot_nuevo.py')<v('bot.py'))" && move /y bot_nuevo.py bot.py >nul
if exist bot_nuevo.py del bot_nuevo.py
%PY% bot.py --loop
echo El bot se detuvo. Reiniciando en 10 segundos... (cierra esta ventana para salir)
timeout /t 10 >nul
goto inicio
