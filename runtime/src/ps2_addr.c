#include "ps2_addr.h"

/* Every list entry needs a JP value even in the US build, so a name added to
   ps2_addr_list.h without regenerating ps2_addr_jp.inc fails to compile. */
[[maybe_unused]] static void jp_values_complete(void) {
#define PS2_ADDR(name, us, kind, nwords) (void)PS2_JP_##name;
#include "ps2_addr_list.h"
#undef PS2_ADDR
}

static const u32 addr_of[PS2_AID_COUNT] = {
#define PS2_ADDR(name, us, kind, nwords) PS2_A(name),
#include "ps2_addr_list.h"
#undef PS2_ADDR
};

static const char *const name_of[PS2_AID_COUNT] = {
#define PS2_ADDR(name, us, kind, nwords) #name,
#include "ps2_addr_list.h"
#undef PS2_ADDR
};

typedef struct { int aid; unsigned n; u32 w[8]; } addr_words;
#define PS2_WORDS_ROW(name, n, ...) { PS2_AID_##name, n, { __VA_ARGS__ } },

/* Only this build's rows are compiled in. */
#if PS2_BUILD_REGION == 1
#define PS2_WORDS_US(...)
#define PS2_WORDS_JP(name, n, ...) PS2_WORDS_ROW(name, n, __VA_ARGS__)
#define BUILD_GAME_ID "SLPS-25418"
#else
#define PS2_WORDS_US(name, n, ...) PS2_WORDS_ROW(name, n, __VA_ARGS__)
#define PS2_WORDS_JP(...)
#define BUILD_GAME_ID "SLUS-20851"
#endif
static const addr_words rows[] = {
#include "ps2_addr_words.inc"
};
#define NROWS (sizeof rows / sizeof rows[0])

static const addr_words *row_of(int aid) {
    for (size_t i = 0; i < NROWS; i++)
        if (rows[i].aid == aid) return &rows[i];
    return NULL;
}

const u32 *ps2_addr_words(int aid, unsigned *n) {
    const addr_words *r = row_of(aid);
    *n = r ? r->n : 0;
    return r ? r->w : NULL;
}

/* Logs the first differing word and returns 0 on a mismatch. */
static int row_matches(const addr_words *r, int log_it) {
    u32 base = addr_of[r->aid];
    for (unsigned i = 0; i < r->n; i++) {
        u32 got = ps2_r32(base + 4u * i);
        if (got != r->w[i]) {
            if (log_it)
                ps2_log("addr: %s at %08X: word %u is %08X, expected %08X",
                        name_of[r->aid], base, i, got, r->w[i]);
            return 0;
        }
    }
    return 1;
}

int ps2_addr_code_matches(int aid) {
    const addr_words *r = row_of(aid);
    return !r || row_matches(r, 0);
}

int ps2_addr_verify(void) {
    int bad = 0;
    for (size_t i = 0; i < NROWS; i++)
        if (!row_matches(&rows[i], 1)) bad++;
    if (!bad)
        ps2_log("addr: %d addresses, %u checked, %s ok", (int)PS2_AID_COUNT,
                (unsigned)NROWS, BUILD_GAME_ID);
    return bad;
}
