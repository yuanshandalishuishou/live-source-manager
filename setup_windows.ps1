# Windows 直播源管理工具安装脚本
# 版本: 2.0 (增强检测和自动安装功能)
# 功能: OS检测、依赖检查、自动安装、数据库初始化、服务启动
# 使用: 右键点击此文件，选择"使用 PowerShell 运行"

# 设置严格模式
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # 加速下载

# 配置变量
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonVersion = "3.13.1"
$PythonInstaller = "python-$PythonVersion-amd64.exe"
$VenvDir = "$ProjectDir\.venv"
$RequirementsFile = "$ProjectDir\requirements.txt"

# 颜色输出函数
function Write-Info { param($msg) Write-Host "[INFO] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Error { param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Write-Debug { param($msg) Write-Host "[DEBUG] $msg" -ForegroundColor Cyan }

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  直播源管理工具 - Windows 安装脚本" -ForegroundColor Cyan
Write-Host "  版本: 2.0" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================================
# 检测函数
# ============================================================================

# 检测 Python 是否已安装（3.13+）
function Test-PythonInstalled {
    $PythonCommands = @("python", "python3", "py")
    
    foreach ($cmd in $PythonCommands) {
        try {
            $version = & $cmd --version 2>$null
            if ($version -match "3\.1[23]") {
                Write-Info "Python 已安装: $version"
                Write-Debug "Python 命令: $cmd"
                return $true
            }
        } catch {}
    }
    
    Write-Warn "Python 3.13+ 未安装"
    return $false
}

# 检测 pip 是否已安装
function Test-PipInstalled {
    param($PythonCmd = "python")
    
    try {
        $version = & $PythonCmd -m pip --version 2>$null
        Write-Info "pip 已安装: $version"
        return $true
    } catch {
        Write-Warn "pip 未安装"
        return $false
    }
}

# 检测虚拟环境是否已创建
function Test-VirtualEnvExists {
    if (Test-Path "$VenvDir\Scripts\Activate.ps1") {
        Write-Info "虚拟环境已存在: $VenvDir"
        return $true
    } else {
        Write-Warn "虚拟环境不存在: $VenvDir"
        return $false
    }
}

# 检测 Python 依赖包是否已安装
function Test-PythonDependencies {
    param($PipCmd = "$VenvDir\Scripts\pip.exe")
    
    if (-not (Test-Path $RequirementsFile)) {
        Write-Warn "requirements.txt 不存在: $RequirementsFile"
        return $false
    }
    
    Write-Info "检查 Python 依赖包..."
    $missing = @()
    
    Get-Content $RequirementsFile | ForEach-Object {
        $line = $_.Trim()
        # 跳过注释和空行
        if ($line -match "^#" -or [string]::IsNullOrWhiteSpace($line)) { return }
        
        # 提取包名
        $pkg = ($line -split "[<=>=!]")[0].Trim()
        
        try {
            & "$VenvDir\Scripts\python.exe" -c "import $pkg" 2>$null
        } catch {
            $missing += $pkg
        }
    }
    
    if ($missing.Count -eq 0) {
        Write-Info "✓ 所有 Python 依赖包已安装"
        return $true
    } else {
        Write-Warn "缺失的依赖包: $($missing -join ', ')"
        return $false
    }
}

# 检测 FFmpeg 是否已安装
function Test-FFmpegInstalled {
    try {
        $ffmpeg = & ffmpeg -version 2>$null | Select-Object -First 1
        Write-Info "FFmpeg 已安装: $ffmpeg"
        return $true
    } catch {
        Write-Warn "FFmpeg 未安装"
        return $false
    }
}

# ============================================================================
# 安装函数
# ============================================================================

# 安装 Python
function Install-Python {
    Write-Info "开始下载 Python $PythonVersion..."
    $InstallerPath = "$env:TEMP\$PythonInstaller"
    
    # 尝试多个镜像源
    $Urls = @(
        "https://mirrors.tuna.tsinghua.edu.cn/python/$PythonVersion/$PythonInstaller",
        "https://mirrors.huaweicloud.com/python/$PythonVersion/$PythonInstaller",
        "https://www.python.org/ftp/python/$PythonVersion/$PythonInstaller"
    )
    
    $Downloaded = $false
    foreach ($Url in $Urls) {
        Write-Info "尝试从 $Url 下载..."
        try {
            Invoke-WebRequest -Uri $Url -OutFile $InstallerPath -TimeoutSec 300
            Write-Info "Python 下载成功"
            $Downloaded = $true
            break
        } catch {
            Write-Warn "下载失败: $_"
        }
    }
    
    if (-not $Downloaded) {
        Write-Error "Python 下载失败，请手动从 https://www.python.org/downloads/ 下载并安装 Python 3.13"
        return $false
    }
    
    Write-Info "开始安装 Python..."
    $args = "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0"
    Start-Process -FilePath $InstallerPath -ArgumentList $args -Wait
    
    # 刷新环境变量
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    
    # 验证安装
    if (Test-PythonInstalled) {
        Write-Info "✓ Python 安装成功"
        return $true
    } else {
        Write-Error "✗ Python 安装失败"
        return $false
    }
}

# 安装 pip
function Install-Pip {
    param($PythonCmd = "python")
    
    Write-Info "安装 pip..."
    
    try {
        & $PythonCmd -m ensurepip
        Write-Info "✓ pip 安装成功 (ensurepip)"
        return $true
    } catch {}
    
    # 尝试使用 get-pip.py
    $GetPipUrl = "https://mirrors.tuna.tsinghua.edu.cn/pypi/get-pip.py"
    $GetPipPath = "$env:TEMP\get-pip.py"
    
    try {
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipPath -TimeoutSec 60
        & $PythonCmd $GetPipPath
        Write-Info "✓ pip 安装成功 (get-pip.py)"
        return $true
    } catch {
        Write-Error "✗ pip 安装失败: $_"
        return $false
    }
}

# 创建虚拟环境
function New-VirtualEnv {
    Write-Info "创建 Python 虚拟环境..."
    Set-Location $ProjectDir
    
    $PythonCmd = "python"
    if (Test-PythonInstalled) {
        $PythonCmd = "python"
    } else {
        Write-Error "Python 未安装，无法创建虚拟环境"
        return $false
    }
    
    & $PythonCmd -m venv $VenvDir
    
    if (Test-VirtualEnvExists) {
        Write-Info "✓ 虚拟环境创建成功: $VenvDir"
        return $true
    } else {
        Write-Error "✗ 虚拟环境创建失败"
        return $false
    }
}

# 安装 Python 依赖
function Install-PythonDependencies {
    param($PipCmd = "$VenvDir\Scripts\pip.exe")
    
    Write-Info "安装 Python 依赖包（使用清华镜像加速）..."
    
    # 配置镜像源
    & $PipCmd config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
    & $PipCmd config set global.trusted-host pypi.tuna.tsinghua.edu.cn
    
    # 安装依赖
    & $PipCmd install -r $RequirementsFile
    
    if ($LASTEXITCODE -eq 0) {
        Write-Info "✓ Python 依赖包安装成功"
        return $true
    } else {
        Write-Warn "清华镜像失败，尝试使用华为云镜像..."
        & $PipCmd config set global.index-url https://repo.huaweicloud.com/repository/pypi/simple
        & $PipCmd config set global.trusted-host repo.huaweicloud.com
        
        & $PipCmd install -r $RequirementsFile
        
        if ($LASTEXITCODE -eq 0) {
            Write-Info "✓ Python 依赖包安装成功（华为云镜像）"
            return $true
        } else {
            Write-Error "✗ Python 依赖包安装失败"
            return $false
        }
    }
}

# 下载并安装 FFmpeg
function Get-FFmpeg {
    Write-Info "下载 FFmpeg (包含 ffmpeg.exe 和 ffprobe.exe)..."
    
    # 创建 tools 目录
    $ToolsDir = "$ProjectDir\tools"
    if (-not (Test-Path $ToolsDir)) {
        New-Item -ItemType Directory -Path $ToolsDir | Out-Null
    }
    
    $FFmpegZip = "$ToolsDir\ffmpeg.zip"
    
    # 尝试多个下载源
    $Urls = @(
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    )
    
    $Downloaded = $false
    foreach ($Url in $Urls) {
        Write-Info "尝试从 $Url 下载..."
        try {
            Invoke-WebRequest -Uri $Url -OutFile $FFmpegZip -TimeoutSec 600
            Write-Info "FFmpeg 下载成功"
            $Downloaded = $true
            break
        } catch {
            Write-Warn "下载失败: $_"
        }
    }
    
    if (-not $Downloaded) {
        Write-Warn "FFmpeg 自动下载失败，请手动下载并解压到 $ToolsDir\ffmpeg\"
        Write-Warn "下载地址: <ADDRESS_REMOVED>
        return $false
    }
    
    # 解压 FFmpeg
    Write-Info "解压 FFmpeg..."
    $TempDir = "$ToolsDir\ffmpeg_temp"
    if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force }
    Expand-Archive -Path $FFmpegZip -DestinationPath $TempDir -Force
    
    # 移动到正确位置
    $ExtractedDir = Get-ChildItem -Path $TempDir -Directory | Select-Object -First 1
    $TargetDir = "$ToolsDir\ffmpeg"
    if (Test-Path $TargetDir) { Remove-Item $TargetDir -Recurse -Force }
    Move-Item "$TempDir\$ExtractedDir" $TargetDir
    Remove-Item $TempDir -Recurse -Force
    Remove-Item $FFmpegZip -Force
    
    # 添加到 PATH
    $FFmpegBin = "$TargetDir\bin"
    if (-not (Test-Path $FFmpegBin)) {
        $FFmpegBin = (Get-ChildItem -Path $TargetDir -Filter "bin" -Directory).FullName
    }
    
    if (-not $FFmpegBin) {
        # 尝试查找 ffmpeg.exe
        $FFmpegExe = Get-ChildItem -Path $TargetDir -Filter "ffmpeg.exe" -Recurse | Select-Object -First 1
        if ($FFmpegExe) {
            $FFmpegBin = $FFmpegExe.DirectoryName
        }
    }
    
    if ($FFmpegBin) {
        $CurrentPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
        if ($CurrentPath -notlike "*$FFmpegBin*") {
            [System.Environment]::SetEnvironmentVariable("Path", "$CurrentPath;$FFmpegBin", "User")
            $env:Path += ";$FFmpegBin"
            Write-Info "FFmpeg 已添加到用户 PATH"
        }
        Write-Info "✓ FFmpeg 安装完成"
        return $true
    } else {
        Write-Error "✗ FFmpeg 安装失败：找不到 ffmpeg.exe"
        return $false
    }
}

# 初始化数据库
function Initialize-Database {
    Write-Info "初始化 SQLite 数据库..."
    Set-Location $ProjectDir
    
    $PythonScript = @"
from web.models import init_db
init_db('admin123')
print('DB_INIT_OK')
"@
    
    $result = & "$VenvDir\Scripts\python.exe" -c $PythonScript 2>&1
    
    if ($result -match "DB_INIT_OK") {
        Write-Info "✓ 数据库初始化成功"
        Write-Info "  默认管理员账号: admin / admin123"
        return $true
    } else {
        Write-Error "✗ 数据库初始化失败: $result"
        return $false
    }
}

# 启动 Web 服务
function Start-WebService {
    Write-Info "启动 Web 管理界面 (端口 23456)..."
    Set-Location $ProjectDir
    
    $Process = Start-Process -FilePath "$VenvDir\Scripts\python.exe" `
        -ArgumentList "-m", "uvicorn", "web.webapp:app", "--host", "0.0.0.0", "--port", "23456" `
        -PassThru `
        -WindowStyle Minimized
    
    Start-Sleep -Seconds 3
    
    # 检查服务是否启动
    try {
        $Response = Invoke-WebRequest -Uri "http://localhost:23456/api/health" -TimeoutSec 5
        Write-Info "✓ Web 服务启动成功!"
        Write-Info "  访问地址: <ADDRESS_REMOVED>
        Write-Info "  进程 ID: $($Process.Id)"
    } catch {
        Write-Warn "Web 服务可能未正常启动，请检查日志"
    }
}

# ============================================================================
# 主安装流程
# ============================================================================

function Main-Install {
    Write-Info "开始检测和安装..."
    
    # 1. 检查/安装 Python
    if (-not (Test-PythonInstalled)) {
        if (-not (Install-Python)) {
            Write-Error "Python 安装失败，请手动安装后重试"
            return
        }
    }
    
    # 2. 检查/安装 pip
    if (-not (Test-PipInstalled)) {
        if (-not (Install-Pip)) {
            Write-Error "pip 安装失败"
            return
        }
    }
    
    # 3. 检查/创建虚拟环境
    if (-not (Test-VirtualEnvExists)) {
        if (-not (New-VirtualEnv)) {
            Write-Error "虚拟环境创建失败"
            return
        }
    }
    
    # 4. 检查/安装 Python 依赖
    if (-not (Test-PythonDependencies)) {
        if (-not (Install-PythonDependencies)) {
            Write-Error "Python 依赖包安装失败"
            return
        }
    }
    
    # 5. 检查/下载 FFmpeg
    if (-not (Test-FFmpegInstalled)) {
        Get-FFmpeg
    } else {
        Write-Info "FFmpeg 已安装"
    }
    
    # 6. 初始化数据库
    if (-not (Initialize-Database)) {
        Write-Error "数据库初始化失败"
        return
    }
    
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Green
    Write-Host "  安装完成!" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Green
    Write-Host ""
    
    # 7. 询问是否启动服务
    $StartService = Read-Host "是否立即启动 Web 服务? (Y/N)"
    if ($StartService -eq "Y" -or $StartService -eq "y") {
        Start-WebService
    }
    
    Write-Host ""
    Write-Info "您可以通过以下命令手动启动服务:"
    Write-Host "  cd $ProjectDir" -ForegroundColor Yellow
    Write-Host "  .venv\Scripts\python.exe -m uvicorn web.webapp:app --host 0.0.0.0 --port 23456" -ForegroundColor Yellow
    Write-Host ""
}

# 执行主安装流程
Main-Install
