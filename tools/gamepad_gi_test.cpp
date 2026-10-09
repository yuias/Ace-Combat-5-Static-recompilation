// Runtime test of the gamepad hub with the real GameInput backend. It does not
// need a controller: it checks start-up timing, the ready/unavailable report,
// whatever pads are present, and that shutdown works even while the start-up
// thread is still inside GameInputCreate.
#include "ps2_gamepad.h"
#include "ps2_os.h"
#include "ps2_settings.h"
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static int g_failed;
#define CHECK(cond) do { if (!(cond)) { \
    printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); g_failed++; } } while (0)

static std::vector<std::string> g_log;
static ps2_mutex_t g_log_lock = PS2_MUTEX_INIT;  // the backend logs from its own thread
extern "C" void ps2_log(const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    ps2_mutex_lock(&g_log_lock);
    printf("%s\n", buf);
    fflush(stdout);
    g_log.push_back(buf);
    ps2_mutex_unlock(&g_log_lock);
}
static bool log_has_prefix(const char *prefix) {
    bool found = false;
    ps2_mutex_lock(&g_log_lock);
    for (const std::string &l : g_log)
        if (l.compare(0, strlen(prefix), prefix) == 0) found = true;
    ps2_mutex_unlock(&g_log_lock);
    return found;
}

static double ms_since(uint64_t t0) {
    return (double)(ps2_mono_ns() - t0) / 1e6;
}

int main() {
    uint64_t t = ps2_mono_ns();
    ps2_gamepad_init();
    double init_ms = ms_since(t);
    printf("gamepad gi test: init returned after %.1f ms\n", init_ms);
    CHECK(init_ms < 100.0);

    t = ps2_mono_ns();
    while (ps2_gamepad_status() == PS2_GAMEPAD_STARTING && ms_since(t) < 10000.0) {
        ps2_gamepad_update(1);
        ps2_sleep_ms(10);
    }
    int status = ps2_gamepad_status();
    CHECK(status != PS2_GAMEPAD_STARTING);
    if (status == PS2_GAMEPAD_UNAVAILABLE) {
        printf("gamepad gi test: skipped (GameInput unavailable)\n");
        ps2_gamepad_shutdown();
        return g_failed ? 1 : 0;
    }

    CHECK(log_has_prefix("pad: GameInput ready after "));
    // Pads that were present may arrive tens of ms after READY.
    t = ps2_mono_ns();
    while (ms_since(t) < 300.0) {
        ps2_gamepad_update(1);
        ps2_pad_event ev;
        while (ps2_gamepad_next_event(&ev)) {}
        ps2_sleep_ms(10);
    }
    ps2_pad_info pads[PS2_GAMEPAD_MAX];
    int n = ps2_gamepad_list(pads, PS2_GAMEPAD_MAX);
    CHECK(n >= 0 && n <= PS2_GAMEPAD_MAX);
    for (int i = 0; i < n; i++) {
        CHECK(pads[i].id != 0);
        CHECK(pads[i].name[0] != 0);
        for (int j = i + 1; j < n; j++) CHECK(pads[i].id != pads[j].id);
    }
    uint32_t cur = ps2_gamepad_current();
    if (cur) {
        CHECK(ps2_gamepad_current_name() != nullptr);
        for (int a = 0; a < PS2_PAD_AXIS_COUNT; a++) {
            int v = ps2_gamepad_axis(a);
            if (a == PS2_PAD_AXIS_LEFT_TRIGGER || a == PS2_PAD_AXIS_RIGHT_TRIGGER)
                CHECK(v >= 0 && v <= 32767);
            else
                CHECK(v >= -32768 && v <= 32767);
        }
    }
    printf("gamepad gi test: %d pads, current %s\n", n,
           cur && ps2_gamepad_current_name() ? ps2_gamepad_current_name() : "none");

    ps2_gamepad_shutdown();
    CHECK(ps2_gamepad_status() == PS2_GAMEPAD_STARTING);

    // Shutdown while the start-up thread may still be inside GameInputCreate.
    t = ps2_mono_ns();
    ps2_gamepad_init();
    ps2_gamepad_shutdown();
    double quick_ms = ms_since(t);
    printf("gamepad gi test: init+shutdown took %.1f ms\n", quick_ms);
    CHECK(quick_ms < 10000.0);

    if (g_failed) {
        printf("gamepad gi test: %d checks failed\n", g_failed);
        return 1;
    }
    printf("gamepad gi test: all checks passed\n");
    return 0;
}
