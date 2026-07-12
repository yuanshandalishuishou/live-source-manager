@echo off
REM ============================================================
REM  Live Source Manager Web - Windows 开机自启包装脚本
REM  本文件为【静态单一来源】：自动定位项目根，无需渲染。
REM  由任务计划程序 (LiveSourceManagerWeb) 调用。
REM  也可双击手动运行以测试启动逻辑。
REM ============================================================
set "SCRIPT_DIR=%~dp0"
REM 项目根 = 本文件所在 deploy\windows 的上两级
pushd "%SCRIPT_DIR%..\.."
set "PROJECT_DIR=%CD%"
popd

set "VENV_PY=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_DIR%\web\data"
set "LOG=%LOG_DIR%\windows_start.log"

if not exist "%VENV_PY%" (
    echo [%date% %time%] [LiveSource] venv 未找到: %VENV_PY% >> "%LOG%"
    exit /b 1
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

cd /d "%PROJECT_DIR%"
echo [%date% %time%] [LiveSource] 启动 Web 服务 (uvicorn 0.0.0.0:23456) >> "%LOG%"
"%VENV_PY%" -m uvicorn web.webapp:app --host 0.0.0.0 --port 23456 >> "%LOG%" 2>&1
echo [%date% %time%] [LiveSource] Web 服务进程退出 (exit=%errorlevel%) >> "%LOG%"
