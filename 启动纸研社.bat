@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [纸研社] 未找到本地虚拟环境: .venv\Scripts\python.exe
    echo 请先在项目目录执行: py -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "main.py"

if errorlevel 1 (
    echo.
    echo [纸研社] 程序异常退出，错误码: %errorlevel%
    pause
)

endlocal