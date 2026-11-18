<#
.SYNOPSIS
    OMNI CLI 一键构建脚本。

.DESCRIPTION
    默认构建单文件 omni.exe；-Installer 额外生成 Windows 安装包（Setup.exe）；
    -Release 构建可分发产物（exe + wheel + portable zip + SHA256）。

    实际工作委托给子脚本，避免重复逻辑：
      scripts/build-exe.ps1        PyInstaller 单文件 exe
      scripts/cli-release.ps1      exe + wheel + zip + SHA256（可签名）
      scripts/make-installer.ps1   NSIS 安装包（可签名）

.PARAMETER Release
    构建发布包（CLI portable zip + wheel），而不是只构建 exe。

.PARAMETER Installer
    生成 Windows 安装包（NSIS Setup.exe）。可与 -Release 组合。

.PARAMETER Exe
    只构建 exe（默认行为，显式指定便于脚本阅读）。

.PARAMETER Plan
    只打印将执行的命令，不实际构建（CI/自检用）。

.PARAMETER Clean
    发布模式：打包前清理输出目录。

.PARAMETER SkipBuild
    跳过 PyInstaller，复用已有 dist/omni.exe。

.PARAMETER SkipWheel
    发布模式：跳过 wheel 构建。

.PARAMETER NoZip
    发布模式：只组装目录，不生成 zip。

.PARAMETER OutputDir
    输出目录（exe 模式默认 dist；发布模式默认 dist/cli-release）。

.PARAMETER Python
    exe 模式：指定解释器。

.PARAMETER NsisPath
    安装包模式：makensis.exe 路径；不填则自动查找（PATH / electron-builder 缓存）。

.PARAMETER SignTool / PfxPath / PfxPassword / CertSha1 / CertSubject / TimestampServer / RequireSigned
    发布/安装包模式：代码签名参数，原样透传。

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Installer
    .\build.ps1 -Release -Installer
    .\build.ps1 -Installer -SkipBuild
    .\build.ps1 -Plan
#>
param(
    [switch]$Release,
    [switch]$Installer,
    [switch]$Exe,
    [switch]$Plan,
    [switch]$Clean,
    [switch]$SkipBuild,
    [switch]$SkipWheel,
    [switch]$NoZip,
    [string]$OutputDir = "",
    [string]$Python = "",
    [string]$NsisPath = "",
    [string]$SignTool = "",
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$CertSha1 = "",
    [string]$CertSubject = "",
    [string]$TimestampServer = "",
    [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"

$ScriptDir      = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExeScript      = Join-Path $ScriptDir "scripts\build-exe.ps1"
$ReleaseScript  = Join-Path $ScriptDir "scripts\cli-release.ps1"
$InstallerScript = Join-Path $ScriptDir "scripts\make-installer.ps1"
$InitFile       = Join-Path $ScriptDir "omni_cli\__init__.py"

if ($Release -and $Exe) {
    Write-Host "ERROR: -Release 与 -Exe 只能选一个。" -ForegroundColor Red
    exit 2
}
if ($Installer -and $Exe) {
    Write-Host "ERROR: -Installer 与 -Exe 只能选一个（-Installer 会先构建 exe）。" -ForegroundColor Red
    exit 2
}
foreach ($script in @($ExeScript, $ReleaseScript, $InstallerScript)) {
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

function Get-SignArguments {
    $sign = @{}
    if ($SignTool) { $sign["SignTool"] = $SignTool }
    if ($PfxPath) { $sign["PfxPath"] = $PfxPath }
    if ($PfxPassword) { $sign["PfxPassword"] = $PfxPassword }
    if ($CertSha1) { $sign["CertSha1"] = $CertSha1 }
    if ($CertSubject) { $sign["CertSubject"] = $CertSubject }
    if ($TimestampServer) { $sign["TimestampServer"] = $TimestampServer }
    if ($RequireSigned) { $sign["RequireSigned"] = $true }
    return $sign
}

function Get-InstallerArguments {
    $arguments = @{ Version = $version }
    if ($OutputDir) { $arguments["OutputDir"] = $OutputDir }
    if ($NsisPath) { $arguments["NsisPath"] = $NsisPath }
    foreach ($key in (Get-SignArguments).Keys) { $arguments[$key] = (Get-SignArguments)[$key] }
    return $arguments
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  OMNI CLI Build (v$version)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

if ($Plan) {
    if ($Release) {
        $releaseArgs = @{}
        if ($Clean) { $releaseArgs["Clean"] = $true }
        if ($SkipBuild) { $releaseArgs["SkipBuild"] = $true }
        if ($SkipWheel) { $releaseArgs["SkipWheel"] = $true }
        if ($NoZip) { $releaseArgs["NoZip"] = $true }
        if ($OutputDir) { $releaseArgs["OutputDir"] = $OutputDir }
        foreach ($key in (Get-SignArguments).Keys) { $releaseArgs[$key] = (Get-SignArguments)[$key] }
        Write-Host "[plan] $(Format-Command $ReleaseScript $releaseArgs)" -ForegroundColor Yellow
    } else {
        if ($SkipBuild) {
            Write-Host "[plan] 跳过 exe 构建（-SkipBuild），使用 dist\omni.exe" -ForegroundColor Yellow
        } else {
            $exeArgs = @{}
            if ($Python) { $exeArgs["Python"] = $Python }
            if ($OutputDir) { $exeArgs["OutputDir"] = $OutputDir }
            Write-Host "[plan] $(Format-Command $ExeScript $exeArgs)" -ForegroundColor Yellow
        }
    }
    if ($Installer) {
        $installerArgs = Get-InstallerArguments
        $installerArgs["Plan"] = $true
        Write-Host "[plan] $(Format-Command $InstallerScript $installerArgs)" -ForegroundColor Yellow
    }
    exit 0
}

if ($Release) {
    $releaseArgs = @{}
    if ($Clean) { $releaseArgs["Clean"] = $true }
    if ($SkipBuild) { $releaseArgs["SkipBuild"] = $true }
    if ($SkipWheel) { $releaseArgs["SkipWheel"] = $true }
    if ($NoZip) { $releaseArgs["NoZip"] = $true }
    if ($OutputDir) { $releaseArgs["OutputDir"] = $OutputDir }
    foreach ($key in (Get-SignArguments).Keys) { $releaseArgs[$key] = (Get-SignArguments)[$key] }

    Write-Host "--- 发布模式：exe + wheel + portable zip ---" -ForegroundColor Yellow
    & $ReleaseScript @releaseArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    if (-not $SkipBuild) {
        $exeArgs = @{}
        if ($Python) { $exeArgs["Python"] = $Python }
        if ($OutputDir) { $exeArgs["OutputDir"] = $OutputDir }
        Write-Host "--- exe 模式：PyInstaller 单文件 ---" -ForegroundColor Yellow
        & $ExeScript @exeArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Write-Host "--- 跳过 exe 构建（-SkipBuild） ---" -ForegroundColor DarkGray
    }
}

if ($Installer) {
    $installerArgs = Get-InstallerArguments
    & $InstallerScript @installerArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not $Release) {
    $distDir = if ($OutputDir) { $OutputDir } else { Join-Path $ScriptDir "dist" }
    $exeName = if ($IsWindows -or $env:OS -eq "Windows_NT") { "omni.exe" } else { "omni" }
    $exePath = Join-Path $distDir $exeName
    if (Test-Path -LiteralPath $exePath) {
        $sizeMB = [math]::Round((Get-Item -LiteralPath $exePath).Length / 1MB, 1)
        Write-Host ""
        Write-Host "产物: $exePath ($sizeMB MB, v$version)" -ForegroundColor Green
        if (-not $Installer) {
            Write-Host "发布包: .\build.ps1 -Release   安装包: .\build.ps1 -Installer" -ForegroundColor DarkGray
        }
    }
}
