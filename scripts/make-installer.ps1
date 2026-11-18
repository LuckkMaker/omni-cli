<#
.SYNOPSIS
    用 NSIS 把 dist/omni.exe 打成 Windows 安装包（按用户安装，无需管理员）。

.DESCRIPTION
    产物：dist\OMNI-CLI-<version>-x64-setup.exe
    安装位置：%LOCALAPPDATA%\Programs\OMNI CLI\bin（与 scripts/cli-install/install.ps1 一致）
    安装时把该目录加入用户 PATH；卸载时移除，并出现在「应用和功能」里。

    makensis 查找顺序：-NsisPath → PATH → electron-builder 缓存（nsis-*）→ Program Files\NSIS。

.PARAMETER Version
    安装包版本；默认从 omni_cli/__init__.py 读取。

.PARAMETER ExePath
    要打包的 omni.exe；默认 dist\omni.exe。

.PARAMETER OutputDir
    输出目录；默认 dist。

.PARAMETER NsisPath
    makensis.exe 路径；不填则自动查找。

.PARAMETER Plan
    只打印将执行的命令。

.PARAMETER SignTool / PfxPath / PfxPassword / CertSha1 / CertSubject / TimestampServer / RequireSigned
    可选：对生成的安装包签名。
#>
param(
    [string]$Version = "",
    [string]$ExePath = "",
    [string]$OutputDir = "",
    [string]$NsisPath = "",
    [switch]$Plan,
    [string]$SignTool = "",
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$CertSha1 = "",
    [string]$CertSubject = "",
    [string]$TimestampServer = "http://timestamp.digicert.com",
    [switch]$RequireSigned
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$DistDir   = if ($OutputDir) { $OutputDir } else { Join-Path $RepoRoot "dist" }
$InitFile  = Join-Path $RepoRoot "omni_cli\__init__.py"
$NsiFile   = Join-Path $ScriptDir "installer.nsi"
$Readme    = Join-Path $RepoRoot "README.md"
$License   = Join-Path $RepoRoot "LICENSE"

if (-not $Version) {
    $init = Get-Content -LiteralPath $InitFile -Raw -Encoding UTF8
    if ($init -match '__version__\s*=\s*"([^"]+)"') { $Version = $Matches[1] }
}
if (-not $Version) { throw "无法读取 omni_cli 版本（可用 -Version 指定）" }

$exe = if ($ExePath) { $ExePath } else { Join-Path $DistDir "omni.exe" }
if (-not (Test-Path -LiteralPath $exe)) {
    throw "找不到 $exe，请先运行 .\build.ps1 或 .\build.ps1 -Release"
}
$exe = (Resolve-Path -LiteralPath $exe).Path
$exeDir = Split-Path -Parent $exe

New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
$outFile = Join-Path $DistDir "OMNI-CLI-$Version-x64-setup.exe"

function Find-Makensis {
    if ($NsisPath) {
        if (-not (Test-Path -LiteralPath $NsisPath)) { throw "找不到 makensis: $NsisPath" }
        return (Resolve-Path -LiteralPath $NsisPath).Path
    }
    $cmd = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $cache = Join-Path $env:LOCALAPPDATA "electron-builder\Cache\nsis"
    if (Test-Path -LiteralPath $cache) {
        $hit = Get-ChildItem -LiteralPath $cache -Recurse -Filter makensis.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\Bin\\makensis\.exe$' } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }

    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles "NSIS\makensis.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    throw "找不到 makensis.exe：请安装 NSIS，或用 -NsisPath 指定（electron-builder 缓存亦可）"
}

$makensis = Find-Makensis
$nsiArgs = @(
    "/V2",
    "/DVERSION=$Version",
    "/DOUTFILE=$outFile",
    "/DBUILD_DIR=$exeDir",
    "/DSCRIPT_DIR=$ScriptDir",
    $NsiFile
)
if (Test-Path -LiteralPath $Readme) { $nsiArgs += "/DREADME_FILE=$Readme" }
if (Test-Path -LiteralPath $License) { $nsiArgs += "/DLICENSE_FILE=$License" }

if ($Plan) {
    Write-Host "[plan] `"$makensis`" $($nsiArgs -join ' ')" -ForegroundColor Yellow
    Write-Host "[plan] 输出: $outFile" -ForegroundColor Yellow
    exit 0
}

Write-Host "--- 生成安装包（NSIS） ---" -ForegroundColor Yellow
Write-Host "[installer] makensis: $makensis"
& $makensis @nsiArgs
if ($LASTEXITCODE -ne 0) { throw "makensis 失败（退出码 $LASTEXITCODE）" }
if (-not (Test-Path -LiteralPath $outFile)) { throw "安装包未生成: $outFile" }

function Find-SignTool {
    if ($SignTool) {
        if (-not (Test-Path -LiteralPath $SignTool)) { throw "找不到 signtool: $SignTool" }
        return (Resolve-Path -LiteralPath $SignTool).Path
    }
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @()
    if (${env:ProgramFiles(x86)}) { $roots += (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin") }
    if ($env:ProgramFiles) { $roots += (Join-Path $env:ProgramFiles "Windows Kits\10\bin") }
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $versions = Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($v in $versions) {
            $candidate = Join-Path $v.FullName "x64\signtool.exe"
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    return $null
}

$signRequested = $PfxPath -or $CertSha1 -or $CertSubject
if ($signRequested) {
    $signtool = Find-SignTool
    if (-not $signtool) { throw "未找到 signtool.exe（可用 -SignTool 指定）" }
    $signArgs = @("sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $TimestampServer)
    if ($PfxPath) {
        $signArgs += @("/f", (Resolve-Path -LiteralPath $PfxPath).Path)
        if ($PfxPassword) { $signArgs += @("/p", $PfxPassword) }
    } elseif ($CertSha1) {
        $signArgs += @("/sha1", $CertSha1)
    } else {
        $signArgs += @("/n", $CertSubject)
    }
    $signArgs += $outFile
    Write-Host "--- 签名安装包 ---" -ForegroundColor Yellow
    & $signtool @signArgs
    if ($LASTEXITCODE -ne 0) { throw "安装包签名失败（退出码 $LASTEXITCODE）" }
} elseif ($RequireSigned) {
    throw "-RequireSigned 需要同时提供 -PfxPath / -CertSha1 / -CertSubject"
}

$sizeMB = [math]::Round((Get-Item -LiteralPath $outFile).Length / 1MB, 1)
Write-Host ""
Write-Host "安装包: $outFile ($sizeMB MB, v$Version)" -ForegroundColor Green
