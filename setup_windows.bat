@echo off
REM Windows 直播源管理工具 - 快速启动脚本
REM 版本: 1.0
REM 功能: 启动 PowerShell 安装脚本

echo ================================================
echo  直播源管理工具 - Windows 安装启动器
echo ================================================
echo.

REM 检查是否以管理员权限运行
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [INFO] 管理员权限已获取
) else (
    echo [WARN] 建议以管理员权限运行此脚本
    echo        右键点击此文件，选择"以管理员身份运行"
    echo.
    pause
)

REM 检查 PowerShell 是否可用
powershell -Command "Get-Host" >nul 2>&1
if %errorLevel% == 0 (
    echo [INFO] 启动 PowerShell 安装脚本...
    echo.
    powershell -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
) else (
    echo [ERROR] PowerShell 不可用，请安装 PowerShell 或手动运行
    pause
    exit /b 1
)

echo.
echo 安装脚本执行完成
pause
