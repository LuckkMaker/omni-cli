param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("add", "remove")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$Dir
)

$ErrorActionPreference = "Stop"

$target = [System.IO.Path]::GetFullPath($Dir).TrimEnd("\")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $userPath) { $userPath = "" }

$entries = @(
    $userPath -split ";" |
        Where-Object { $_ -and $_.Trim() } |
        Where-Object { $_.TrimEnd("\") -ine $target }
)
if ($Action -eq "add") { $entries += $target }

[Environment]::SetEnvironmentVariable("Path", ($entries -join ";"), "User")
exit 0
