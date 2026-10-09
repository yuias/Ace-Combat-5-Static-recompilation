#ifndef PS2_TEXPACK_H
#define PS2_TEXPACK_H
/* Texture pack support, implemented in ps2_texpack.cpp: a content key for a
   decoded texture, a PNG reader and writer (Windows WIC), and the texture dump
   enabled by PS2_TEX_DUMP=<dir>. No windows.h here, so includers never see its
   macros. */
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Where a dumped texture came from, for index.csv. tex0 is the raw TEX0
   register; x0, y0 is the origin of the window inside the texture; field is
   the GS field counter when the texture was first seen. */
typedef struct {
    uint64_t tex0;
    uint32_t x0, y0;
    uint32_t field;
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

/* Counts a texture binding that did not come from the upload records and so
   is not dumped. */
void ps2_texpack_note_uncovered(void);

/* Logs the dump counters; silent when dumping is off or nothing happened. */
void ps2_texpack_report(void);

#ifdef __cplusplus
}
#endif
#endif
