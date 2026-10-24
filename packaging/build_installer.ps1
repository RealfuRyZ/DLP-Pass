# ============================================================================
# 构建 DLP-Pass NSIS 安装包
#
# 用法（在项目根目录）：
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1 `
#       -AppDir DLP-Pass-Win7 -AppExe DLP-Pass-Win7.exe
#
# 前置条件：
#   1. 已运行 packaging\build_exe.py 或 build_exe_win7.py，
#      dist 中存在对应的目录版产物（AppDir\AppExe）；
#   2. 已安装 NSIS 3（makensis.exe 在 PATH 或常见安装目录）。
# ============================================================================

[CmdletBinding()]
param(
    [string]$AppDir = "DLP-Pass",
    [string]$AppExe = "DLP-Pass.exe",
    [string]$NsisExe = "",
    [string]$OutputFile = ""
)

$ErrorActionPreference = "Stop"

$packagingDir = $PSScriptRoot
$root = Split-Path -Parent $packagingDir

$versionFile = Join-Path $root "dlp_pass\__init__.py"
$versionText = Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8
$versionMatch = [regex]::Match($versionText, '__version__\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "无法从 $versionFile 读取 __version__"
}
$version = $versionMatch.Groups[1].Value

$appDirPath = Join-Path $packagingDir "dist\$AppDir"
$appPath = Join-Path $appDirPath $AppExe
if (-not (Test-Path -LiteralPath $appPath)) {
    throw "找不到目录版主程序：$appPath`n请先运行 packaging\build_exe.py 或 packaging\build_exe_win7.py。"
}

$candidates = @()
if ($NsisExe) { $candidates += $NsisExe }
$command = Get-Command makensis.exe -ErrorAction SilentlyContinue
if ($command) { $candidates += $command.Source }
$candidates += (Join-Path $env:ProgramFiles "NSIS\makensis.exe")
$programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
if ($programFilesX86) {
    $candidates += (Join-Path $programFilesX86 "NSIS\makensis.exe")
}
$candidates += (Join-Path $env:LOCALAPPDATA "Programs\NSIS\makensis.exe")
$candidates += "D:\Program Files (x86)\NSIS\makensis.exe"
$makensis = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $makensis) {
    throw "未找到 makensis.exe，请安装 NSIS 3 或通过 -NsisExe 指定完整路径。"
}

if (-not $OutputFile) {
    $suffix = if ($AppDir -match "Win7") { "-Win7" } else { "" }
    $OutputFile = Join-Path $packagingDir "dist\DLP-Pass-Setup-$version$suffix.exe"
}

Write-Host "版本   : $version"
Write-Host "主程序 : $appPath"
Write-Host "NSIS   : $makensis"
Write-Host "输出   : $OutputFile"
Write-Host ""

Push-Location $packagingDir
try {
    & $makensis /V3 "/DAPP_VERSION=$version" "/DAPP_DIR=$AppDir" "/DAPP_EXE=$AppExe" "/DOUTPUT_FILE=$OutputFile" "installer.nsi"
    if ($LASTEXITCODE -ne 0) {
        throw "makensis 失败，退出码 $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $OutputFile)) {
    throw "安装包未生成：$OutputFile"
}

$hash = (Get-FileHash -LiteralPath $OutputFile -Algorithm SHA256).Hash
$sizeMb = [math]::Round((Get-Item -LiteralPath $OutputFile).Length / 1MB, 1)
Write-Host ""
Write-Host "===== 构建完成 ====="
Write-Host "产物   : $OutputFile"
Write-Host "大小   : $sizeMb MB"
Write-Host "SHA256 : $hash"
