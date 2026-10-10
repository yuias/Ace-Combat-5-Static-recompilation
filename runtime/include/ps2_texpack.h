#ifndef PS2_TEXPACK_H
#define PS2_TEXPACK_H
/* Texture pack support, implemented in ps2_texpack.cpp: a content key for a
   decoded texture, a PNG reader and writer (Windows WIC), the texture dump
   enabled by PS2_TEX_DUMP=<dir>, and the replacement of decoded textures from a
   pack directory. No windows.h here, so includers never see its macros. */
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Where a dumped texture came from, for index.csv. tex0 is the raw TEX0
   register; x0, y0 is the origin of the window inside the texture; field is
   the GS field counter when the texture was first seen. src says which decode
   produced the pixels. */
enum { PS2_TEXPACK_SRC_RECORDS, PS2_TEXPACK_SRC_GS };
typedef struct {
    uint64_t tex0;
    uint32_t x0, y0;
    uint32_t field;
    uint32_t src;
} ps2_texpack_meta;

/* 64-bit FNV-1a over the w*h*4 RGBA bytes, then over w and h as two
   little-endian 32-bit words. Replacement textures are looked up by this key. */
uint64_t ps2_texpack_key(const uint8_t *rgba, uint32_t w, uint32_t h);

/* Writes w*h RGBA8 pixels (straight alpha, 0..255) as a PNG. The file appears
   under its final name only when complete. Returns 1 on success. COM is
   initialised on the calling thread on first use. */
int ps2_texpack_write_png(const char *path_utf8, const uint8_t *rgba, uint32_t w, uint32_t h);

/* Reads a PNG as RGBA8 into a malloc'd w*h*4 buffer (free it with free), or
   returns NULL. Any format WIC can convert is accepted. */
uint8_t *ps2_texpack_read_png(const char *path_utf8, uint32_t *w, uint32_t *h);

/* 1 when PS2_TEX_DUMP names a directory. The environment is read once. */
int ps2_texpack_dump_enabled(void);

/* Dumps one decoded texture (w*h RGBA8) under its key unless it was dumped
   already, in this run or an earlier one. Does nothing when dumping is off. */
void ps2_texpack_dump(const uint8_t *rgba, uint32_t w, uint32_t h, const ps2_texpack_meta *meta);

/* Largest replacement scale k (the image is k times the native size in both
   axes; equal to the renderer's largest internal scale) and the largest
   replacement image in bytes (W*H*4). */
#define PS2_TEXPACK_MAX_SCALE        8u
#define PS2_TEXPACK_MAX_IMAGE_BYTES  (8u << 20)

/* Turns validated replacement pixels (W*H RGBA8, malloc'd) into a renderer
   image. Takes ownership of rgba in every case. Returns a nonzero handle, or
   0 when it refused the image (rgba freed). mips != 0 asks for a full chain. */
typedef uint32_t (*ps2_texpack_image_fn)(uint8_t *rgba, uint32_t w, uint32_t h,
                                         uint32_t scale, int mips);
void ps2_texpack_set_image_fn(ps2_texpack_image_fn fn);

/* 1 when replacement is switched on (PS2_TEX_PACK, else the texture_pack
   setting) and the pack directory (PS2_TEX_PACK_DIR, else "texpack") holds at
   least one valid file name. Decided once, on the first call. */
int ps2_texpack_active(void);

/* Replacement for one decoded texture (w*h RGBA8, the pixels the dump would
   write). Returns 0 for none, else the image handle; *scale receives k.
   Hashes only when the pack has a file of size w x h; decodes on first hit.
   Returns 0 without changing any entry when no image function is set. */
uint32_t ps2_texpack_lookup(const uint8_t *rgba, uint32_t w, uint32_t h, int mips,
                            uint32_t *scale);

typedef struct {
    uint32_t files, ignored, duplicates;   /* index */
    uint64_t lookups, replaced, missing;   /* keys hashed, answered, no file */
    uint32_t loaded, rejected, refused;    /* files decoded, refused by rules, by renderer */
} ps2_texpack_counters;
void ps2_texpack_counters_get(ps2_texpack_counters *out);

/* Logs the dump counters (silent when dumping is off or nothing happened) and,
   when replacement is active, the replacement counters. */
void ps2_texpack_report(void);

#ifdef __cplusplus
}
#endif
#endif
