# Build ac5.exe with LLVM clang for x86_64-pc-windows-msvc, without MSYS2.
# Usage (repo root): powershell -File tools/build-clang.ps1 [-BuildDir build/clang] [<extra cmake configure args>]
#
# Needs LLVM clang 19 or newer on PATH (GNU-style clang driver, not clang-cl),
# Visual Studio 2026 (18.x) or its Build Tools with the C++ x64 tools and a
# Windows SDK, and the Vulkan SDK. The script enters the Visual Studio developer
# environment itself. The SDL3 VC devel package is downloaded into deps/
# (git-ignored) on first run.

[CmdletBinding()]
param(
  [string]$BuildDir = 'build/clang',
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$CMakeArgs
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$SdlVersion = '3.4.18'

$root = Split-Path -Parent $PSScriptRoot
$deps = Join-Path $root 'deps'
# Own folder: the mingw and VC zips both unpack to SDL3-<version>/.
$sdlDeps = Join-Path $deps 'sdl3-vc'
$sdl = Join-Path $sdlDeps "SDL3-$SdlVersion"

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
  if (-not (Test-Path $vswhere)) { throw 'vswhere not found; install Visual Studio 2026 with the C++ x64 tools' }
  $vsQuery = @('-version', '[18.0,19.0)', '-products', '*', '-latest',
               '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64')
  $vsId = (& $vswhere @vsQuery -property instanceId | Select-Object -First 1)
  $vsPath = (& $vswhere @vsQuery -property installationPath | Select-Object -First 1)
  if (-not $vsId) { throw 'Visual Studio 2026 (18.x) with the C++ tools was not found; install the "Desktop development with C++" workload' }
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
  "-DSDL3_DIR=$sdl/cmake" @CMakeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $cmake --build $out
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# SDL3.dll next to the exe, so it starts from anywhere; the C runtime is
# linked statically. Remove the DLL an llvm-mingw build left here.
Copy-Item "$sdl/lib/x64/SDL3.dll" $out -Force
Remove-Item (Join-Path $out 'libwinpthread-1.dll') -Force -ErrorAction SilentlyContinue
Write-Host "built $out/ac5.exe"
