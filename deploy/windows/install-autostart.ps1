# ============================================================
#  Live Source Manager Web - Windows 开机自启注册脚本
#  用法:
#    powershell -ExecutionPolicy Bypass -File install-autostart.ps1 [-ProjectDir <路径>]
#  功能:
#    创建任务计划程序任务 LiveSourceManagerWeb，开机(或登录)自动
#    启动 Web 服务。以管理员运行时创建 SYSTEM 级开机启动任务；
#    非管理员时降级为"登录时"自启任务。
#  配合 deploy/windows/start-web.bat 使用。
# ============================================================
param(
    [string]$ProjectDir
)

$ErrorActionPreference = "Stop"

# 解析项目根：默认本脚本位于 <项目根>/deploy/windows/，上两级即项目根
if (-not $ProjectDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectDir = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
}

$BatPath  = Join-Path $ProjectDir 'deploy\windows\start-web.bat'
$TaskName = 'LiveSourceManagerWeb'

function Write-Info { param($m) Write-Host "[INFO] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }

if (-not (Test-Path $BatPath)) {
    Write-Warn "自启包装脚本缺失: $BatPath"
    Write-Warn "请确认 deploy/windows/start-web.bat 存在后重试"
    exit 1
}

# 幂等：已存在则先移除旧任务
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Info "已移除旧任务 $TaskName"
}

$action = New-ScheduledTaskAction -Execute $BatPath
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)   # 0 = 无执行时间限制（7x24 常驻）

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

try {
    if ($isAdmin) {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
        Write-Info "已创建系统级开机自启任务: $TaskName (SYSTEM 账户, 系统启动时运行)"
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
        Write-Warn "当前未以管理员运行，已创建[登录时]自启任务: $TaskName"
        Write-Warn "如需系统级(无用户登录也运行)开机自启，请以管理员身份运行本脚本"
    }
    Write-Info "项目目录 : $ProjectDir"
    Write-Info "启动脚本 : $BatPath"
    Write-Info "管理命令:"
    Write-Host "    立即启动: Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Yellow
    Write-Host "    停止    : Stop-ScheduledTask  -TaskName $TaskName" -ForegroundColor Yellow
    Write-Host "    删除    : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false" -ForegroundColor Yellow
    Write-Host "    运行日志: $ProjectDir\web\data\windows_start.log" -ForegroundColor Yellow
} catch {
    Write-Warn "创建任务失败: $_"
    exit 1
}
