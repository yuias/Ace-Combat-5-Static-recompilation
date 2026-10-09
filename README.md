# ps2recomp: Ace Combat 5

A static recompilation of **Ace Combat 5: The Unsung War** (PS2, NTSC-U) for Windows. The recompiler and the build also handle the Japanese release, but that one doesn't play yet, see [Japanese release](#japanese-release-slps-25418).

The game's main CPU code isn't emulated. A Python tool reads the original executable and translates every function into C ahead of time. Clang then compiles that together with a runtime that stands in for the rest of the console: the GS (drawn through Vulkan), the VU vector units, SPU2 audio, the IPU for the movies, the IOP modules, memory cards and controllers. What you get at the end is a normal `ac5.exe`.

The graphics are native too. The 3D (the aircraft, the cockpit, terrain, ground objects, the sky and clouds) doesn't go through an emulated Graphics Synthesizer. The vector programs the game runs on the VU1 to transform and light its models have been rewritten as native code, and the geometry is drawn as real GPU meshes, with vertex shaders, mipmapped textures and anisotropic filtering. The flight HUD, the radar and the radio captions are drawn at your window's resolution, so they stay sharp at any size. Whatever the native renderer doesn't cover yet (the menus, the hangar, some effects) still goes through the emulated GS, into the same frame.

The game is fully playable.

There's no game code or assets in this repo. You bring your own copy of the game and the recompiler builds the C code from it on your machine, which is also why `generated/` is in `.gitignore`.

## What you need

- **The game.** The US release, serial SLUS-20851, as an ISO or as the extracted disc files. The config files in `config/` are tied to addresses in the US executable, so a disc from another region doesn't work with them. The Japanese release (SLPS-25418) has its own config set and its own section further down. It boots and reaches a mission, but it hasn't been played through yet.
- **64-bit Windows** and a GPU with a Vulkan driver. I build and play on Windows 10.
- **LLVM clang** 19 or newer on `PATH`, from the [LLVM installer](https://github.com/llvm/llvm-project/releases), `winget install LLVM.LLVM` or scoop's `llvm`. The build uses the GNU-style `clang` driver, not `clang-cl`. The generated code relies on guaranteed tail calls (`musttail`), which Clang provides.
- **Visual Studio 2022 or newer** (or its Build Tools) with the "Desktop development with C++" workload, for the MSVC x64 libraries and a Windows SDK. You don't need to open a developer prompt: the build script enters the environment of the newest one installed.
- **GameInput**, Microsoft's input API, for controllers. The build script in step 4 downloads Microsoft's `Microsoft.GameInput` NuGet package (the header and a static loader library, SHA-256 checked) into `deps/` (git-ignored) the first time it runs. To use a controller, install the GameInput runtime once by running `deps/gameinput/3.5.283/redist/GameInputRedist.msi`. Without it the game runs keyboard-only and logs `pad: GameInput unavailable: ...`. Don't copy `GameInputRedist.dll` next to `ac5.exe`, that hides Xbox controllers. The build only needs `deps/gameinput`; other `deps/` subfolders from earlier builds can be deleted.
- **CMake** 3.20 or newer, and **Ninja** (`pip install ninja` is the easiest way to get it).
- **The [Vulkan SDK](https://vulkan.lunarg.com)**. The build uses its `glslc` to compile the shaders.
- **Python 3.** Only the standard library is used, there's nothing to pip install. I'm on 3.12.

## Step 1: get the executable off the disc

The recompiler only needs one file from the disc, `SLUS_208.51`, which is the game's main executable. It's in the root of the ISO. Mount the ISO in Explorer (double-click it) or open it with 7-Zip, and copy that file somewhere.

Make sure it's the right one before going any further:

```powershell
Get-FileHash .\SLUS_208.51 -Algorithm SHA256
```

You should get:

```
C3594227605307806592416DBD723BF5771952E279EE281A40DA305426667385
```

The file should also be exactly 3,634,092 bytes. If the hash doesn't match, stop here. You've got a different region or a modified executable, and the recompile won't line up with the config files.

Keep the ISO around. The executable is just the code, the game still loads its models, textures, sound and movies off the disc while you play.

## Step 2: set up a terminal

Everything from here on is PowerShell, run from the root of this repo. If you only just installed the Vulkan SDK, open a new terminal so it picks up the `VULKAN_SDK` variable. The build script falls back to the system-wide value if it's missing.

## Step 3: recompile

```powershell
$env:PYTHONPATH = "tools"
python -m ps2recomp "C:\path\to\SLUS_208.51" -o generated `
    --ida-db config/ida_db.json `
    --ida-seeds config/ida_seeds.json `
    --symbols config/sdk_symbols.json `
    --symbols config/manual_symbols.json `
    --overrides config/overrides.json `
    --hooks config/hooks.json
```

This takes about 15 seconds, and the last line should be:

```
emitted 10742 functions into 43 files in 15.0s
```

(the time will be different for you). If you already have a `generated/` folder from an older checkout, regenerate it, because `ps2_image.c` now records which region the image is from and the runtime checks that. That gives you a `generated/` folder of about 50 MB:

- `ps2_code_0000.c` through `ps2_code_0042.c`: the recompiled functions
- `ps2_func_table.c` and `ps2_funcs.h`: the address to function table the runtime dispatches through
- `ps2_symbols.c`: names for the functions that have one
- `ps2_image.bin`: the executable's memory image, which the runtime loads at startup (`ps2_image.c` just records where it goes and how big it is)

Use all of those flags. There's a shorter version with only `--ida-db` sitting in an error message in `CMakeLists.txt`, don't go by that one. It does produce code that compiles, but it leaves out the overrides and hooks, and the game won't work without them. The overrides replace SDK functions that talk to hardware the PC doesn't have (SIF RPC, the CD drive, the pads and so on) with native handlers in `runtime/src/ps2_hle_*.c`. Recompile those faithfully and the game just sits there waiting on hardware that never answers.

The rest, in short:

- `ida_db.json` and `ida_seeds.json` are function boundaries and entry points exported from IDA. When I tested the recompiler on the SDK sample programs, it found roughly two thirds of the functions on its own and about 97% with the IDA data.
- `sdk_symbols.json` names the PS2 SDK functions linked into the game, found by signature matching. `manual_symbols.json` is the handful the matcher couldn't place.
- `hooks.json` hangs native handlers off a few of the game's own functions (scene changes, file opens, sound loading, the radio queue). They run first, then the original code carries on as normal.

If you want to actually read the output, add `--comments`. Every line then gets the original MIPS instruction written next to it. The files come out about half again as big, but the code is exactly the same.

## Step 4: build

```powershell
powershell -ExecutionPolicy Bypass -File tools/build-clang.ps1
```

On the first run this downloads the GameInput package into `deps/`, then configures and builds into `build/clang`. Extra arguments are passed on to the CMake configure step, for example `-DPS2_DIAG=ON`.

The shaders get compiled into a `shaders` folder right next to `ac5.exe`, and that's where the game looks for them. If you ever move the exe somewhere else, take that folder with it. The C runtime and GameInput's loader are linked into the exe, so no DLL sits next to it (a DLL an older build left next to `ac5.exe` is removed). `vulkan-1.dll` already comes with your graphics driver.

Every generated file is basically one gigantic function, so give it a few minutes. There shouldn't be any warnings.

When it's done you'll have:

- `build/clang/ac5.exe`: the game
- `build/clang/shaders/`: the compiled shaders
- `build/clang/gsreplay.exe`: a dev tool that replays graphics captures, not needed to play

## Step 5: play

Still in the repo root:

```powershell
.\build\clang\ac5.exe --data generated --disc "C:\path\to\Ace Combat 5 - The Unsung War (USA) (En,Ja).iso" --watchdog 0
```

- `--data` is the folder that has `ps2_image.bin` in it, so `generated`.
- `--disc` takes the ISO or a folder with the extracted disc.
- `--watchdog 0` switches the watchdog off. Normally the runtime gives up if the game hasn't delivered a frame in 10 seconds. That's useful when something's hung during debugging, not so much when you're playing.

The window stays black for about 20 seconds before the first picture. That's normal, give it a moment. The log goes to stderr. In PowerShell 7 you can save it by sticking `2> ac5_log.txt` on the end. The older Windows PowerShell 5.1 mangles stderr when you redirect it like that, so if that's what you have, run the same command from `cmd` instead. `--verbose` makes it log a lot more (and it really is a lot).

The other command-line flags and environment variables (sound output, saves, test modes) are listed in [docs/runtime-options.md](docs/runtime-options.md).

You can start `ac5.exe` from outside the terminal too (double-clicking it in Explorer, say), but it still needs `--data` and `--disc`, so a shortcut with those arguments filled in is the easiest way.

Where your stuff goes:

- Saves are written to a `saves` folder inside whatever folder you started the game from. The memory card files get created the first time the game touches them. Set `PS2_SAVE_DIR` if you want them somewhere else. These are this runtime's own format, not PCSX2 memory cards, so you can't bring saves over from an emulator.
- Settings are saved to `ac5_settings.ini` next to `ac5.exe`. The settings menu offers windowed and borderless fullscreen; a settings file from an older build that chose exclusive fullscreen opens borderless.
- Compiled graphics pipelines are cached in `ac5_pipelines.cache` and `ac5_pipelines.keys` next to `ac5.exe`, so a state that was compiled once doesn't cause a stutter the next time it shows up. Deleting them is harmless, they just get rebuilt.

### Controls

Controllers are read through Microsoft GameInput (install its runtime once, see [What you need](#what-you-need)). Pads that GameInput reports as gamepads, such as Xbox controllers and compatible pads, work. Pads it lists only as generic controllers are not used; a DualShock 4 over Bluetooth is one example of a pad that can show up that way. PlayStation pads can still work through Steam Input or a similar tool that presents them as an Xbox controller. Buttons map by position: the bottom face button is Cross, right is Circle, left is Square, top is Triangle. Bumpers are L1/R1, triggers are L2/R2, clicking the sticks gives L3/R3, and Back and Start are Select and Start. Ctrl+C in the console closes the game.

Keyboard defaults:

| PS2 | Keyboard |
| --- | --- |
| D-pad | Arrow keys |
| Cross, Circle, Square, Triangle | X, S, Z, A |
| L1, R1 | Q, E |
| L2, R2 | 1, 3 |
| Left stick | Numpad 8, 4, 2, 6 (W also works for up) |
| Start, Select | Enter, Right Shift |

You can rebind all of it in the settings menu.

Other keys:

- **F4** opens and closes the settings menu
- **F11** or **Alt+Enter** toggles borderless fullscreen
- **Esc** quits, or closes the settings menu if it's open
- **F6 to F10** are debugging hotkeys (captures and state dumps), you can ignore them

## Mods

Mods go in a `mods` folder in whatever folder you start the game from, one folder per mod. Nothing gets repacked or rebuilt. The game reads its files through a layered file system, and a mod is just another layer on top of the disc. That works the same with the ISO and with extracted disc files.

A mod can:

- replace files, including files inside `DATA.PAC`, by dropping them in its `files/` folder under the name the game uses for them
- change the game's named tuning values with a `params.txt`
- apply PCSX2 `.pnach` patches that change data (code patches can't work on recompiled code, and get refused with a message)
- run Lua scripts that hook the game's functions, read and write its memory, react to frames and scene changes, and rewrite the controller input
- run native code from a DLL, but only if its `mod.toml` says `native = true`

`mods/README.md` explains all of it. The Lua API is `runtime/include/ac5mod.h`, and that header stays documented on purpose.

To name files inside `DATA.PAC` you need the name table of your release, which is already in the repo: `config/pac_names.txt` for the US release and `config/slps-25418/pac_names.txt` for the Japanese one. The two archives are laid out differently (965 and 1169 members), so each build loads only its own table. A table that states a member count (`# members:`) different from the disc's is skipped, and the log says so. If you want to rebuild the US table, `python -m modkit.export_names` generates it from `datapack.bin` in the PS4 release. There is no such index for the Japanese release; its table comes from matching its files to the US ones by content (see [Japanese release](#japanese-release-slps-25418)). `python -m modkit extract` pulls files out of the archive under the same names, so you have something to start from; it picks the table from the disc's `SYSTEM.CNF`, or from `--region us|jp`. Both need `PYTHONPATH` pointing at `tools`.

If the game misbehaves, set `PS2_NO_MODS=1` first. That turns the whole mod layer off, and if the problem is still there, it isn't a mod. `PS2_MOD_DIR` loads mods from a different folder. The log ends with a summary of every mod, conflict, hook and patch.

`tests/mods` has the mods I test the mod layer with. `python tools/make_test_mods.py` adds the ones that need files from your own disc, because those can't be in the repo.

## Optional: recompile the VU1 microprograms as well

The PS2's VU1 runs small vector programs that the game uploads to it while it's running. They aren't in the executable as code, so `ps2recomp` never sees them. The ones the native renderer has replaced never run at all, and by default the runtime interprets the rest. You can record the ones the game really uses and compile those to C too, which is faster for whatever the native renderer doesn't cover yet.

1. Record them. Set the census variable and play like normal:

   ```powershell
   $env:PS2_VU_CENSUS = "1"
   .\build\clang\ac5.exe --data generated --disc "C:\path\to\game.iso" --watchdog 0
   ```

   Get through the menus and into a mission, because some of the programs only get uploaded once you're actually flying. A short run that never leaves the menus only records the menu programs. Quit with Esc when you're done, and on the way out it writes `out\vu_programs\` (a `manifest.json` plus a `.bin` for each program).

2. Turn them into C. `PYTHONPATH` still needs to point at `tools` for this:

   ```powershell
   Remove-Item Env:PS2_VU_CENSUS
   python -m vurecomp --programs out/vu_programs -o generated/ps2_vu1_progs.inc
   ```

   `python -m vurecomp --programs out/vu_programs --stats` shows what got recorded, if you're curious.

3. Rebuild. There's a catch here: Ninja won't notice the new file on its own, because `ps2_vu.c` only includes it if it exists and nothing depended on it before. A plain `cmake --build` just says "no work to do". Touch `ps2_vu.c` first so it gets recompiled:

   ```powershell
   (Get-Item runtime\src\ps2_vu.c).LastWriteTime = Get-Date
   cmake --build build/clang
   ```

Next time the game draws something with the VU1, the log should get a line like `vu1: 15 recompiled microprograms available` (15 is what a census through a full mission gave me, yours depends on how far you played). If you ever want to compare against the interpreter, set `PS2_VU_RECOMP=0`.

## Japanese release (SLPS-25418)

The Japanese release has its own config set in `config/slps-25418/`. The runtime's own hooks and patches take their addresses from `runtime/include/ps2_addr_list.h`, which lists the US addresses; `python -m regionaddr generate` translates them for JP into `runtime/include/ps2_addr_jp.inc`, and a JP build checks the expected code at those addresses when it starts. A JP build boots and reaches a mission, but it hasn't been played through yet, so expect rough edges. Note that the JP release confirms with Circle and cancels with Cross.

The executable is `SLPS_254.18` in the root of the disc. The check is the same as in step 1:

```powershell
Get-FileHash .\SLPS_254.18 -Algorithm SHA256
```

```
B510EE45343325BDACF14B81E3A1B34DE103C1F0379BEAA014795A4E401025AD
```

It should be exactly 3,634,988 bytes.

`config/slps-25418/` is committed, so you don't need the US executable to use it. It was generated from the US config files, the US executable and the JP executable: `python -m regionmap build` compares the two executables and writes a map of which US address is which JP address, then `python -m regionconfig translate` uses that map to translate every table in `config/`. Anything that couldn't be carried over is listed with a reason in `config/slps-25418/manifest.json`. `python -m regionconfig check` tells you whether the committed set still matches the US configs. All of these need `PYTHONPATH` pointing at `tools`.

To recompile (this is the US command from step 3 with the JP config files and output folder):

```powershell
$env:PYTHONPATH = "tools"
python -m ps2recomp "C:\path\to\SLPS_254.18" -o generated/slps-25418 `
    --ida-db config/slps-25418/ida_db.json `
    --ida-seeds config/slps-25418/ida_seeds.json `
    --symbols config/slps-25418/sdk_symbols.json `
    --symbols config/slps-25418/manual_symbols.json `
    --overrides config/slps-25418/overrides.json `
    --hooks config/slps-25418/hooks.json
```

It reports `region: SLPS-25418 (jp)` and covers 98.02% of the code, against 98.04% for the US executable. To build it, use a separate build folder so the US build stays as it is:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build-clang.ps1 -BuildDir build/clang-jp "-DPS2_GENERATED_DIR=generated/slps-25418"
```

That gives you `build/clang-jp/ac5.exe`.

Each build knows which region it was made from. A JP build refuses a US disc and a US build refuses a JP disc: the log says `region: ...` and the game exits. Set `PS2_ALLOW_REGION_MISMATCH=1` to run anyway, which is only useful for debugging because the result will not work.

`config/slps-25418/pac_names.txt` names the Japanese `DATA.PAC` files that have a counterpart in the US archive, and mods can use those names like the US ones. The counterparts are found by content: `vfs_test files <disc>` lists every file slot of an archive with its size and hashes, and `python -m modkit.match_names` carries the US names over to the Japanese slots with the same content, falling back to a file of the same size or at the same position in the matching member where the content differs. Files that exist only in the Japanese release have no name and are addressed by position, as `BIN/DATA.PAC/<member>/#<index>`. The header of the table lists how many files each kind of match named.

To regenerate it, build `vfs_test` (`python tools/test_vfs.py` does), list both discs, then match:

```
out/vfs_test/vfs_test.exe files US_DISC > us.txt
out/vfs_test/vfs_test.exe files JP_DISC > jp.txt
python -m modkit.match_names --us-list us.txt --jp-list jp.txt --us-names config/pac_names.txt -o config/slps-25418/pac_names.txt
```

## Regenerating the config files

You don't need to. Everything in `config/` is already generated and committed, so you don't need IDA or the PS2 SDK to build. If you want to redo them anyway:

- `tools/ida/export_db.py` and `tools/ida/export_seeds.py` are IDA scripts that produce `ida_db.json` and `ida_seeds.json`.
- `tools/identify_sdk.py` rebuilds `sdk_symbols.json` by matching the game against the SDK's EE libraries. Point `PS2SDK_DIR` at your SDK and `AC5_DISC` at the extracted disc folder first.

The files in `config/slps-25418/` are made from these by the translation described in the previous section, not by running the IDA scripts again.

## What's in here

- `tools/ps2recomp/`: the recompiler (ELF in, C out)
- `tools/vurecomp/`: the VU1 microprogram recompiler
- `tools/`: test and check scripts
- `runtime/`: everything that stands in for the console, plus the settings menu and the mod layer
- `runtime/src/rn/`: the native renderer
- `runtime/shaders/`: the GLSL shaders, compiled at build time
- `config/`: the IDA export, symbol tables, overrides and hooks (US); `config/slps-25418/` is the same set for the Japanese release
- `tools/regionmap/`, `tools/regionconfig/`: map addresses between the two executables and translate the config set
- `third_party/imgui/`: Dear ImGui, used for the settings menu
- `third_party/lua/`: Lua 5.4.9, used for mod scripts
- `tools/modkit/`: reads `DATA.PAC` by file name
- `mods/`: where mods go, see `mods/README.md`
- `tests/mods/`: the mods the mod layer is tested with
- `generated/`: the recompiler's output, which you create in step 3

## Troubleshooting

- **CMake says `Cannot find source file: .../generated/ps2_func_table.c`.** You haven't done step 3 yet, or the output went somewhere other than `generated`.
- **CMake can't find Vulkan or `glslc`.** The Vulkan SDK isn't installed, or `VULKAN_SDK` isn't set in that terminal.
- **The build script fails while downloading or checking GameInput.** Either the network is down, or the SHA-256 check failed. In the second case delete `deps/gameinput` and run the build script again.
- **The build script says clang or Visual Studio wasn't found.** Check the LLVM and Visual Studio entries under [What you need](#what-you-need). `clang` has to be on `PATH` in the terminal you run the script from, and Visual Studio needs the C++ workload.
- **No controller works and the log says `pad: GameInput unavailable`.** The GameInput runtime isn't installed. Run `deps/gameinput/3.5.283/redist/GameInputRedist.msi` once, then start the game again.
- **The log says `vk: cannot open shader`.** The `shaders` folder isn't next to `ac5.exe` anymore. Put it back, or set `PS2_SHADER_DIR` to wherever the `.spv` files are.
- **The game quits by itself with `==== WATCHDOG: the guest delivered no field for 10 seconds`.** You left out `--watchdog 0`.

## Legal

Ace Combat is a trademark of Bandai Namco Entertainment. This project isn't affiliated with or endorsed by them in any way. No game files are included, and I won't share any, so please don't ask.

The code in this repo is released under the Apache License 2.0, see `LICENSE`. Dear ImGui is MIT licensed and keeps its own license in `third_party/imgui/LICENSE.txt`. Lua is MIT licensed too, see `third_party/lua/LICENSE.html`. The GameInput header and static loader library come from Microsoft's `Microsoft.GameInput` package and are MIT licensed; the loader is linked into `ac5.exe`. The GameInput runtime is under Microsoft's own license terms and is installed by the player, it isn't redistributed here.
