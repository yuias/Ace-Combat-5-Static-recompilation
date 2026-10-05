# Build ac5.exe with Clang (llvm-mingw), without MSYS2.
# Usage (repo root): powershell -File tools/build-clang.ps1 [-BuildDir build/clang] [<extra cmake configure args>]
#
# llvm-mingw and the SDL3 mingw devel package are downloaded into deps/
# (git-ignored) on first run. The Vulkan SDK must be installed separately.

[CmdletBinding()]
param(
  [string]$BuildDir = 'build/clang',
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$CMakeArgs
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$LlvmMingwTag = '20260922'
$SdlVersion = '3.4.18'

$root = Split-Path -Parent $PSScriptRoot
$deps = Join-Path $root 'deps'
$llvm = Join-Path $deps "llvm-mingw-$LlvmMingwTag-ucrt-x86_64"
$sdl = Join-Path $deps "SDL3-$SdlVersion/x86_64-w64-mingw32"

function Get-Zip([string]$url, [string]$check) {
  if (Test-Path $check) { return }
  New-Item -ItemType Directory -Force $deps | Out-Null
  $zip = Join-Path $deps (Split-Path -Leaf $url)
  Write-Host "downloading $url"
  Invoke-WebRequest $url -OutFile $zip
  Expand-Archive $zip -DestinationPath $deps -Force
  Remove-Item $zip
}

Get-Zip "https://github.com/mstorsjo/llvm-mingw/releases/download/$LlvmMingwTag/llvm-mingw-$LlvmMingwTag-ucrt-x86_64.zip" $llvm
Get-Zip "https://github.com/libsdl-org/SDL/releases/download/release-$SdlVersion/SDL3-devel-$SdlVersion-mingw.zip" $sdl

# A terminal opened before the SDK install has no VULKAN_SDK; read the
# machine-wide value instead.
if (-not $env:VULKAN_SDK) {
  $env:VULKAN_SDK = [Environment]::GetEnvironmentVariable('VULKAN_SDK', 'Machine')
}
if (-not $env:VULKAN_SDK) { throw 'VULKAN_SDK is not set; install the Vulkan SDK first.' }

$env:PATH = "$llvm/bin;$env:PATH"
$out = Join-Path $root $BuildDir

cmake -S $root -B $out -G Ninja -DCMAKE_BUILD_TYPE=Release `
  "-DCMAKE_C_COMPILER=$llvm/bin/clang.exe" `
  "-DCMAKE_CXX_COMPILER=$llvm/bin/clang++.exe" `
  "-DSDL3_DIR=$sdl/lib/cmake/SDL3" @CMakeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake --build $out
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Runtime DLLs next to the exe, so it starts from anywhere.
Copy-Item "$sdl/bin/SDL3.dll" $out -Force
Copy-Item "$llvm/x86_64-w64-mingw32/bin/libwinpthread-1.dll" $out -Force
Write-Host "built $out/ac5.exe"
