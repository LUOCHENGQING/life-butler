@echo off
chcp 936 >nul
title 小管家 - 启动（含公网访问）
color 0A
cd /d "%~dp0"

cls
echo ==========================================================
echo              小管家 一键启动（含公网穿透）
echo ==========================================================
echo.

if not exist "backend\.env" goto NO_ENV
if not exist "backend\.venv\Scripts\python.exe" goto NO_VENV

REM ---------- 获取局域网 IP ----------
set "LANIP=127.0.0.1"
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=*" %%b in ("%%a") do (
        for /f "tokens=1 delims= " %%c in ("%%b") do set "LANIP=%%c"
    )
)

echo [1/4] 启动后端 API ...
start "小管家-后端" cmd /k "cd /d "%~dp0backend" && ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
timeout /t 6 /nobreak >nul

echo [2/4] 启动电脑端网页 ...
start "小管家-电脑端" cmd /k "cd /d "%~dp0frontend" && npx next dev -p 3000"
timeout /t 5 /nobreak >nul

echo [3/4] 启动手机端网页 ...
start "小管家-手机端" cmd /k "cd /d "%~dp0mobile" && npx next dev -p 3001 -H 0.0.0.0"
timeout /t 8 /nobreak >nul

echo [4/4] 建立公网隧道...
start "小管家-公网隧道" cmd /k ""C:\Program Files\cpolar\cpolar.exe" http 3001 -log=stdout"
timeout /t 15 /nobreak >nul

echo 正在获取公网地址...
cd /d "%~dp0backend"
".venv\Scripts\python.exe" cpolar_url.py
cd /d "%~dp0"
timeout /t 2 /nobreak >nul

start "" "http://localhost:3000"

cls
echo ==========================================================
echo.
echo    全部启动完成
echo.
echo    电脑端网页 : http://localhost:3000
echo    局域网手机 : http://%LANIP%:3001
echo    公网手机   : 见「小管家-公网隧道」窗口里的 https 地址
echo.
echo ----------------------------------------------------------
echo    手机使用方法:
echo      1. 外面用: 打开公网地址（电脑需开机）
echo      2. 家里用: 连同一 WiFi，打开局域网地址
echo      3. 浏览器菜单 - 添加到主屏幕
echo.
echo    注意: 公网地址每次重启会变，以隧道窗口显示为准
echo ----------------------------------------------------------
echo.
echo ==========================================================
echo.
pause
exit /b 0

:NO_ENV
echo [X] 缺少 backend\.env
pause
exit /b 1

:NO_VENV
echo [X] 后端虚拟环境缺失，请先执行:
echo       cd backend
echo       python -m venv .venv
echo       .venv\Scripts\pip install -r requirements.txt
pause
exit /b 1
