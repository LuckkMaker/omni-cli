<#
.SYNOPSIS
    构建单文件 OMNI CLI 可执行文件（omni.exe）。

.DESCRIPTION
    用 PyInstaller 把 `omni_cli` 打成单文件 console exe，输出到 <repo>\dist\omni.exe。
    omni-link 安装包只消费这个产物（extraResources: resources/python/omni.exe），
    因此本脚本是 CLI 与宿主应用之间唯一的构建契约。

.PARAMETER Python
    指定解释器；默认优先仓库根 .venv，其次 PATH 上的 python。

.PARAMETER OutputDir
    输出目录；默认 <repo>\dist。

.PARAMETER NoVerify
    跳过产物自检（不运行 omni.exe --version）。

.EXAMPLE
    .\scripts\build-exe.ps1
    .\scripts\build-exe.ps1 -Python C:\Python312\python.exe
#>
param(
    [string]$Python = "",
    [string]$OutputDir = "",
    [switch]$NoVerify
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$DistDir   = if ($OutputDir) { $OutputDir } else { Join-Path $RepoRoot "dist" }
$WorkDir   = Join-Path $RepoRoot "build\pyinstaller"
$SpecDir   = Join-Path $RepoRoot "build"
$Entry     = Join-Path $ScriptDir "omni_cli_main.py"
$ExeName   = if ($IsWindows -or $env:OS -eq "Windows_NT") { "omni.exe" } else { "omni" }

function Find-Python {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python)) { throw "找不到 Python: $Python" }
        return $Python
    }
    $venv = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venv) { return $venv }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "找不到 Python。请创建 .venv 或把 python 加入 PATH，或用 -Python 指定。"
}

$py = Find-Python

# 构建依赖：PyInstaller + 运行期依赖（wheel 里由 pyproject 声明，exe 里需显式安装）。
Write-Host "[build-exe] 解释器: $py" -ForegroundColor DarkGray
& $py -c "import PyInstaller, kconfiglib, yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build-exe] 安装构建依赖（pyinstaller / kconfiglib / PyYAML）" -ForegroundColor Yellow
    & $py -m pip install --quiet --disable-pip-version-check pyinstaller "kconfiglib>=14.1.0" "PyYAML>=6.0"
    if ($LASTEXITCODE -ne 0) { throw "构建依赖安装失败" }
}

$version = (& $py -c "import sys; sys.path.insert(0, r'$RepoRoot'); from omni_cli import __version__; print(__version__)" | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "无法读取 omni_cli 版本" }

New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

$buildArgs = @(
    "-m", "PyInstaller",
    "--name", "omni",
    "--noconfirm",
    "--console",
    "--onefile",
    "--clean",
    "--collect-submodules", "omni_cli",
    "--hidden-import", "kconfiglib",
    "--paths", $RepoRoot,
    "--distpath", $DistDir,
    "--workpath", $WorkDir,
    "--specpath", $SpecDir,
    $Entry
)

Write-Host "[build-exe] PyInstaller 构建 omni $version" -ForegroundColor Yellow
& $py @buildArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$exePath = Join-Path $DistDir $ExeName
if (-not (Test-Path -LiteralPath $exePath)) { throw "找不到产物: $exePath" }

if (-not $NoVerify) {
    $out = (& $exePath --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "omni.exe 无法运行: $out" }
    if ($out -notmatch [regex]::Escape($version)) {
        throw "产物版本不一致：$out（期望 $version）"
    }
    Write-Host "[build-exe] 自检通过: $out" -ForegroundColor Green
}

$sizeMB = [math]::Round((Get-Item -LiteralPath $exePath).Length / 1MB, 1)
Write-Host "[build-exe] 产物: $exePath ($sizeMB MB)" -ForegroundColor Green
