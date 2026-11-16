@echo off
REM ===== BRIDGE - panel flotante para TradingView (Windows) =====
REM Doble clic: abre BRIDGE encima de TradingView (sin consola).
REM Si no se abre nada, abre una consola en esta carpeta y escribe:  py panel.py   (asi ves el error)
cd /d "%~dp0"
where pyw >nul 2>&1 && (start "" pyw "%~dp0panel.py" & exit /b)
where pythonw >nul 2>&1 && (start "" pythonw "%~dp0panel.py" & exit /b)
where py >nul 2>&1 && (start "" py "%~dp0panel.py" & exit /b)
echo No encuentro Python. Instalalo desde https://www.python.org/downloads/ marcando "Add Python to PATH".
pause
