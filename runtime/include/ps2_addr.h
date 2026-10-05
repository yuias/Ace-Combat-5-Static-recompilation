#pragma once
#include "ps2_runtime.h"
#include "ps2_region.h"

/* Guest addresses of the executable this runtime is built for. The region is
   fixed per build (see CMakeLists.txt), so PS2_A(NAME) is an integer constant
   expression and works in static initializers. The list of names is
   ps2_addr_list.h; the JP values are generated into ps2_addr_jp.inc. */

#ifndef PS2_BUILD_REGION
#error "PS2_BUILD_REGION comes from CMakeLists.txt (region of the generated ps2_image.c)"
#endif
/* #if cannot see enum constants, so PS2_BUILD_REGION is compared with 1. */
_Static_assert(PS2_REGION_JP == 1, "PS2_BUILD_REGION is compared with literal 1");

#ifdef __cplusplus
extern "C" {
#endif

enum {  /* canonical US addresses */
#define PS2_ADDR(name, us, kind, nwords) PS2_US_##name = (int)(us),
#include "ps2_addr_list.h"
#undef PS2_ADDR
};
enum {  /* generated JP addresses */
#define PS2_ADDR_JP(name, jp) PS2_JP_##name = (int)(jp),
#include "ps2_addr_jp.inc"
#undef PS2_ADDR_JP
};
#if PS2_BUILD_REGION == 1 && defined(PS2_ADDR_JP_PENDING)
#error "runtime/include/ps2_addr_jp.inc has unresolved entries; run python -m regionaddr generate"
#endif
enum {  /* ids for the word table */
#define PS2_ADDR(name, us, kind, nwords) PS2_AID_##name,
#include "ps2_addr_list.h"
#undef PS2_ADDR
    PS2_AID_COUNT
};

#if PS2_BUILD_REGION == 1
#define PS2_A(name) ((u32)PS2_JP_##name)
#else
#define PS2_A(name) ((u32)PS2_US_##name)
#endif

/* Expected code words at the entry's address for this build's region; *n = 0
   (and NULL) when the entry has none. */
const u32 *ps2_addr_words(int aid, unsigned *n);
/* 1 when guest RAM holds the expected words (or there are none). */
int ps2_addr_code_matches(int aid);
/* Checks every entry with code words; logs each mismatch and returns the
   number of mismatching entries. Logs "addr: <n> addresses, <m> checked,
   <game id> ok" when all match. */
int ps2_addr_verify(void);

#ifdef __cplusplus
}
#endif
