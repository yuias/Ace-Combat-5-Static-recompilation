#ifndef PS2_REGION_H
#define PS2_REGION_H

#include <stddef.h>
#include "ps2_runtime.h"

#ifdef __cplusplus
extern "C" {
#endif

enum { PS2_REGION_US = 0, PS2_REGION_JP = 1 };

/* Emitted by ps2recomp into the generated ps2_image.c. Region-specific
   address tables select their row with ps2_region. */
extern const u32  ps2_region;
extern const char ps2_game_id[];        /* "SLUS-20851" / "SLPS-25418" */
extern const char ps2_region_exe[];     /* "SLUS_208.51" / "SLPS_254.18" */
extern const char ps2_region_config[];  /* "config" / "config/slps-25418" */

/* "<ps2_region_config>/<name>" into buf; returns buf. */
const char *ps2_region_config_path(const char *name, char *buf, size_t n);

/* Call after the disc is open. Compares SYSTEM.CNF's BOOT2 executable with
   ps2_region_exe (case-insensitive). 0: match, no SYSTEM.CNF to compare, or
   a mismatch allowed by PS2_ALLOW_REGION_MISMATCH=1; -1: mismatch (already
   logged with both names). */
int ps2_region_check_disc(void);

#ifdef __cplusplus
}
#endif

#endif
