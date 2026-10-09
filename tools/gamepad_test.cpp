// Headless test of the gamepad hub (ps2_gamepad.cpp) against a fake backend:
// hotplug, selection, conversion to the stored button/axis ids and ranges,
// event synthesis, queue limits and shutdown order. No GameInput needed.
#include "ps2_gamepad.h"
#include "ps2_gamepad_backend.h"
#include "ps2_settings.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

static int g_failed;
#define CHECK(cond) do { if (!(cond)) { \
    printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); g_failed++; } } while (0)

// ps2_log stub: prints and keeps every line so checks can look for text.
static std::vector<std::string> g_log;
extern "C" void ps2_log(const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    printf("%s\n", buf);
    g_log.push_back(buf);
}
static bool log_has(size_t from, const char *text) {
    for (size_t i = from; i < g_log.size(); i++)
        if (g_log[i] == text) return true;
    return false;
}

// Fake backend.
struct FakeDev {
    std::atomic<int> refs;
    ps2_gi_reading reading;
    bool has_reading;
    FakeDev() : refs(1), reading(), has_reading(false) {}
};
static std::vector<FakeDev *> g_all;
static int g_starts, g_stops, g_destroys;
// T start, S stop, R release, D destroy. Only the shutdown in check 16 is
// recorded: releases also come from the writer thread in check 14, and the
// string is not synchronized.
static std::string g_order;
static std::atomic<bool> g_record(false);

void ps2_gpb_start() { g_starts++; }
void ps2_gpb_stop() { g_stops++; if (g_record) g_order += 'S'; }
void ps2_gpb_destroy() { g_destroys++; if (g_record) g_order += 'D'; }
bool ps2_gpb_read(void *dev, ps2_gi_reading *out) {
    FakeDev *d = (FakeDev *)dev;
    if (!d->has_reading) return false;
    *out = d->reading;
    return true;
}
void ps2_gpb_release(void *dev) {
    ((FakeDev *)dev)->refs--;
    if (g_record) g_order += 'R';
}

static FakeDev *new_dev() {
    FakeDev *d = new FakeDev();
    g_all.push_back(d);
    return d;
}
static void connect(FakeDev *d, const char *name) { ps2_gph_device(d, 1, name); }
static void remove_dev(FakeDev *d) { ps2_gph_device(d, 0, nullptr); }

static ps2_gi_reading rd(uint32_t buttons = 0, float lt = 0, float rt = 0,
                         float lx = 0, float ly = 0, float rx = 0, float ry = 0) {
    ps2_gi_reading r;
    r.buttons = buttons;
    r.left_trigger = lt;
    r.right_trigger = rt;
    r.left_x = lx;
    r.left_y = ly;
    r.right_x = rx;
    r.right_y = ry;
    return r;
}

static std::vector<ps2_pad_event> drain() {
    std::vector<ps2_pad_event> v;
    ps2_pad_event e;
    while (ps2_gamepad_next_event(&e)) v.push_back(e);
    return v;
}

static void set(FakeDev *d, const ps2_gi_reading &r) {
    d->reading = r;
    d->has_reading = true;
}

// Sets the reading, updates once and throws the resulting events away.
static void feed(FakeDev *d, const ps2_gi_reading &r) {
    set(d, r);
    ps2_gamepad_update(1);
    drain();
}

static int held_count() {
    int n = 0;
    for (int b = 0; b < PS2_PAD_BUTTON_COUNT; b++) n += ps2_gamepad_button(b);
    return n;
}

static bool only_held(std::initializer_list<int> ids) {
    for (int b = 0; b < PS2_PAD_BUTTON_COUNT; b++) {
        bool want = false;
        for (int i : ids) want = want || i == b;
        if ((ps2_gamepad_button(b) != 0) != want) return false;
    }
    return true;
}

static bool all_axes_zero() {
    for (int a = 0; a < PS2_PAD_AXIS_COUNT; a++)
        if (ps2_gamepad_axis(a) != 0) return false;
    return true;
}

int main() {
    // 1. Before init.
    CHECK(ps2_gamepad_status() == PS2_GAMEPAD_STARTING);
    CHECK(ps2_gamepad_current() == 0);
    CHECK(ps2_gamepad_current_name() == nullptr);
    {
        ps2_pad_info info[4];
        ps2_pad_event e;
        CHECK(ps2_gamepad_list(info, 4) == 0);
        CHECK(ps2_gamepad_button(0) == 0);
        CHECK(ps2_gamepad_axis(0) == 0);
        CHECK(ps2_gamepad_next_event(&e) == 0);
    }
    ps2_gamepad_init();
    CHECK(g_starts == 1);
    ps2_gamepad_init();
    CHECK(g_starts == 1);
    ps2_gph_status(PS2_GAMEPAD_READY);
    CHECK(ps2_gamepad_status() == PS2_GAMEPAD_READY);

    // 2. First pad connects and is selected.
    FakeDev *A = new_dev();
    size_t mark = g_log.size();
    connect(A, "Pad A");
    {
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        CHECK(ps2_gamepad_list(info, PS2_GAMEPAD_MAX) == 0);
        CHECK(ps2_gamepad_current() == 0);
    }
    ps2_gamepad_update(1);
    {
        std::vector<ps2_pad_event> ev = drain();
        CHECK(ev.size() == 1);
        if (ev.size() == 1) {
            CHECK(ev[0].type == PS2_PEV_ADDED);
            CHECK(ev[0].id == 1);
        }
    }
    CHECK(ps2_gamepad_current() == 1);
    CHECK(ps2_gamepad_current_name() && strcmp(ps2_gamepad_current_name(), "Pad A") == 0);
    CHECK(log_has(mark, "pad: Pad A connected"));

    // 3. First reading is the baseline: held state without events.
    set(A, rd(PS2_GI_A | PS2_GI_MENU | PS2_GI_DPAD_LEFT | PS2_GI_PADDLE_RIGHT1 |
                  PS2_GI_LEFT_THUMBSTICK,
              1.0f, 0.5f, 1.0f, 1.0f, -1.0f, -1.0f));
    ps2_gamepad_update(1);
    CHECK(drain().empty());
    CHECK(only_held({0, 6, 13, 16, 7}));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == 32767);
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTY) == -32768);
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_RIGHTX) == -32768);
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_RIGHTY) == 32767);
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFT_TRIGGER) == 32767);
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_RIGHT_TRIGGER) == 16384);

    // 4. Clamping and rounding.
    feed(A, rd(0, 0, 0, 2.0f));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == 32767);
    feed(A, rd(0, 0, 0, 0, -3.0f));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTY) == 32767);
    feed(A, rd(0, 0, 0, NAN));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == 0);
    feed(A, rd(0, -0.5f));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFT_TRIGGER) == 0);
    feed(A, rd(0, 0, 0, 0.25f));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == 8192);
    feed(A, rd(0, 0, 0, 0, 0.25f));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTY) == -8192);
    feed(A, rd(0, 0, 0, -0.5f));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == -16384);

    // 5. Bit table.
    {
        struct { uint32_t bit; int id; } table[] = {
            { PS2_GI_MENU, 6 }, { PS2_GI_VIEW, 4 }, { PS2_GI_A, 0 }, { PS2_GI_B, 1 },
            { PS2_GI_X, 2 }, { PS2_GI_Y, 3 }, { PS2_GI_DPAD_UP, 11 },
            { PS2_GI_DPAD_DOWN, 12 }, { PS2_GI_DPAD_LEFT, 13 },
            { PS2_GI_DPAD_RIGHT, 14 }, { PS2_GI_LEFT_SHOULDER, 9 },
            { PS2_GI_RIGHT_SHOULDER, 10 }, { PS2_GI_LEFT_THUMBSTICK, 7 },
            { PS2_GI_RIGHT_THUMBSTICK, 8 }, { PS2_GI_PADDLE_LEFT1, 17 },
            { PS2_GI_PADDLE_LEFT2, 19 }, { PS2_GI_PADDLE_RIGHT1, 16 },
            { PS2_GI_PADDLE_RIGHT2, 18 },
        };
        for (auto &t : table) {
            feed(A, rd(t.bit));
            CHECK(only_held({t.id}));
            CHECK(ps2_gamepad_button(5) == 0);
        }
        const uint32_t ignored[] = { 0x4000, 0x8000, 0x10000, 0x20000, 0x40000,
                                     0x80000, 0x100000, 0x200000, 0x400000,
                                     0x800000, 0x1000000, 0x2000000 };
        for (uint32_t bit : ignored) {
            feed(A, rd(bit));
            CHECK(held_count() == 0);
        }
    }

    // 6. Events for a change; none for an unchanged reading.
    feed(A, rd(PS2_GI_A));
    set(A, rd(PS2_GI_B, 0, 0, 0.5f));
    ps2_gamepad_update(1);
    {
        std::vector<ps2_pad_event> ev = drain();
        CHECK(ev.size() == 3);
        if (ev.size() == 3) {
            CHECK(ev[0].type == PS2_PEV_BUTTON_UP && ev[0].button == 0 && ev[0].id == 1);
            CHECK(ev[1].type == PS2_PEV_BUTTON_DOWN && ev[1].button == 1 && ev[1].id == 1);
            CHECK(ev[2].type == PS2_PEV_AXIS && ev[2].axis == 0 && ev[2].value == 16384 &&
                  ev[2].id == 1);
        }
    }
    ps2_gamepad_update(1);
    CHECK(drain().empty());

    // 7. Without focus the pad reads as neutral and sends nothing.
    ps2_gamepad_update(0);
    CHECK(drain().empty());
    CHECK(held_count() == 0);
    CHECK(all_axes_zero());
    ps2_gamepad_update(1);
    CHECK(drain().empty());
    CHECK(only_held({1}));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == 16384);

    // 8. A failed read keeps the snapshot and sends nothing.
    A->has_reading = false;
    ps2_gamepad_update(1);
    CHECK(drain().empty());
    CHECK(only_held({1}));
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == 16384);
    A->has_reading = true;

    // 9. A second pad is listed but not selected; a duplicate connect is not.
    FakeDev *B = new_dev();
    connect(B, "Pad B");
    ps2_gamepad_update(1);
    {
        std::vector<ps2_pad_event> ev = drain();
        CHECK(ev.size() == 1);
        if (ev.size() == 1) CHECK(ev[0].type == PS2_PEV_ADDED && ev[0].id == 2);
    }
    CHECK(ps2_gamepad_current() == 1);
    {
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        int n = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
        CHECK(n == 2);
        if (n == 2) {
            CHECK(info[0].id == 1 && strcmp(info[0].name, "Pad A") == 0);
            CHECK(info[1].id == 2 && strcmp(info[1].name, "Pad B") == 0);
        }
    }
    A->refs++;  // the backend's own reference for the repeated notification
    connect(A, "Pad A");
    ps2_gamepad_update(1);
    CHECK(drain().empty());
    CHECK(A->refs == 1);

    // 10. Removing an unselected pad leaves the selection alone.
    remove_dev(B);
    ps2_gamepad_update(1);
    {
        std::vector<ps2_pad_event> ev = drain();
        CHECK(ev.size() == 1);
        if (ev.size() == 1) CHECK(ev[0].type == PS2_PEV_REMOVED && ev[0].id == 2);
    }
    CHECK(ps2_gamepad_current() == 1);
    CHECK(B->refs == 0);

    // 11. Names: empty -> "Controller"; long names are cut at a character edge.
    FakeDev *C = new_dev();
    connect(C, "");
    FakeDev *D = new_dev();
    {
        std::string longname(126, 'x');
        while (longname.size() + 3 <= 200) longname += "\xE3\x81\x82";  // 3-byte characters
        while (longname.size() < 200) longname += 'y';
        connect(D, longname.c_str());
    }
    ps2_gamepad_update(1);
    drain();
    {
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        int n = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
        CHECK(n == 3);
        if (n == 3) {
            CHECK(info[1].id == 3 && strcmp(info[1].name, "Controller") == 0);
            CHECK(info[2].id == 4 && strcmp(info[2].name, std::string(126, 'x').c_str()) == 0);
        }
    }

    // 12. Selecting another pad starts from a baseline.
    set(C, rd(PS2_GI_A, 0, 0, 1.0f));
    mark = g_log.size();
    ps2_gamepad_select(3);
    CHECK(ps2_gamepad_current() == 3);
    CHECK(log_has(mark, "pad: Controller selected"));
    ps2_gamepad_update(1);
    CHECK(drain().empty());
    CHECK(only_held({0}));
    mark = g_log.size();
    ps2_gamepad_select(99);
    ps2_gamepad_select(3);
    CHECK(ps2_gamepad_current() == 3);
    CHECK(g_log.size() == mark);

    // 13. Removing the selected pad.
    mark = g_log.size();
    remove_dev(C);
    ps2_gamepad_update(1);
    {
        std::vector<ps2_pad_event> ev = drain();
        CHECK(ev.size() == 1);
        if (ev.size() == 1) CHECK(ev[0].type == PS2_PEV_REMOVED && ev[0].id == 3);
    }
    CHECK(ps2_gamepad_current() == 0);
    CHECK(ps2_gamepad_current_name() == nullptr);
    CHECK(log_has(mark, "pad: Controller disconnected"));
    CHECK(held_count() == 0 && all_axes_zero());
    CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == 0);
    CHECK(C->refs == 0);
    {
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        int n = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
        CHECK(n == 2);
        if (n == 2) CHECK(info[0].id == 1 && info[1].id == 4);
    }

    // 14. Backend threads race the hub thread.
    {
        FakeDev *T = new_dev();
        T->refs = 0;  // every notification below brings its own reference
        std::atomic<bool> done(false);
        std::thread th([T, &done] {
            for (int i = 0; i < 50; i++) {
                T->refs++;
                ps2_gph_device(T, 1, "Pad T");
                ps2_gph_device(T, 0, nullptr);
                std::this_thread::sleep_for(std::chrono::microseconds(200));
            }
            done = true;
        });
        while (!done) {
            ps2_gamepad_update(1);
            drain();
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
        th.join();
        ps2_gamepad_update(1);
        // A removal lost to a full queue would leave T listed; the final one
        // is delivered on its own.
        remove_dev(T);
        ps2_gamepad_update(1);
        drain();
        CHECK(T->refs == 0);
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        int n = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
        CHECK(n == 2);
    }

    // 14b. Removing a pad listed before the selected one keeps the selection
    // on the same pad. Two pads follow the selected one so a stale index would
    // land on a different live pad. Q also has a name that truncates to
    // nothing and must fall back to "Controller".
    {
        FakeDev *P = new_dev();
        FakeDev *Q = new_dev();
        connect(P, "Pad P");
        connect(Q, std::string(200, '\x80').c_str());
        ps2_gamepad_update(1);
        drain();
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        int n = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
        CHECK(n == 4);
        if (n == 4) {
            CHECK(info[1].id == 4);  // D, listed before P
            CHECK(strcmp(info[3].name, "Controller") == 0);
            uint32_t pid = info[2].id;
            ps2_gamepad_select(pid);
            CHECK(ps2_gamepad_current() == pid);
            set(P, rd(PS2_GI_B, 0, 0, 0.5f));
            set(Q, rd(PS2_GI_X));
            ps2_gamepad_update(1);
            drain();
            CHECK(only_held({1}));

            remove_dev(D);
            ps2_gamepad_update(1);
            drain();
            CHECK(D->refs == 0);
            CHECK(ps2_gamepad_current() == pid);
            CHECK(ps2_gamepad_current_name() &&
                  strcmp(ps2_gamepad_current_name(), "Pad P") == 0);
            CHECK(only_held({1}));
            feed(P, rd(PS2_GI_Y, 0, 0, -0.5f));
            CHECK(only_held({3}));
            CHECK(ps2_gamepad_axis(PS2_PAD_AXIS_LEFTX) == -16384);
            n = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
            CHECK(n == 3);
        }
        remove_dev(P);
        remove_dev(Q);
        ps2_gamepad_update(1);
        drain();
        CHECK(P->refs == 0 && Q->refs == 0);
        CHECK(ps2_gamepad_current() == 0);
    }

    // 15. Queue limits.
    ps2_gamepad_select(1);
    set(A, rd());
    ps2_gamepad_update(1);
    drain();
    for (int i = 0; i < 300; i++) {
        set(A, rd((i & 1) ? 0 : PS2_GI_A));
        ps2_gamepad_update(1);
    }
    CHECK(drain().size() == 256);
    {
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        int listed = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
        std::vector<FakeDev *> added;
        for (int i = 0; i < PS2_GAMEPAD_MAX + 1 - listed; i++) {
            FakeDev *d = new_dev();
            added.push_back(d);
            connect(d, "Extra");
        }
        ps2_gamepad_update(1);
        drain();
        CHECK(ps2_gamepad_list(info, PS2_GAMEPAD_MAX) == PS2_GAMEPAD_MAX);
        for (size_t i = 0; i < added.size(); i++)
            CHECK(added[i]->refs == (i + 1 == added.size() ? 0 : 1));
    }

    // 16. Shutdown releases everything, in order; init works again.
    {
        FakeDev *pending = new_dev();
        connect(pending, "Late");  // never reaches an update
        g_order.clear();
        g_record = true;
        ps2_gamepad_shutdown();
        g_record = false;
        CHECK(g_stops == 1 && g_destroys == 1);
        CHECK(!g_order.empty() && g_order.front() == 'S' && g_order.back() == 'D');
        CHECK(g_order.find_first_not_of('R', 1) == g_order.size() - 1);
        for (FakeDev *d : g_all) CHECK(d->refs == 0);
        CHECK(ps2_gamepad_status() == PS2_GAMEPAD_STARTING);
        CHECK(ps2_gamepad_current() == 0);

        ps2_gamepad_init();
        CHECK(g_starts == 2);
        FakeDev *E = new_dev();
        connect(E, "Pad E");
        ps2_gamepad_update(1);
        ps2_pad_info info[PS2_GAMEPAD_MAX];
        int n = ps2_gamepad_list(info, PS2_GAMEPAD_MAX);
        CHECK(n == 1);
        if (n == 1) CHECK(info[0].id == 1);
        ps2_gamepad_shutdown();
        CHECK(E->refs == 0);
    }

    if (g_failed) {
        printf("gamepad test: %d checks failed\n", g_failed);
        return 1;
    }
    printf("gamepad test: all checks passed\n");
    return 0;
}
