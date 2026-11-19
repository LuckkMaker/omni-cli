# 开发用：用「源码后端」跑 bus 服务，省去每次打包安装。
#
# 用法（建议在管理员 PowerShell 里执行）：
#   .\scripts\dev-bus.ps1            # 停掉现有后端，改用 omni-backend/bus/server.py
#   .\scripts\dev-bus.ps1 -Status    # 只看当前连的是哪个后端
#
# 说明：
#   - CLI 请用 `python -m omni_cli ...`（源码），改动即时生效，不需要重新打包。
#   - 后端用 omni-backend/bus/server.py（源码），改完后端代码再跑一次本脚本即可。
#   - 只有发布、验证打包产物、或给 GUI 用时，才需要 build.ps1 / bus/build.py。
#   - 重启后端会中断正在进行的抓包（pcap 已落盘，可事后 open 回放）。
param(
    [switch]$Status
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

if ($Status) {
    python -m omni_cli backend status bus
    exit $LASTEXITCODE
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "提示: 当前不是管理员终端；后端能启动，但 usb capture start 会报 not_elevated。" -ForegroundColor Yellow
}

python -m omni_cli backend stop bus --quiet 2>$null | Out-Null
python -m omni_cli backend start bus
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "日常循环：改 CLI 直接跑 python -m omni_cli 命令；改后端再执行本脚本。"
Write-Host "抓包示例：python -m omni_cli usb capture start --device '\\.\USBPcap1'"
Write-Host "          python -m omni_cli usb capture transfers --follow"
