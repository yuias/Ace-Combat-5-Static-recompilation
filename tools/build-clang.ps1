# Build ac5.exe with LLVM clang for x86_64-pc-windows-msvc, without MSYS2.
# Usage (repo root): powershell -File tools/build-clang.ps1 [-BuildDir build/clang] [<extra cmake configure args>]
#
# Needs LLVM clang 19 or newer on PATH (GNU-style clang driver, not clang-cl),
# Visual Studio 2022 or newer (or its Build Tools) with the C++ x64 tools and
# a Windows SDK, and the Vulkan SDK. The script enters the Visual Studio developer
# environment itself. The SDL3 VC devel package and the Microsoft.GameInput
# package are downloaded into deps/ (git-ignored) on first run.

[CmdletBinding()]
param(
  [string]$BuildDir = 'build/clang',
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$CMakeArgs
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$SdlVersion = '3.4.18'
$GameInputVersion = '3.5.283'
# SHA-256 of microsoft.gameinput.<version>.nupkg as api.nuget.org serves it.
$GameInputSha256 = 'b5988cb8ff9d7009b6ddf6ad4e3ff87e91b00cc17ffcd5d628dabc21c208f100'

$root = Split-Path -Parent $PSScriptRoot
$deps = Join-Path $root 'deps'
# Own folder: the mingw and VC zips both unpack to SDL3-<version>/.
$sdlDeps = Join-Path $deps 'sdl3-vc'
$sdl = Join-Path $sdlDeps "SDL3-$SdlVersion"
$gameInput = Join-Path $deps "gameinput\$GameInputVersion"

function Get-Zip([string]$url, [string]$destDir, [string]$check) {
  if (Test-Path $check) { return }
  New-Item -ItemType Directory -Force $destDir | Out-Null
  $zip = Join-Path $destDir (Split-Path -Leaf $url)
  Write-Host "downloading $url"
  Invoke-WebRequest $url -OutFile $zip
  Expand-Archive $zip -DestinationPath $destDir -Force
  Remove-Item $zip
}

Get-Zip "https://github.com/libsdl-org/SDL/releases/download/release-$SdlVersion/SDL3-devel-$SdlVersion-VC.zip" $sdlDeps "$sdl/cmake/SDL3Config.cmake"

# .NET instead of Get-FileHash: that cmdlet fails to load when PSModulePath
# lists PowerShell 7 module folders ahead of Windows PowerShell's own.
function Get-Sha256([string]$path) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $stream = [System.IO.File]::OpenRead($path)
  try { $bytes = $sha.ComputeHash($stream) } finally { $stream.Dispose(); $sha.Dispose() }
  return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Get-GameInput {
  $marker = Join-Path $gameInput '.verified-sha256'
  if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $GameInputSha256)) { return }
  $gameInputRoot = Join-Path $deps 'gameinput'
  $downloadDir = Join-Path $gameInputRoot 'download'
  New-Item -ItemType Directory -Force $downloadDir | Out-Null
  # A nupkg is a zip; the archive tools of Windows PowerShell 5.1 reject other extensions.
  $zip = Join-Path $downloadDir "microsoft.gameinput.$GameInputVersion.zip"
  $cached = (Test-Path $zip) -and ((Get-Sha256 $zip) -eq $GameInputSha256)
  if (-not $cached) {
    $url = "https://api.nuget.org/v3-flatcontainer/microsoft.gameinput/$GameInputVersion/microsoft.gameinput.$GameInputVersion.nupkg"
    $part = "$zip.part"
    Write-Host "downloading $url"
    Invoke-WebRequest $url -OutFile $part
    $hash = (Get-Sha256 $part)
    if ($hash -ne $GameInputSha256) {
      Remove-Item $part -Force
      throw "GameInput package SHA-256 mismatch: expected $GameInputSha256, got $hash"
    }
    Move-Item $part $zip -Force
  }
  # Expand-Archive mishandles the package's [Content_Types].xml.
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $staging = Join-Path $gameInputRoot '.staging'
  if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $staging)
  foreach ($need in 'native\include\GameInput.h', 'native\lib\x64\GameInput.lib', 'redist\GameInputRedist.msi') {
    if (-not (Test-Path (Join-Path $staging $need))) { throw "the GameInput package has no $need" }
  }
  if (Test-Path $gameInput) { Remove-Item $gameInput -Recurse -Force }
  Move-Item $staging $gameInput
  [System.IO.File]::WriteAllText($marker, $GameInputSha256)
}

Get-GameInput

# A terminal opened before the SDK install has no VULKAN_SDK; read the
# machine-wide value instead.
if (-not $env:VULKAN_SDK) {
  $env:VULKAN_SDK = [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine')
}
if (-not $env:VULKAN_SDK) { throw 'VULKAN_SDK is not set; install the Vulkan SDK first.' }

# Resolve the tools before the developer shell, which puts Visual Studio's own
# clang, cmake and ninja on PATH. clang is resolved to its real directory
# because scoop and similar installers put a shim on PATH.
$clangCmd = Get-Command clang.exe -ErrorAction SilentlyContinue
if (-not $clangCmd) { throw 'clang not found on PATH; install LLVM 19 or newer' }
$clangVersion = (& $clangCmd.Source --version) -join "`n"
if ($clangVersion -notmatch 'clang version (\d+)') { throw "cannot read the clang version from: $clangVersion" }
if ([int]$Matches[1] -lt 19) { throw "clang $($Matches[1]) is too old; install LLVM 19 or newer" }
if ($clangVersion -notmatch 'InstalledDir: (.+)') { throw "cannot find the clang install directory in: $clangVersion" }
$bin = $Matches[1].Trim()
foreach ($exe in 'clang.exe', 'clang++.exe', 'lld-link.exe') {
  if (-not (Test-Path (Join-Path $bin $exe))) { throw "$exe not found in $bin; install LLVM 19 or newer" }
}
$cmake = (Get-Command cmake.exe -ErrorAction SilentlyContinue).Source
$ninja = (Get-Command ninja.exe -ErrorAction SilentlyContinue).Source

# The developer environment provides INCLUDE, LIB, the Windows SDK and mt/rc.
if ($env:VSCMD_VER) {
  $vsPath = $env:VSINSTALLDIR.TrimEnd('\')
} else {
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  if (-not (Test-Path $vswhere)) { throw 'vswhere not found; install Visual Studio 2022 or newer with the C++ x64 tools' }
  # A lone version is a minimum; -latest then picks the newest install.
  $vsQuery = @('-version', '17.0', '-products', '*', '-latest',
               '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64')
  $vsId = (& $vswhere @vsQuery -property instanceId | Select-Object -First 1)
  $vsPath = (& $vswhere @vsQuery -property installationPath | Select-Object -First 1)
  if (-not $vsId) { throw 'Visual Studio 2022 or newer with the C++ tools was not found; install the "Desktop development with C++" workload' }
  # VsDevCmd's own scripts call vswhere by name, which is not on PATH by default.
  $env:PATH = "$(Split-Path -Parent $vswhere);$env:PATH"
  Import-Module (Join-Path $vsPath 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll')
  Enter-VsDevShell $vsId -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
}
if (-not $env:WindowsSdkDir) { throw 'the Visual Studio environment has no Windows SDK; install one with the C++ workload' }
if (-not $cmake) { $cmake = (Get-Command cmake.exe -ErrorAction SilentlyContinue).Source }
if (-not $ninja) { $ninja = (Get-Command ninja.exe -ErrorAction SilentlyContinue).Source }
if (-not $cmake) { throw 'cmake not found on PATH' }
if (-not $ninja) { throw 'ninja not found on PATH' }

Write-Host "clang: $bin\clang.exe ($(($clangVersion -split "`n")[0]))"
Write-Host "Visual Studio: $vsPath"

$out = Join-Path $root $BuildDir

# A build dir configured for llvm-mingw cannot be reused.
$cache = Join-Path $out 'CMakeCache.txt'
if ((Test-Path $cache) -and -not (Select-String -Path $cache -Quiet -Pattern '^CMAKE_C_COMPILER_TARGET:[A-Z]+=x86_64-pc-windows-msvc$')) {
  Write-Host "$BuildDir is reconfigured for the new toolchain"
  Remove-Item $cache -Force
  Remove-Item (Join-Path $out 'CMakeFiles') -Recurse -Force -ErrorAction SilentlyContinue
}

& $cmake -S $root -B $out -G Ninja -DCMAKE_BUILD_TYPE=Release `
  "-DCMAKE_MAKE_PROGRAM=$ninja" `
  "-DCMAKE_C_COMPILER=$bin/clang.exe" `
  "-DCMAKE_CXX_COMPILER=$bin/clang++.exe" `
  -DCMAKE_C_COMPILER_TARGET=x86_64-pc-windows-msvc `
  -DCMAKE_CXX_COMPILER_TARGET=x86_64-pc-windows-msvc `
  "-DSDL3_DIR=$sdl/cmake" `
  "-DPS2_GAMEINPUT_DIR=$($gameInput -replace '\\','/')" @CMakeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $cmake --build $out
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# SDL3.dll next to the exe, so it starts from anywhere; the C runtime is
# linked statically. Remove the DLL an llvm-mingw build left here.
Copy-Item "$sdl/lib/x64/SDL3.dll" $out -Force
Remove-Item (Join-Path $out 'libwinpthread-1.dll') -Force -ErrorAction SilentlyContinue
Write-Host "built $out/ac5.exe"
