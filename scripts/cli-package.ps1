# OMNI CLI 打包 / 安装（uv + wheel）
# 用法：
#   .\scripts\cli-package.ps1                       # 构建 wheel 并安装到 <repo>\.venv
#   .\scripts\cli-package.ps1 -NoBuild              # 跳过构建，仅重装已有 wheel
#   .\scripts\cli-package.ps1 -StartBackend         # 安装后用 omni-link 源码启动常驻后端
#   .\scripts\cli-package.ps1 -BackendDir D:\path\python
param(
    [switch]$NoBuild,
    [switch]$StartBackend,
    [string]$BackendDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$VenvPy    = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Omni      = Join-Path $RepoRoot ".venv\Scripts\omni.exe"
$DistDir   = Join-Path $RepoRoot "dist"

if (-not (Test-Path $VenvPy)) { throw "找不到 venv 解释器: $VenvPy（先 npm run python:install 或 python -m venv .venv）" }

function Resolve-BackendDir {
    if ($BackendDir) { return (Resolve-Path -LiteralPath $BackendDir).Path }
    if ($env:OMNI_BACKEND_DIR) { return $env:OMNI_BACKEND_DIR }
    # submodule 布局：<omni-link>/vendor/omni-cli -> <omni-link>/vendor/omni-backend/link
    $sub = Join-Path (Split-Path -Parent (Split-Path -Parent $RepoRoot)) "vendor\omni-backend\link"
    if (Test-Path -LiteralPath (Join-Path $sub "server.py")) { return $sub }
    # 同级仓库布局：<workspace>/omni-cli 与 <workspace>/omni-backend 平级
    foreach ($sibling in @(
        (Join-Path (Split-Path -Parent $RepoRoot) "omni-backend\link"),
        (Join-Path (Split-Path -Parent $RepoRoot) "omni-link\vendor\omni-backend\link"),
        (Join-Path (Split-Path -Parent $RepoRoot) "omni-link\python")
    )) {
        if (Test-Path -LiteralPath (Join-Path $sibling "server.py")) { return $sibling }
    }
    return ""
}

if (-not $NoBuild) {
    # 清理 setuptools 的 build/lib、egg-info 和 __pycache__，避免 wheel 带入 .pyc。
    foreach ($cache in @(
        (Join-Path $RepoRoot "build"),
        (Join-Path $RepoRoot "omni_cli.egg-info"),
        (Join-Path $RepoRoot "__pycache__"),
        (Join-Path $RepoRoot "omni_cli\__pycache__"),
        (Join-Path $RepoRoot "omni_cli\commands\__pycache__"),
        (Join-Path $RepoRoot "omni_cli\config\__pycache__")
    )) {
        if (Test-Path -LiteralPath $cache) { Remove-Item -LiteralPath $cache -Recurse -Force }
    }
    Write-Host "[1/3] uv build --wheel" -ForegroundColor Cyan
    uv build $RepoRoot --wheel --out-dir $DistDir
}

$whl = Get-ChildItem (Join-Path $DistDir "omni_cli-*.whl") -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $whl) { throw "未找到 wheel，请去掉 -NoBuild 先构建一次" }

Write-Host "[2/3] 安装 $($whl.Name) 到 venv" -ForegroundColor Cyan
# --link-mode=copy：避免 uv 缓存与 venv 跨文件系统时的 hardlink 告警
uv pip install --python $VenvPy --reinstall --link-mode=copy $whl.FullName

Write-Host "[3/3] 验证并记录后端位置" -ForegroundColor Cyan
& $Omni --version

# wheel 安装后包不在 omni-link 源码树内，把后端目录写进用户级 cli.config.json，
# 这样后续任意目录直接跑 omni（--managed / backend start）都能找到 server.py，
# 无需每次设置环境变量。仍可用 OMNI_BACKEND_DIR / OMNI_BACKEND_SERVER 覆盖。
$resolved = Resolve-BackendDir
if ($resolved) {
    $saved = & $VenvPy -c "from omni_cli import settings; print(settings.save_backend_dir(r'$resolved'))"
    Write-Host "已记录后端目录: $saved"
} else {
    Write-Warning "未找到 omni-link 后端目录；请用 -BackendDir 或设置 OMNI_BACKEND_DIR，否则调试/烧录命令不可用。"
}

if ($StartBackend) {
    & $Omni backend start
    & $Omni backend status
}

Write-Host ""
Write-Host "完成。常用方式（无需再设环境变量）：" -ForegroundColor Green
Write-Host "  omni backend start          # 启动常驻后端"
Write-Host "  omni probe list             # 自动附加常驻后端"
Write-Host "  omni device summary         # 设备应用库"
Write-Host "  omni config generate --board stm32f407ig   # 本地工程配置（无需后端）"
Write-Host "  omni backend stop"
Write-Host ""
Write-Host "  # 单条命令即用即走（临时拉起后端）"
Write-Host "  omni probe list --managed"
Write-Host ""
Write-Host "  # 附加到已在运行的后端（前端/手动启动）"
Write-Host "  omni probe list --backend-port 57123"
