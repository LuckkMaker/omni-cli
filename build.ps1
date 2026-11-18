<#
.SYNOPSIS
    OMNI CLI 一键构建脚本。

.DESCRIPTION
    默认构建单文件 omni.exe；-Release 构建可分发产物（exe + wheel + portable zip + SHA256）。

    实际工作委托给两个子脚本，避免重复逻辑：
      scripts/build-exe.ps1      PyInstaller 单文件 exe
      scripts/cli-release.ps1    exe + wheel + zip + SHA256（可签名）

.PARAMETER Release
    构建发布包（CLI portable zip + wheel），而不是只构建 exe。

.PARAMETER Exe
    只构建 exe（默认行为，显式指定便于脚本阅读）。

.PARAMETER Plan
    只打印将执行的命令，不实际构建（CI/自检用）。

.PARAMETER Clean
    发布模式：打包前清理输出目录。

.PARAMETER SkipBuild
    发布模式：跳过 PyInstaller，复用已有 dist/omni.exe。

.PARAMETER SkipWheel
    发布模式：跳过 wheel 构建。

.PARAMETER NoZip
    发布模式：只组装目录，不生成 zip。

.PARAMETER OutputDir
    输出目录（exe 模式默认 dist；发布模式默认 dist/cli-release）。

.PARAMETER Python
    exe 模式：指定解释器。

.PARAMETER SignTool / PfxPath / PfxPassword / CertSha1 / CertSubject / TimestampServer / RequireSigned
    发布模式：代码签名参数，原样透传给 cli-release.ps1。

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Release
    .\build.ps1 -Release -Clean -PfxPath .\certs\omni.pfx
    .\build.ps1 -Plan
#>
param(
    [switch]$Release,
    [switch]$Exe,
    [switch]$Plan,
    [switch]$Clean,
    [switch]$SkipBuild,
    [switch]$SkipWheel,
    [switch]$NoZip,
    [string]$OutputDir = "",
    [string]$Python = "",
    [string]$SignTool = "",
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$CertSha1 = "",
    [string]$CertSubject = "",
    [string]$TimestampServer = "",
    [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"

$ScriptDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExeScript     = Join-Path $ScriptDir "scripts\build-exe.ps1"
$ReleaseScript = Join-Path $ScriptDir "scripts\cli-release.ps1"
$InitFile      = Join-Path $ScriptDir "omni_cli\__init__.py"

if ($Release -and $Exe) {
    Write-Host "ERROR: -Release 与 -Exe 只能选一个。" -ForegroundColor Red
    exit 2
}
foreach ($script in @($ExeScript, $ReleaseScript)) {
    if (-not (Test-Path -LiteralPath $script)) {
        Write-Host "ERROR: 找不到子脚本: $script" -ForegroundColor Red
        exit 1
    }
}

# 版本从包源码读取，避免为了打印版本启动解释器。
$version = $null
if (Test-Path -LiteralPath $InitFile) {
    $init = Get-Content -LiteralPath $InitFile -Raw -Encoding UTF8
    if ($init -match '__version__\s*=\s*"([^"]+)"') { $version = $Matches[1] }
}
if (-not $version) {
    Write-Host "ERROR: 无法从 omni_cli/__init__.py 读取版本。" -ForegroundColor Red
    exit 1
}

function Format-Command([string]$script, [hashtable]$arguments) {
    $parts = @("powershell", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"")
    foreach ($key in ($arguments.Keys | Sort-Object)) {
        $value = $arguments[$key]
        if ($value -is [switch] -or $value -is [bool]) {
            if ($value) { $parts += "-$key" }
        } else {
            $parts += "-$key `"$value`""
        }
    }
    return ($parts -join " ")
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  OMNI CLI Build (v$version)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

if ($Release) {
    $cliArgs = @{}
    if ($Clean) { $cliArgs["Clean"] = $true }
    if ($SkipBuild) { $cliArgs["SkipBuild"] = $true }
    if ($SkipWheel) { $cliArgs["SkipWheel"] = $true }
    if ($NoZip) { $cliArgs["NoZip"] = $true }
    if ($OutputDir) { $cliArgs["OutputDir"] = $OutputDir }
    if ($SignTool) { $cliArgs["SignTool"] = $SignTool }
    if ($PfxPath) { $cliArgs["PfxPath"] = $PfxPath }
    if ($PfxPassword) { $cliArgs["PfxPassword"] = $PfxPassword }
    if ($CertSha1) { $cliArgs["CertSha1"] = $CertSha1 }
    if ($CertSubject) { $cliArgs["CertSubject"] = $CertSubject }
    if ($TimestampServer) { $cliArgs["TimestampServer"] = $TimestampServer }
    if ($RequireSigned) { $cliArgs["RequireSigned"] = $true }

    if ($Plan) {
        Write-Host "[plan] $(Format-Command $ReleaseScript $cliArgs)" -ForegroundColor Yellow
        exit 0
    }

    Write-Host "--- 发布模式：exe + wheel + portable zip ---" -ForegroundColor Yellow
    & $ReleaseScript @cliArgs
    exit $LASTEXITCODE
}

$exeArgs = @{}
if ($Python) { $exeArgs["Python"] = $Python }
if ($OutputDir) { $exeArgs["OutputDir"] = $OutputDir }

if ($Plan) {
    Write-Host "[plan] $(Format-Command $ExeScript $exeArgs)" -ForegroundColor Yellow
    exit 0
}

Write-Host "--- exe 模式：PyInstaller 单文件 ---" -ForegroundColor Yellow
& $ExeScript @exeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: exe 构建失败（退出码 $LASTEXITCODE）。" -ForegroundColor Red
    exit $LASTEXITCODE
}

$distDir = if ($OutputDir) { $OutputDir } else { Join-Path $ScriptDir "dist" }
$exeName = if ($IsWindows -or $env:OS -eq "Windows_NT") { "omni.exe" } else { "omni" }
$exePath = Join-Path $distDir $exeName
if (-not (Test-Path -LiteralPath $exePath)) {
    Write-Host "ERROR: 构建结束但找不到产物: $exePath" -ForegroundColor Red
    exit 1
}
$sizeMB = [math]::Round((Get-Item -LiteralPath $exePath).Length / 1MB, 1)
Write-Host ""
Write-Host "产物: $exePath ($sizeMB MB, v$version)" -ForegroundColor Green
Write-Host "发布包: .\build.ps1 -Release" -ForegroundColor DarkGray
