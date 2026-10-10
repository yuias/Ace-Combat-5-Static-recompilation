// Unit test of the texture pack index and lookup (ps2_texpack.cpp). The
// renderer is replaced by a stub image function. Usage: texpack_test on|off.
// "on" expects PS2_TEX_PACK=1 and PS2_TEX_PACK_DIR=<dir> in the environment and
// writes its pack into <dir>; "off" expects PS2_TEX_PACK=0.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>

#include "ps2_settings.h"
#include "ps2_texpack.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL: " __VA_ARGS__); printf("\n"); fails++; } } while (0)

ps2_settings ps2_cfg;

static std::string log_text;
extern "C" void ps2_log(const char *fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    log_text += buf;
    log_text += '\n';
}

// Stub renderer: remembers the last image it was given and refuses 64x64.
static int stub_calls;
static std::vector<uint8_t> stub_pixels;
static uint32_t stub_w, stub_h, stub_k;
static int stub_mips;
static uint32_t stub_image(uint8_t *rgba, uint32_t w, uint32_t h, uint32_t k, int mips) {
    stub_calls++;
    stub_w = w;
    stub_h = h;
    stub_k = k;
    stub_mips = mips;
    stub_pixels.assign(rgba, rgba + (size_t)w * h * 4u);
    free(rgba);
    if (w == 64 && h == 64)
        return 0;
    return 1000u + (uint32_t)stub_calls;
}

// Deterministic pixels with varied alpha, so that every seed has its own key.
static std::vector<uint8_t> gen(uint32_t seed, uint32_t w, uint32_t h) {
    std::vector<uint8_t> v((size_t)w * h * 4u);
    uint32_t s = seed * 2654435761u + 12345u;
    for (uint8_t &b : v) {
        s = s * 1664525u + 1013904223u;
        b = (uint8_t)(s >> 24);
    }
    return v;
}

static ps2_texpack_counters counters() {
    ps2_texpack_counters c;
    ps2_texpack_counters_get(&c);
    return c;
}

static std::string pack_name(const std::vector<uint8_t> &px, uint32_t w, uint32_t h) {
    char n[64];
    snprintf(n, sizeof n, "%016llx_%ux%u.png", (unsigned long long)ps2_texpack_key(px.data(), w, h),
             w, h);
    return n;
}

static void put(const std::string &dir, const std::string &name, const std::vector<uint8_t> &px,
                uint32_t w, uint32_t h) {
    std::string p = dir + "/" + name;
    CHECK(ps2_texpack_write_png(p.c_str(), px.data(), w, h), "cannot write %s", p.c_str());
}

static void put_text(const std::string &dir, const std::string &name) {
    std::string p = dir + "/" + name;
    FILE *f = fopen(p.c_str(), "wb");
    if (f) {
        fputs("x", f);
        fclose(f);
    } else {
        CHECK(0, "cannot write %s", p.c_str());
    }
}

static int run_off() {
    CHECK(!ps2_texpack_active(), "off: pack is active");
    ps2_texpack_set_image_fn(stub_image);
    std::vector<uint8_t> px = gen(1, 32, 32);
    uint32_t k = 0;
    CHECK(ps2_texpack_lookup(px.data(), 32, 32, 0, &k) == 0, "off: lookup answered");
    CHECK(stub_calls == 0, "off: stub called");
    CHECK(log_text.find("texpack:") == std::string::npos, "off: logged: %s", log_text.c_str());
    ps2_texpack_counters c = counters();
    CHECK(c.files == 0 && c.lookups == 0, "off: counters not zero");
    return fails;
}

static int run_on() {
    const char *dir_c = getenv("PS2_TEX_PACK_DIR");
    if (!dir_c || !*dir_c) {
        printf("FAIL: PS2_TEX_PACK_DIR is not set\n");
        return 1;
    }
    std::string dir = dir_c;
    CreateDirectoryA(dir.c_str(), nullptr);
    CreateDirectoryA((dir + "/sub").c_str(), nullptr);

    std::vector<uint8_t> A = gen(1, 32, 32), B = gen(2, 32, 32), C = gen(3, 16, 16),
                         D = gen(4, 8, 8), E = gen(5, 32, 32), F = gen(6, 32, 32),
                         G = gen(7, 64, 64), H = gen(8, 32, 32);
    std::vector<uint8_t> fa = gen(101, 128, 128), fa2 = gen(102, 128, 128);
    put(dir, pack_name(A, 32, 32), fa, 128, 128);
    put(dir, pack_name(B, 32, 32), gen(103, 100, 100), 100, 100);
    put(dir, pack_name(C, 16, 16), gen(104, 32, 64), 32, 64);
    put(dir, pack_name(D, 8, 8), gen(105, 72, 72), 72, 72);
    std::vector<uint8_t> fe = gen(106, 32, 32);
    put(dir, pack_name(E, 32, 32), fe, 32, 32);
    put(dir, pack_name(H, 32, 32), gen(107, 64, 64), 64, 64);
    put(dir, "0123_32x32.png", gen(108, 4, 4), 4, 4);
    put(dir, "abc.png", gen(109, 4, 4), 4, 4);
    put_text(dir, "notes.txt");
    put(dir + "/sub", pack_name(A, 32, 32), fa2, 128, 128);

    uint32_t k = 0, h;
    ps2_texpack_counters c;

    // No image function yet: nothing is looked up and no entry changes.
    CHECK(ps2_texpack_active(), "on: pack is not active");
    CHECK(ps2_texpack_lookup(A.data(), 32, 32, 1, &k) == 0, "lookup without image function");
    c = counters();
    CHECK(c.lookups == 0 && c.loaded == 0 && c.rejected == 0, "lookup without image function counted");
    ps2_texpack_set_image_fn(stub_image);

    // 11. activation
    char expect[1200];
    snprintf(expect, sizeof expect,
             "texpack: replacing textures from %s: 6 files (2 names ignored, 1 duplicates)\n",
             dir.c_str());
    CHECK(log_text.find(expect) != std::string::npos, "activation line missing: %s", log_text.c_str());
    c = counters();
    CHECK(c.files == 6, "files %u", c.files);
    // 6. ignored names; notes.txt is not counted
    CHECK(c.ignored == 2, "ignored %u", c.ignored);
    // 7. duplicate
    CHECK(c.duplicates == 1, "duplicates %u", c.duplicates);

    // 1. a 4x file
    h = ps2_texpack_lookup(A.data(), 32, 32, 1, &k);
    CHECK(h != 0 && k == 4, "case 1: handle %u scale %u", h, k);
    CHECK(stub_calls == 1 && stub_w == 128 && stub_h == 128 && stub_k == 4 && stub_mips == 1,
          "case 1: stub saw %ux%u k%u mips %d (%d calls)", stub_w, stub_h, stub_k, stub_mips, stub_calls);
    CHECK(stub_pixels == fa, "case 1: stub pixels differ from the first file");
    uint32_t k2 = 0;
    CHECK(ps2_texpack_lookup(A.data(), 32, 32, 0, &k2) == h && k2 == 4, "case 1: second lookup");
    CHECK(stub_calls == 1, "case 1: stub called again");

    // 2. not a whole multiple
    k = 0;
    CHECK(ps2_texpack_lookup(B.data(), 32, 32, 0, &k) == 0, "case 2: answered");
    CHECK(counters().rejected == 1, "case 2: rejected %u", counters().rejected);
    CHECK(ps2_texpack_lookup(B.data(), 32, 32, 0, &k) == 0, "case 2: answered the second time");
    CHECK(counters().rejected == 1, "case 2: rejected again");

    // 3. axes scale differently
    CHECK(ps2_texpack_lookup(C.data(), 16, 16, 0, &k) == 0, "case 3: answered");
    CHECK(counters().rejected == 2, "case 3: rejected %u", counters().rejected);

    // 4. scale 9
    CHECK(ps2_texpack_lookup(D.data(), 8, 8, 0, &k) == 0, "case 4: answered");
    CHECK(counters().rejected == 3, "case 4: rejected %u", counters().rejected);
    CHECK(log_text.find("texpack: rejected ") != std::string::npos, "rejection not logged");

    // 5. same size
    k = 0;
    h = ps2_texpack_lookup(E.data(), 32, 32, 0, &k);
    CHECK(h != 0 && k == 1, "case 5: handle %u scale %u", h, k);
    CHECK(stub_calls == 2 && stub_w == 32 && stub_mips == 0 && stub_pixels == fe,
          "case 5: stub saw %ux%u mips %d", stub_w, stub_h, stub_mips);

    // 8. no file for this key
    uint64_t lookups = counters().lookups, missing = counters().missing;
    CHECK(ps2_texpack_lookup(F.data(), 32, 32, 0, &k) == 0, "case 8: answered");
    CHECK(counters().missing == missing + 1 && counters().lookups == lookups + 1,
          "case 8: missing %llu", (unsigned long long)counters().missing);

    // 9. no file of this size: no hash, no counter
    lookups = counters().lookups;
    missing = counters().missing;
    CHECK(ps2_texpack_lookup(G.data(), 64, 64, 0, &k) == 0, "case 9: answered");
    CHECK(counters().lookups == lookups && counters().missing == missing, "case 9: counted");

    // 10. the renderer refuses one file
    int calls = stub_calls;
    CHECK(ps2_texpack_lookup(H.data(), 32, 32, 0, &k) == 0, "case 10: answered");
    CHECK(stub_calls == calls + 1 && counters().refused == 1, "case 10: refused %u", counters().refused);
    CHECK(ps2_texpack_lookup(H.data(), 32, 32, 0, &k) == 0, "case 10: answered the second time");
    CHECK(stub_calls == calls + 1 && counters().refused == 1, "case 10: stub called again");
    CHECK(log_text.find("texpack: the renderer refused ") != std::string::npos, "refusal not logged");

    c = counters();
    CHECK(c.loaded == 2 && c.rejected == 3 && c.replaced == 3 && c.missing == 1,
          "final counters: loaded %u rejected %u replaced %llu missing %llu", c.loaded, c.rejected,
          (unsigned long long)c.replaced, (unsigned long long)c.missing);
    CHECK(c.lookups == 10, "final lookups %llu", (unsigned long long)c.lookups);

    ps2_texpack_report();
    CHECK(log_text.find("texpack: 10 textures looked up, 3 replaced from 2 files, 1 without a file, "
                        "3 files rejected, 1 refused\n") != std::string::npos,
          "report line missing: %s", log_text.c_str());
    return fails;
}

int main(int argc, char **argv) {
    if (argc < 2 || (strcmp(argv[1], "on") != 0 && strcmp(argv[1], "off") != 0)) {
        printf("usage: texpack_test on|off\n");
        return 2;
    }
    bool on = strcmp(argv[1], "on") == 0;
    on ? run_on() : run_off();
    if (fails) {
        printf("%s", log_text.c_str());
        return 1;
    }
    printf("texpack test: all checks passed\n");
    return 0;
}
