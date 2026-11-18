<#
.SYNOPSIS
    OMNI CLI 一键构建脚本。

.DESCRIPTION
    默认一次构建全部产物：单文件 omni.exe、wheel + portable zip（发布包）、
    Windows 安装包（Setup.exe）。

    实际工作委托给子脚本，避免重复逻辑：
      scripts/build-exe.ps1        PyInstaller 单文件 exe
      scripts/cli-release.ps1      exe + wheel + zip + SHA256（可签名）
      scripts/make-installer.ps1   NSIS 安装包（可签名）

.PARAMETER Exe
    只构建单文件 exe，不生成发布包与安装包。

.PARAMETER NoRelease
    跳过发布包（wheel + portable zip），仍生成 exe 与安装包。

.PARAMETER NoInstaller
    跳过 Windows 安装包（NSIS Setup.exe）。

.PARAMETER Plan
    只打印将执行的命令，不实际构建（CI/自检用）。

.PARAMETER Clean
    打包前清理发布输出目录。

.PARAMETER SkipBuild
    跳过 PyInstaller，复用已有 dist/omni.exe。

.PARAMETER SkipWheel
    跳过 wheel 构建。

.PARAMETER NoZip
    只组装发布目录，不生成 portable zip。

.PARAMETER OutputDir
    输出目录（exe 默认 dist；发布包默认 dist/cli-release）。

.PARAMETER Python
    exe 模式：指定解释器。

.PARAMETER NsisPath
    安装包模式：makensis.exe 路径；不填则自动查找（PATH / electron-builder 缓存）。

.PARAMETER SignTool / PfxPath / PfxPassword / CertSha1 / CertSubject / TimestampServer / RequireSigned
    发布/安装包模式：代码签名参数，原样透传。

.EXAMPLE
    .\build.ps1                  # 默认：exe + wheel + portable zip + Setup.exe
    .\build.ps1 -Exe             # 只构建 dist\omni.exe
    .\build.ps1 -NoInstaller     # exe + 发布包（未装 NSIS 时）
    .\build.ps1 -Plan
#>
param(
    [switch]$Exe,
    [switch]$NoRelease,
    [switch]$NoInstaller,
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

$buildRelease   = -not ($Exe -or $NoRelease)
$buildInstaller = -not ($Exe -or $NoInstaller)

if ($Exe -and ($NoRelease -or $NoInstaller)) {
    Write-Host "ERROR: -Exe 不能与 -NoRelease / -NoInstaller 组合。" -ForegroundColor Red
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
    if ($buildRelease) {
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
    if ($buildInstaller) {
        $installerArgs = Get-InstallerArguments
        $installerArgs["Plan"] = $true
        Write-Host "[plan] $(Format-Command $InstallerScript $installerArgs)" -ForegroundColor Yellow
    }
    exit 0
}

if ($buildRelease) {
    $releaseArgs = @{}
    if ($Clean) { $releaseArgs["Clean"] = $true }
    if ($SkipBuild) { $releaseArgs["SkipBuild"] = $true }
    if ($SkipWheel) { $releaseArgs["SkipWheel"] = $true }
    if ($NoZip) { $releaseArgs["NoZip"] = $true }
    if ($OutputDir) { $releaseArgs["OutputDir"] = $OutputDir }
    foreach ($key in (Get-SignArguments).Keys) { $releaseArgs[$key] = (Get-SignArguments)[$key] }

    Write-Host "--- 发布包：exe + wheel + portable zip ---" -ForegroundColor Yellow
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

if ($buildInstaller) {
    $installerArgs = Get-InstallerArguments
    Write-Host "--- 安装包：NSIS Setup.exe ---" -ForegroundColor Yellow
    & $InstallerScript @installerArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# 汇总本次产物（只列出实际存在的文件）。
$defaultDist = Join-Path $ScriptDir "dist"
$exeDir = if ($buildRelease -or -not $OutputDir) { $defaultDist } else { $OutputDir }
$releaseDir = if ($OutputDir) { $OutputDir } else { Join-Path $defaultDist "cli-release" }
$setupDir = if ($OutputDir) { $OutputDir } else { $defaultDist }
$exeName = if ($IsWindows -or $env:OS -eq "Windows_NT") { "omni.exe" } else { "omni" }
$artifacts = @()
if ($buildRelease -or -not $SkipBuild) { $artifacts += Join-Path $exeDir $exeName }
if ($buildRelease) { $artifacts += Join-Path $releaseDir "omni-cli-$version-win64.zip" }
if ($buildInstaller) { $artifacts += Join-Path $setupDir "OMNI-CLI-$version-x64-setup.exe" }
$artifacts += @(Get-ChildItem -LiteralPath $defaultDist -Filter "omni_cli-$version-*.whl" -File -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName })

$existing = @($artifacts | Where-Object { Test-Path -LiteralPath $_ })
if ($existing.Count -gt 0) {
    Write-Host ""
    foreach ($path in $existing) {
        $sizeMB = [math]::Round((Get-Item -LiteralPath $path).Length / 1MB, 1)
        Write-Host "产物: $path ($sizeMB MB, v$version)" -ForegroundColor Green
    }
}
