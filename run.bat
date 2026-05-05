@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 未找到本地虚拟环境: .venv\Scripts\python.exe
    echo 请先安装依赖，或运行原来的依赖安装步骤。
    pause
    exit /b 1
)

echo 正在启动本地网页工作台...
echo 浏览器地址: http://127.0.0.1:8765/
".venv\Scripts\python.exe" "web_server.py"

if errorlevel 1 (
    echo.
    echo 服务异常退出，错误码: %errorlevel%
    pause
)

endlocal
