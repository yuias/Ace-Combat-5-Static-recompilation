// Gamepad hub: keeps the device table and the selected pad, turns the
// backend's readings into the button/axis ids and value ranges the settings
// file and the menu use, and queues events. The device source (GameInput, or
// a fake in the test) is behind ps2_gamepad_backend.h.
#include "ps2_gamepad.h"
#include "ps2_gamepad_backend.h"
#include "ps2_settings.h"
#include "ps2_os.h"
#include <atomic>
#include <cstring>
#include <cstdio>

extern "C" void ps2_log(const char *fmt, ...);

namespace {

const int PENDING_MAX = 64;
const int EVENT_MAX = 256;

struct Pending {
    void *dev;
    int   connected;
    char  name[PS2_GAMEPAD_NAME];
};

struct Device {
    uint32_t id;
    void    *dev;
    char     name[PS2_GAMEPAD_NAME];
};

std::atomic<int> g_status(PS2_GAMEPAD_STARTING);
bool g_inited;

// Backend threads append here; the renderer thread drains it in update.
ps2_mutex_t g_lock = PS2_MUTEX_INIT;
Pending g_pending[PENDING_MAX];
int g_pending_n;

// Renderer thread only from here on.
Device g_devs[PS2_GAMEPAD_MAX];
int g_dev_n;
uint32_t g_next_id = 1;
int g_sel = -1;

uint32_t g_buttons;                 // bit b = button id b
int g_axis[PS2_PAD_AXIS_COUNT];
// The next successful reading is adopted without events, so a newly selected
// or re-focused pad does not report its current state as presses.
bool g_baseline;

ps2_pad_event g_events[EVENT_MAX];
int g_ev_head, g_ev_n;

struct ButtonMap { uint32_t bit; int id; };
const ButtonMap k_button_map[] = {
    { PS2_GI_A, PS2_PAD_BUTTON_SOUTH },
    { PS2_GI_B, PS2_PAD_BUTTON_EAST },
    { PS2_GI_X, PS2_PAD_BUTTON_WEST },
    { PS2_GI_Y, PS2_PAD_BUTTON_NORTH },
    { PS2_GI_VIEW, PS2_PAD_BUTTON_BACK },
    { PS2_GI_MENU, PS2_PAD_BUTTON_START },
    { PS2_GI_LEFT_THUMBSTICK, PS2_PAD_BUTTON_LEFT_STICK },
    { PS2_GI_RIGHT_THUMBSTICK, PS2_PAD_BUTTON_RIGHT_STICK },
    { PS2_GI_LEFT_SHOULDER, PS2_PAD_BUTTON_LEFT_SHOULDER },
    { PS2_GI_RIGHT_SHOULDER, PS2_PAD_BUTTON_RIGHT_SHOULDER },
    { PS2_GI_DPAD_UP, PS2_PAD_BUTTON_DPAD_UP },
    { PS2_GI_DPAD_DOWN, PS2_PAD_BUTTON_DPAD_DOWN },
    { PS2_GI_DPAD_LEFT, PS2_PAD_BUTTON_DPAD_LEFT },
    { PS2_GI_DPAD_RIGHT, PS2_PAD_BUTTON_DPAD_RIGHT },
    { PS2_GI_PADDLE_RIGHT1, PS2_PAD_BUTTON_RIGHT_PADDLE1 },
    { PS2_GI_PADDLE_LEFT1, PS2_PAD_BUTTON_LEFT_PADDLE1 },
    { PS2_GI_PADDLE_RIGHT2, PS2_PAD_BUTTON_RIGHT_PADDLE2 },
    { PS2_GI_PADDLE_LEFT2, PS2_PAD_BUTTON_LEFT_PADDLE2 },
};

// NaN reads as 0. Negative values scale by 32768 so full deflection reaches
// -32768, as the old stored ranges did.
int stick(float v) {
    if (!(v == v)) v = 0;
    v = v < -1 ? -1 : v > 1 ? 1 : v;
    return v >= 0 ? (int)(v * 32767.0f + 0.5f) : -(int)(-v * 32768.0f + 0.5f);
}

int trigger(float v) {
    if (!(v == v)) v = 0;
    v = v < 0 ? 0 : v > 1 ? 1 : v;
    return (int)(v * 32767.0f + 0.5f);
}

void convert(const ps2_gi_reading &r, uint32_t *buttons, int *axis) {
    uint32_t b = 0;
    for (const ButtonMap &m : k_button_map)
        if (r.buttons & m.bit) b |= 1u << m.id;
    *buttons = b;
    axis[PS2_PAD_AXIS_LEFTX] = stick(r.left_x);
    axis[PS2_PAD_AXIS_LEFTY] = stick(-r.left_y);        // +Y down
    axis[PS2_PAD_AXIS_RIGHTX] = stick(r.right_x);
    axis[PS2_PAD_AXIS_RIGHTY] = stick(-r.right_y);
    axis[PS2_PAD_AXIS_LEFT_TRIGGER] = trigger(r.left_trigger);
    axis[PS2_PAD_AXIS_RIGHT_TRIGGER] = trigger(r.right_trigger);
}

void neutral() {
    g_buttons = 0;
    for (int &a : g_axis) a = 0;
}

void push_event(int type, uint32_t id, int button, int axis, int value) {
    if (g_ev_n >= EVENT_MAX) return;
    ps2_pad_event &e = g_events[(g_ev_head + g_ev_n) % EVENT_MAX];
    e.type = type;
    e.id = id;
    e.button = button;
    e.axis = axis;
    e.value = value;
    g_ev_n++;
}

int find_dev(void *dev) {
    for (int i = 0; i < g_dev_n; i++)
        if (g_devs[i].dev == dev) return i;
    return -1;
}

void select_index(int i) {
    g_sel = i;
    neutral();
    g_baseline = true;
}

void reset_state() {
    g_status.store(PS2_GAMEPAD_STARTING);
    ps2_mutex_lock(&g_lock);
    g_pending_n = 0;
    ps2_mutex_unlock(&g_lock);
    g_dev_n = 0;
    g_next_id = 1;
    g_sel = -1;
    neutral();
    g_baseline = false;
    g_ev_head = g_ev_n = 0;
}

}  // namespace

void ps2_gamepad_init(void) {
    if (g_inited) return;
    reset_state();
    g_inited = true;
    ps2_gpb_start();
}

void ps2_gamepad_shutdown(void) {
    if (!g_inited) return;
    // No hub lock is held: stop waits for callbacks that take it.
    ps2_gpb_stop();
    for (int i = 0; i < g_dev_n; i++) ps2_gpb_release(g_devs[i].dev);
    // Release outside the lock, as update does.
    void *unclaimed[PENDING_MAX];
    int unclaimed_n = 0;
    ps2_mutex_lock(&g_lock);
    for (int i = 0; i < g_pending_n; i++)
        if (g_pending[i].connected) unclaimed[unclaimed_n++] = g_pending[i].dev;
    ps2_mutex_unlock(&g_lock);
    for (int i = 0; i < unclaimed_n; i++) ps2_gpb_release(unclaimed[i]);
    // The devices must go before the backend object they came from.
    ps2_gpb_destroy();
    reset_state();
    g_inited = false;
}

int ps2_gamepad_status(void) {
    return g_status.load();
}

void ps2_gph_status(int status) {
    g_status.store(status);
}

void ps2_gph_device(void *dev, int connected, const char *name) {
    Pending p;
    p.dev = dev;
    p.connected = connected;
    p.name[0] = 0;
    if (connected) {
        if (!name || !name[0]) name = "Controller";
        size_t len = strlen(name);
        if (len > PS2_GAMEPAD_NAME - 1) {
            len = PS2_GAMEPAD_NAME - 1;
            // Do not cut a UTF-8 sequence in half.
            while (len > 0 && ((unsigned char)name[len] & 0xC0) == 0x80) len--;
            // Nothing valid left: the name is never empty.
            if (len == 0) {
                name = "Controller";
                len = strlen(name);
            }
        }
        memcpy(p.name, name, len);
        p.name[len] = 0;
    }
    bool dropped = false;
    ps2_mutex_lock(&g_lock);
    if (g_pending_n < PENDING_MAX) g_pending[g_pending_n++] = p;
    else dropped = true;
    ps2_mutex_unlock(&g_lock);
    // A lost removal is harmless; a lost connect must not leak its reference.
    if (dropped && connected) ps2_gpb_release(dev);
}

void ps2_gamepad_update(int focused) {
    if (!g_inited) return;

    Pending local[PENDING_MAX];
    int n;
    ps2_mutex_lock(&g_lock);
    n = g_pending_n;
    memcpy(local, g_pending, (size_t)n * sizeof local[0]);
    g_pending_n = 0;
    ps2_mutex_unlock(&g_lock);

    for (int k = 0; k < n; k++) {
        Pending &p = local[k];
        if (p.connected) {
            if (find_dev(p.dev) >= 0 || g_dev_n >= PS2_GAMEPAD_MAX) {
                ps2_gpb_release(p.dev);
                continue;
            }
            Device &d = g_devs[g_dev_n++];
            d.id = g_next_id++;
            d.dev = p.dev;
            memcpy(d.name, p.name, sizeof d.name);
            push_event(PS2_PEV_ADDED, d.id, 0, 0, 0);
            if (g_sel < 0) {
                select_index(g_dev_n - 1);
                ps2_log("pad: %s connected", d.name);
            }
        } else {
            int i = find_dev(p.dev);
            if (i < 0) continue;
            push_event(PS2_PEV_REMOVED, g_devs[i].id, 0, 0, 0);
            if (i == g_sel) {
                ps2_log("pad: %s disconnected", g_devs[i].name);
                g_sel = -1;
                neutral();
            } else if (g_sel > i) {
                g_sel--;
            }
            void *dev = g_devs[i].dev;
            for (int j = i; j + 1 < g_dev_n; j++) g_devs[j] = g_devs[j + 1];
            g_dev_n--;
            ps2_gpb_release(dev);
        }
    }

    if (g_sel < 0) {
        neutral();
        return;
    }
    if (!focused) {
        neutral();
        g_baseline = true;
        return;
    }

    ps2_gi_reading r;
    if (!ps2_gpb_read(g_devs[g_sel].dev, &r)) return;

    uint32_t buttons;
    int axis[PS2_PAD_AXIS_COUNT];
    convert(r, &buttons, axis);
    if (g_baseline) {
        g_baseline = false;
    } else {
        uint32_t id = g_devs[g_sel].id;
        for (int b = 0; b < PS2_PAD_BUTTON_COUNT; b++) {
            uint32_t was = (g_buttons >> b) & 1u, now = (buttons >> b) & 1u;
            if (was != now)
                push_event(now ? PS2_PEV_BUTTON_DOWN : PS2_PEV_BUTTON_UP, id, b, 0, 0);
        }
        for (int a = 0; a < PS2_PAD_AXIS_COUNT; a++)
            if (g_axis[a] != axis[a]) push_event(PS2_PEV_AXIS, id, 0, a, axis[a]);
    }
    g_buttons = buttons;
    for (int a = 0; a < PS2_PAD_AXIS_COUNT; a++) g_axis[a] = axis[a];
}

int ps2_gamepad_next_event(ps2_pad_event *ev) {
    if (!g_inited || g_ev_n == 0) return 0;
    *ev = g_events[g_ev_head];
    g_ev_head = (g_ev_head + 1) % EVENT_MAX;
    g_ev_n--;
    return 1;
}

int ps2_gamepad_list(ps2_pad_info *out, int max) {
    if (!g_inited) return 0;
    int n = g_dev_n < max ? g_dev_n : max;
    for (int i = 0; i < n; i++) {
        out[i].id = g_devs[i].id;
        memcpy(out[i].name, g_devs[i].name, sizeof out[i].name);
    }
    return n;
}

uint32_t ps2_gamepad_current(void) {
    return g_inited && g_sel >= 0 ? g_devs[g_sel].id : 0;
}

const char *ps2_gamepad_current_name(void) {
    return g_inited && g_sel >= 0 ? g_devs[g_sel].name : nullptr;
}

void ps2_gamepad_select(uint32_t id) {
    if (!g_inited || id == 0) return;
    for (int i = 0; i < g_dev_n; i++) {
        if (g_devs[i].id != id) continue;
        if (i == g_sel) return;
        select_index(i);
        ps2_log("pad: %s selected", g_devs[i].name);
        return;
    }
}

int ps2_gamepad_button(int button) {
    if (!g_inited || g_sel < 0 || button < 0 || button >= PS2_PAD_BUTTON_COUNT) return 0;
    return (int)((g_buttons >> button) & 1u);
}

int ps2_gamepad_axis(int axis) {
    if (!g_inited || g_sel < 0 || axis < 0 || axis >= PS2_PAD_AXIS_COUNT) return 0;
    return g_axis[axis];
}
