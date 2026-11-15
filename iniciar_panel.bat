@echo off
REM ===== BRIDGE - panel flotante para TradingView (Windows) =====
REM Doble clic: abre la ventana BRIDGE encima de TradingView (sin consola).
REM Si no se abre nada, abre una consola en esta carpeta y escribe:  python panel.py   (asi ves el error)
cd /d %~dp0
where pyw >/dev/null 2>/dev/null && (start "" pyw "%~dp0panel.py" & exit /b)
where pythonw >/dev/null 2>/dev/null && (start "" pythonw "%~dp0panel.py" & exit /b)
where py >/dev/null 2>/dev/null && (py "%~dp0panel.py" & exit /b)
python "%~dp0panel.py"
if errorlevel 1 pause
