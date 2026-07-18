param(
    [string]$CompilerPath = "",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if (-not $Version) {
    $config = Get-Content -LiteralPath (Join-Path $projectRoot "mangax\core\config.py") -Raw
    $match = [regex]::Match($config, 'APP_VERSION\s*=\s*"v(?<version>\d+\.\d+\.\d+)"')
    if (-not $match.Success) {
        throw "MangaX surumu config.py dosyasindan okunamadi."
    }
    $Version = $match.Groups["version"].Value
}

$readerExe = Join-Path $projectRoot "dist\MangaX-Reader\MangaX-Reader.exe"
if (-not (Test-Path -LiteralPath $readerExe)) {
    throw "Reader paketi bulunamadi. Once scripts\windows\build_reader.bat calistirilmali."
}

if (-not $CompilerPath) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
        "C:\Program Files\Inno Setup 7\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "C:\tmp\InnoSetup7\ISCC.exe"
    )
    $CompilerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

if (-not $CompilerPath -or -not (Test-Path -LiteralPath $CompilerPath)) {
    throw "Inno Setup derleyicisi bulunamadi. Inno Setup 6 veya 7 kurulumu gerekli."
}

$definition = Join-Path $projectRoot "packaging\windows\mangax_reader_installer.iss"
& $CompilerPath "/DAppVersion=$Version" $definition
if ($LASTEXITCODE -ne 0) {
    throw "Reader kurulum paketi derlenemedi (kod: $LASTEXITCODE)."
}

$installer = Join-Path $projectRoot "dist\installers\MangaX-Reader-Setup-v$Version.exe"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Beklenen kurulum dosyasi olusmadi: $installer"
}

Get-Item -LiteralPath $installer
