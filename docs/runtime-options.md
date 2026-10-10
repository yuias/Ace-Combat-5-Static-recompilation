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

### Texture dump

| Variable | Meaning |
| --- | --- |
| `PS2_TEX_DUMP=DIR` | Write each distinct texture the renderer decodes to DIR as a PNG, for building a texture replacement pack. DIR is created if it does not exist. |

Each file is named `<key>_<w>x<h>.png`: the key as 16 lowercase hex digits,
then the size in texels. The PNG is 8-bit RGBA with straight alpha, with the
pixels exactly as the renderer sees them. The PS2 alpha value 0x80 (opaque) is
stored as 255, and smaller values are doubled, so a replacement texture uses the
ordinary 0 to 255 range.

The key is a 64-bit FNV-1a hash over the `w*h*4` RGBA bytes of the decoded
texture, followed by `w` and `h` as two little-endian 32-bit words. Two
textures with the same pixels and size share one file. A texture whose file
already exists is not written again, so repeated runs (other scenes, the other
region's disc) add to the same directory.

Both texture decodes are dumped: the one from the recorded upload transfers,
used by the 3D model, terrain and shadow paths, and the one from GS memory,
used by 2D drawing (fonts, HUD, menus) and as the fallback. The GS-memory
decode covers the whole texture as TEX0 sizes it, so unused texels can hold
leftovers from earlier uploads and give the same picture a different key. Mip
levels above 0 are not dumped. Movies are drawn from textures too, so a run
through one writes a file for every frame. At exit the log reports the number
of textures written, already present and failed (`texdump: ...`).

`index.csv` in DIR gets one line per texture written, with a header line when
the file is created:

| Column | Meaning |
| --- | --- |
| `key` | The file name's key. |
| `w`, `h` | Size in texels. |
| `psm` | Pixel storage format from TEX0. |
| `tbp`, `tbw`, `cbp` | Texture base pointer, buffer width and palette base pointer from TEX0, as written in the register. |
| `x0`, `y0` | Origin of the dumped window inside the uploaded texture. |
| `field` | The GS field counter when the texture was first seen. |
| `src` | `rec` for the upload-record decode, `gs` for the GS-memory decode. |

### Texture replacement

Texture replacement draws a texture from a pack folder in place of the
texture the game uploaded. It is off by default.

| Control | Meaning |
| --- | --- |
| `PS2_TEX_PACK=0\|1` | Turn replacement off or on. When set and non-empty, it overrides the `texture_pack` setting. |
| `texture_pack = 0\|1` | The same switch as a setting, in the `[graphics]` section of `ac5_settings.ini`. Default 0. It is read at start. |
| `PS2_TEX_PACK_DIR=DIR` | The pack folder. Default: `texpack` in the current folder. |

Replacement is active only when it is switched on and the folder holds at
least one file with a valid name. With it off, or with an empty pack, the
output is the same as without the feature. With it off the runtime logs
nothing about it.

A pack is a folder of PNG files. It is searched recursively, and a file can sit
in any subfolder. Each file is named like a dumped texture,
`<key>_<w>x<h>.png`: the key as 16 hex digits (upper or lower case), then the
size of the original texture in texels, each from 1 to 4096. A replacement is
the same picture at k times the original size, with the same k on both axes.
k is a whole number from 1 to 8, and one image can be at most 8 MiB as RGBA
(width times height times 4 bytes). A k of 1 replaces the pixels at the
original size. Other `.png` names are counted as ignored, and other file types
(such as the `index.csv` of a dump) are skipped without a message. If two
files have the same key, the first one found is used and the rest are
reported as duplicates.

The usual way to build a pack is to start from a dump (see "Texture dump"):

1. Run the game with `PS2_TEX_DUMP=DIR` until the textures you want have been
   on screen.
2. Copy those PNG files into the pack folder.
3. Edit or upscale them, and keep each file name unchanged. The new image must
   be k times the size in the name.

The key covers the exact decoded pixels and the size. A texture whose palette
changes, such as a palette-animated one, has one key per palette state, so it
needs one file for each state it should replace. A texture decoded from GS
memory can include leftover texels from earlier uploads (see "Texture dump"),
which gives the same picture another key. Dump the texture as it appears in
the game and use that key.

A replacement is drawn with the game's own filtering, so 2D text that the game
draws with bilinear filtering stays bilinear at the higher resolution.

These are not replaced:

- Movie frames.
- Terrain pages that the game fills tile by tile, because the key of the whole
  page changes with each fill.
- The cropped part of a texture that the 3D path cuts out for a clamped
  region. It is drawn at the original resolution.

Files are read only when a texture first needs them, on the thread that
decodes the texture. A large file can cause a short pause the first time it is used. Starting with
a large pack costs no more than reading the file names.

The log shows what happened (`<dir>` is the pack folder as given):

| Line | Meaning |
| --- | --- |
| `texpack: replacing textures from <dir>: <n> files (<n> names ignored, <n> duplicates)` | Replacement is active. |
| `texpack: <dir> holds no replacement files; texture replacement stays off` | The folder has no valid file names. |
| `texpack: cannot read <dir>; texture replacement stays off` | The folder is missing or unreadable. |
| `texpack: loaded <name> at <k>x` | A file was read and accepted. Only the first 16 are logged. |
| `texpack: rejected <name>: <reason>` | A file was refused by the rules above, such as a size that is not a whole multiple. It is not tried again. |
| `texpack: the renderer refused <name> (replacement store full)` | The replacement store is full. Only the first refusal is logged. |
| `texpack: duplicate <name> ignored; the first file found is used` | A repeated key. Only the first 8 are logged. |
| `texpack: <n> textures looked up, <n> replaced from <n> files, <n> without a file, <n> files rejected, <n> refused` | Summary at exit. |
| `vk: <n> replacement images (<n> MB)` | Summary at exit: the images the renderer holds for the pack. |
