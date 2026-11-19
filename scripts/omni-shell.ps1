# OMNI CLI Shell
#
# 打开一个已经配置好 omni 的终端（类似 ESP-IDF Command Prompt）。由安装包创建的
# 「OMNI CLI Shell」快捷方式调用；USB 抓包需要管理员权限，右键快捷方式选
# “以管理员身份运行”即可。
#
# 两种部署形态自动识别：
#   - 安装版：<安装目录>\omni-shell.ps1，omni 来自 <安装目录>\bin\omni.exe
#   - 源码版：<仓库>\scripts\omni-shell.ps1，omni 指向 python -m omni_cli

param(
    [switch]$NoTerminal
)

$ErrorActionPreference = "Continue"

# 优先在 Windows Terminal 里打开：它的滚动历史不会被新输出顶走，conhost 会。
if (-not $NoTerminal -and -not $env:WT_SESSION) {
    $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
    if ($wt) {
        try {
            $argLine = '-w 0 new-tab --title "OMNI CLI Shell" -d "' + $PSScriptRoot +
                '" powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File omni-shell.ps1 -NoTerminal'
            Start-Process -FilePath $wt.Source -ArgumentList $argLine
            return
        } catch {
            # 打不开 Windows Terminal 就留在当前控制台
        }
    }
}

# 安装版：脚本在 <安装目录>\，exe 在 <安装目录>\bin\
# 源码版：脚本在 <仓库>\scripts\，包在 <仓库>\omni_cli\
$cliKind = ""
$cliPath = ""
$cliRoot = ""
$Root = ""

foreach ($candidate in @($PSScriptRoot, (Split-Path -Parent $PSScriptRoot))) {
    $exe = Join-Path $candidate "bin\omni.exe"
    if (Test-Path -LiteralPath $exe) {
        $cliKind = "exe"
        $cliPath = $exe
        $Root = $candidate
        break
    }
    if (Test-Path -LiteralPath (Join-Path $candidate "omni_cli\__main__.py")) {
        $cliKind = "source"
        $cliRoot = $candidate
        $Root = $candidate
        break
    }
}

if (-not $cliKind) {
    $found = Get-Command omni.exe -ErrorAction SilentlyContinue
    if ($found) {
        $cliKind = "exe"
        $cliPath = $found.Source
    }
}

if (-not $cliKind) {
    Write-Host "错误: 找不到 omni（既不在脚本旁，也不在 PATH）。" -ForegroundColor Red
    return
}

if ($cliKind -eq "exe") {
    $binDir = Split-Path -Parent $cliPath
    $parts = @($env:PATH -split ';' | Where-Object { $_ })
    if ($parts -notcontains $binDir) {
        $env:PATH = "$binDir;$env:PATH"
    }
} else {
    Set-Location -LiteralPath $cliRoot
    $env:PYTHONPATH = $cliRoot
    function global:omni { & python -m omni_cli @args }
}

try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    $Host.UI.RawUI.WindowTitle = "OMNI CLI Shell"
} catch {
    # 老控制台/重定向下可能不支持，忽略
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$version = ""
try {
    $version = ((& omni --version) 2>$null | Select-Object -First 1)
} catch {
    $version = ""
}

$backend = "未运行"
try {
    $status = & omni backend status bus --json 2>$null | ConvertFrom-Json
    if ($status.running) {
        $backend = "运行中 端口=$($status.port) pid=$($status.pid) 版本=$($status.version)"
        # 抓包权限看的是后端进程，不是这个终端：把后端的提权状态也打出来
        try {
            $usbEnv = & omni usb doctor --json 2>$null | ConvertFrom-Json
            if ($null -ne $usbEnv.elevated) {
                if ($usbEnv.elevated) {
                    $backend += "（后端已提权）"
                } else {
                    $backend += "（后端未提权：capture start 会失败）"
                }
            }
        } catch {
            # 旧后端没有 environment 接口，忽略
        }
    }
} catch {
    # 后端没起或 JSON 解析失败：保持“未运行”
}

$origin = if ($cliKind -eq "exe") { $cliPath } else { "源码：$cliRoot（python -m omni_cli）" }
$adminText = if ($isAdmin) { "是" } else { "否（USB 抓包请右键快捷方式，选「以管理员身份运行」）" }

Write-Host ""
Write-Host "OMNI CLI Shell" -ForegroundColor Cyan -NoNewline
if ($version) { Write-Host "  $version" } else { Write-Host "" }
Write-Host "  CLI     : $origin"
Write-Host "  管理员  : $adminText"
Write-Host "  后端 bus: $backend"
Write-Host ""
Write-Host "  omni --help                    查看命令"
Write-Host "  omni usb capture start --device '\\.\USBPcap1'   抓包（需管理员）"
Write-Host "  omni usb capture transfers --follow              实时看 URB"
Write-Host ""
