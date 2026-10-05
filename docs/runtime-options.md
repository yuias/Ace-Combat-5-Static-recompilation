# Runtime options

Command-line flags and environment variables that `ac5.exe` reads. The basic
launch command is in the README ("Step 5: play"); this page lists the rest.

The runtime reads many more `PS2_*` environment variables than are listed
here, most of them debugging switches. This page covers the ones meant for
users so far.

## Command-line flags

### Running the game

| Flag | Meaning |
| --- | --- |
| `--data DIR` | Folder containing `ps2_image.bin` (`generated` for the US build, `generated/slps-25418` for JP). |
| `--disc PATH` | The game disc: an ISO or a folder with the extracted disc. |
| `--watchdog N` | Give up if no field is delivered for N seconds. `0` never gives up. |
| `--novideo` | Run without a window. |
| `--hidden` | Create the window hidden; keyboard input is ignored. |
| `--no-deinterlace` | Show the interlaced fields as they are. |
| `--verbose` | Log much more. |

### Limiting a run

| Flag | Meaning |
| --- | --- |
| `--seconds S` | Stop after S seconds of wall-clock time. |
| `--vblanks N` | Stop after N vblanks. `0` (default) runs until the window is closed. |
| `--stop-scene M.N[:frames]` | Stop once the game has stayed in scene state M.N for that many frames (default 90). |
| `--autoplay` | Press buttons automatically so the game advances through menus on its own. |

### Debugging and tests

| Flag | Meaning |
| --- | --- |
| `--capture FILE` | Where F6 writes a graphics capture. Default `out/capture.gscap`. |
| `--capture-seconds S`, `--capture-frames N` | Length of a capture. |
| `--capture-level dma\|gs` | `dma` (default) records the whole graphics machine, `gs` only the renderer. Replay with `gsreplay --loop 0 --bench`. |
| `--capture-at FIELD` | Start a capture automatically at that field. |
| `--dump-state FILE` | Write a full state dump to FILE when the run ends. |
| `--profile` | Function-entry histogram. Only in builds with `PS2_DIAG`. |
| `--trace-depth N` | How many recent calls a crash trace shows (default 64). Only in builds with call tracing. |
| `--selftest` | Disc-file (NUFILE) checks against the disc, then exit. |
| `--spu2-test` | SPU2, streamed-audio and sound-RPC checks, then exit. |
| `--hle-test` | IOP heap, pad, CDVD and file-seek contract checks, then exit. Pass `--disc` to include the disc checks. |
| `--gs-test`, `--vif-test`, `--vu-test` | Graphics-unit self-tests, then exit. |
| `--ipu-test FILE.ipu [--ipu-frames N]` | Decode an IPU movie file, then exit. |

## Environment variables

### Audio

Sound goes through WASAPI in shared mode to the Windows default output
device. When the default output changes or the current device goes away, the
runtime moves to the new default; with no output device at all it retries
once a second while the game keeps running.

| Variable | Meaning |
| --- | --- |
| `PS2_NO_AUDIO` | Set to anything to run without opening an audio device. |
| `PS2_AUDIO_WAV=FILE` | Also write the mixed output to FILE as 48 kHz 16-bit stereo WAV. |

### Files

| Variable | Meaning |
| --- | --- |
| `PS2_SAVE_DIR=DIR` | Where memory-card files go. Default: `saves` in the current folder. |
| `PS2_ALLOW_REGION_MISMATCH=1` | Run a disc from the other region anyway. Only useful for debugging. |
