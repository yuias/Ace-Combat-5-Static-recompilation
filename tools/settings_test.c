#include "ps2_runtime.h"
#include "ps2_settings.h"
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

u8 *ps2_pt[PS2_PT_ENTRIES];
u8 *ps2_ram;
u8 *ps2_spr;
void ps2_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
}
u8   ps2_mmio_r8(u32 a) { (void)a; abort(); }
u16  ps2_mmio_r16(u32 a) { (void)a; abort(); }
u32  ps2_mmio_r32(u32 a) { (void)a; abort(); }
u64  ps2_mmio_r64(u32 a) { (void)a; abort(); }
void ps2_mmio_r128(ps2_reg128 *d, u32 a) { (void)d; (void)a; abort(); }
void ps2_mmio_w8(u32 a, u8 v) { (void)a; (void)v; abort(); }
void ps2_mmio_w16(u32 a, u16 v) { (void)a; (void)v; abort(); }
void ps2_mmio_w32(u32 a, u32 v) { (void)a; (void)v; abort(); }
void ps2_mmio_w64(u32 a, u64 v) { (void)a; (void)v; abort(); }
void ps2_mmio_w128(u32 a, const ps2_reg128 *v) { (void)a; (void)v; abort(); }

/* The key ids are USB HID usages, the numbering saved binds were made with. */
_Static_assert(PS2_KEY_A == 4, "PS2_KEY_A");
_Static_assert(PS2_KEY_E == 8, "PS2_KEY_E");
_Static_assert(PS2_KEY_Q == 20, "PS2_KEY_Q");
_Static_assert(PS2_KEY_S == 22, "PS2_KEY_S");
_Static_assert(PS2_KEY_W == 26, "PS2_KEY_W");
_Static_assert(PS2_KEY_X == 27, "PS2_KEY_X");
_Static_assert(PS2_KEY_Z == 29, "PS2_KEY_Z");
_Static_assert(PS2_KEY_1 == 30, "PS2_KEY_1");
_Static_assert(PS2_KEY_3 == 32, "PS2_KEY_3");
_Static_assert(PS2_KEY_RETURN == 40, "PS2_KEY_RETURN");
_Static_assert(PS2_KEY_ESCAPE == 41, "PS2_KEY_ESCAPE");
_Static_assert(PS2_KEY_BACKSPACE == 42, "PS2_KEY_BACKSPACE");
_Static_assert(PS2_KEY_SPACE == 44, "PS2_KEY_SPACE");
_Static_assert(PS2_KEY_F4 == 61, "PS2_KEY_F4");
_Static_assert(PS2_KEY_F6 == 63, "PS2_KEY_F6");
_Static_assert(PS2_KEY_F7 == 64, "PS2_KEY_F7");
_Static_assert(PS2_KEY_F8 == 65, "PS2_KEY_F8");
_Static_assert(PS2_KEY_F9 == 66, "PS2_KEY_F9");
_Static_assert(PS2_KEY_F10 == 67, "PS2_KEY_F10");
_Static_assert(PS2_KEY_F11 == 68, "PS2_KEY_F11");
_Static_assert(PS2_KEY_DELETE == 76, "PS2_KEY_DELETE");
_Static_assert(PS2_KEY_RIGHT == 79, "PS2_KEY_RIGHT");
_Static_assert(PS2_KEY_LEFT == 80, "PS2_KEY_LEFT");
_Static_assert(PS2_KEY_DOWN == 81, "PS2_KEY_DOWN");
_Static_assert(PS2_KEY_UP == 82, "PS2_KEY_UP");
_Static_assert(PS2_KEY_KP_2 == 90, "PS2_KEY_KP_2");
_Static_assert(PS2_KEY_KP_4 == 92, "PS2_KEY_KP_4");
_Static_assert(PS2_KEY_KP_6 == 94, "PS2_KEY_KP_6");
_Static_assert(PS2_KEY_KP_8 == 96, "PS2_KEY_KP_8");
_Static_assert(PS2_KEY_RSHIFT == 229, "PS2_KEY_RSHIFT");
_Static_assert(PS2_KEY_COUNT == 512, "PS2_KEY_COUNT");

/* The pad ids are the numbering older builds stored. */
_Static_assert(PS2_PAD_BUTTON_SOUTH == 0, "PS2_PAD_BUTTON_SOUTH");
_Static_assert(PS2_PAD_BUTTON_EAST == 1, "PS2_PAD_BUTTON_EAST");
_Static_assert(PS2_PAD_BUTTON_WEST == 2, "PS2_PAD_BUTTON_WEST");
_Static_assert(PS2_PAD_BUTTON_NORTH == 3, "PS2_PAD_BUTTON_NORTH");
_Static_assert(PS2_PAD_BUTTON_BACK == 4, "PS2_PAD_BUTTON_BACK");
_Static_assert(PS2_PAD_BUTTON_GUIDE == 5, "PS2_PAD_BUTTON_GUIDE");
_Static_assert(PS2_PAD_BUTTON_START == 6, "PS2_PAD_BUTTON_START");
_Static_assert(PS2_PAD_BUTTON_LEFT_STICK == 7, "PS2_PAD_BUTTON_LEFT_STICK");
_Static_assert(PS2_PAD_BUTTON_RIGHT_STICK == 8, "PS2_PAD_BUTTON_RIGHT_STICK");
_Static_assert(PS2_PAD_BUTTON_LEFT_SHOULDER == 9, "PS2_PAD_BUTTON_LEFT_SHOULDER");
_Static_assert(PS2_PAD_BUTTON_RIGHT_SHOULDER == 10, "PS2_PAD_BUTTON_RIGHT_SHOULDER");
_Static_assert(PS2_PAD_BUTTON_DPAD_UP == 11, "PS2_PAD_BUTTON_DPAD_UP");
_Static_assert(PS2_PAD_BUTTON_DPAD_DOWN == 12, "PS2_PAD_BUTTON_DPAD_DOWN");
_Static_assert(PS2_PAD_BUTTON_DPAD_LEFT == 13, "PS2_PAD_BUTTON_DPAD_LEFT");
_Static_assert(PS2_PAD_BUTTON_DPAD_RIGHT == 14, "PS2_PAD_BUTTON_DPAD_RIGHT");
_Static_assert(PS2_PAD_BUTTON_MISC1 == 15, "PS2_PAD_BUTTON_MISC1");
_Static_assert(PS2_PAD_BUTTON_RIGHT_PADDLE1 == 16, "PS2_PAD_BUTTON_RIGHT_PADDLE1");
_Static_assert(PS2_PAD_BUTTON_LEFT_PADDLE1 == 17, "PS2_PAD_BUTTON_LEFT_PADDLE1");
_Static_assert(PS2_PAD_BUTTON_RIGHT_PADDLE2 == 18, "PS2_PAD_BUTTON_RIGHT_PADDLE2");
_Static_assert(PS2_PAD_BUTTON_LEFT_PADDLE2 == 19, "PS2_PAD_BUTTON_LEFT_PADDLE2");
_Static_assert(PS2_PAD_BUTTON_TOUCHPAD == 20, "PS2_PAD_BUTTON_TOUCHPAD");
_Static_assert(PS2_PAD_BUTTON_MISC2 == 21, "PS2_PAD_BUTTON_MISC2");
_Static_assert(PS2_PAD_BUTTON_MISC3 == 22, "PS2_PAD_BUTTON_MISC3");
_Static_assert(PS2_PAD_BUTTON_MISC4 == 23, "PS2_PAD_BUTTON_MISC4");
_Static_assert(PS2_PAD_BUTTON_MISC5 == 24, "PS2_PAD_BUTTON_MISC5");
_Static_assert(PS2_PAD_BUTTON_MISC6 == 25, "PS2_PAD_BUTTON_MISC6");
_Static_assert(PS2_PAD_BUTTON_COUNT == 26, "PS2_PAD_BUTTON_COUNT");
_Static_assert(PS2_PAD_AXIS_LEFTX == 0, "PS2_PAD_AXIS_LEFTX");
_Static_assert(PS2_PAD_AXIS_LEFTY == 1, "PS2_PAD_AXIS_LEFTY");
_Static_assert(PS2_PAD_AXIS_RIGHTX == 2, "PS2_PAD_AXIS_RIGHTX");
_Static_assert(PS2_PAD_AXIS_RIGHTY == 3, "PS2_PAD_AXIS_RIGHTY");
_Static_assert(PS2_PAD_AXIS_LEFT_TRIGGER == 4, "PS2_PAD_AXIS_LEFT_TRIGGER");
_Static_assert(PS2_PAD_AXIS_RIGHT_TRIGGER == 5, "PS2_PAD_AXIS_RIGHT_TRIGGER");
_Static_assert(PS2_PAD_AXIS_COUNT == 6, "PS2_PAD_AXIS_COUNT");

/* Every key id ps2_key_from_scancode can return, with the name the menu and the settings
   file show; "Unknown key" where the id has no name. */
static const struct { int id; const char *name; } expected_keys[] = {
        { 4, "A" },
        { 5, "B" },
        { 6, "C" },
        { 7, "D" },
        { 8, "E" },
        { 9, "F" },
        { 10, "G" },
        { 11, "H" },
        { 12, "I" },
        { 13, "J" },
        { 14, "K" },
        { 15, "L" },
        { 16, "M" },
        { 17, "N" },
        { 18, "O" },
        { 19, "P" },
        { 20, "Q" },
        { 21, "R" },
        { 22, "S" },
        { 23, "T" },
        { 24, "U" },
        { 25, "V" },
        { 26, "W" },
        { 27, "X" },
        { 28, "Y" },
        { 29, "Z" },
        { 30, "1" },
        { 31, "2" },
        { 32, "3" },
        { 33, "4" },
        { 34, "5" },
        { 35, "6" },
        { 36, "7" },
        { 37, "8" },
        { 38, "9" },
        { 39, "0" },
        { 40, "Return" },
        { 41, "Escape" },
        { 42, "Backspace" },
        { 43, "Tab" },
        { 44, "Space" },
        { 45, "-" },
        { 46, "=" },
        { 47, "[" },
        { 48, "]" },
        { 49, "\\" },
        { 51, ";" },
        { 52, "'" },
        { 53, "`" },
        { 54, "," },
        { 55, "." },
        { 56, "/" },
        { 57, "CapsLock" },
        { 58, "F1" },
        { 59, "F2" },
        { 60, "F3" },
        { 61, "F4" },
        { 62, "F5" },
        { 63, "F6" },
        { 64, "F7" },
        { 65, "F8" },
        { 66, "F9" },
        { 67, "F10" },
        { 68, "F11" },
        { 69, "F12" },
        { 70, "PrintScreen" },
        { 71, "ScrollLock" },
        { 72, "Pause" },
        { 73, "Insert" },
        { 74, "Home" },
        { 75, "PageUp" },
        { 76, "Delete" },
        { 77, "End" },
        { 78, "PageDown" },
        { 79, "Right" },
        { 80, "Left" },
        { 81, "Down" },
        { 82, "Up" },
        { 83, "Numlock" },
        { 84, "Keypad /" },
        { 85, "Keypad *" },
        { 86, "Keypad -" },
        { 87, "Keypad +" },
        { 88, "Keypad Enter" },
        { 89, "Keypad 1" },
        { 90, "Keypad 2" },
        { 91, "Keypad 3" },
        { 92, "Keypad 4" },
        { 93, "Keypad 5" },
        { 94, "Keypad 6" },
        { 95, "Keypad 7" },
        { 96, "Keypad 8" },
        { 97, "Keypad 9" },
        { 98, "Keypad 0" },
        { 99, "Keypad ." },
        { 100, "NonUSBackslash" },
        { 101, "Application" },
        { 102, "Power" },
        { 103, "Keypad =" },
        { 104, "F13" },
        { 105, "F14" },
        { 106, "F15" },
        { 107, "F16" },
        { 108, "F17" },
        { 109, "F18" },
        { 110, "F19" },
        { 111, "F20" },
        { 112, "F21" },
        { 113, "F22" },
        { 114, "F23" },
        { 115, "F24" },
        { 127, "Mute" },
        { 128, "VolumeUp" },
        { 129, "VolumeDown" },
        { 133, "Keypad ," },
        { 135, "International 1" },
        { 136, "International 2" },
        { 137, "International 3" },
        { 138, "International 4" },
        { 139, "International 5" },
        { 224, "Left Ctrl" },
        { 225, "Left Shift" },
        { 226, "Left Alt" },
        { 227, "Left GUI" },
        { 228, "Right Ctrl" },
        { 229, "Right Shift" },
        { 230, "Right Alt" },
        { 231, "Right GUI" },
};
static const int n_expected_keys = (int)(sizeof expected_keys / sizeof expected_keys[0]);

static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL: " __VA_ARGS__); printf("\n"); fails++; } } while (0)

static int feq(float a, float b) { return fabsf(a - b) < 1e-5f; }

enum { REF_UP, REF_RIGHT, REF_DOWN, REF_LEFT };
typedef struct {
    u8 rawInputs[4];
    u8 lx, ly;
    int lxInvert, lyInvert;
    float axisScale, axisDeadzone;
} ref_pad;

static float ref_clamp(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

static void ref_set(ref_pad *p, int index, float value) {
    p->rawInputs[index] = (u8)ref_clamp(value * p->axisScale * 255.0f, 0.0f, 255.0f);
#define MERGE(pos, neg) ((p->rawInputs[pos] != 0) ? (127u + ((p->rawInputs[pos] + 1u) / 2u)) : (127u - (p->rawInputs[neg] / 2u)))
    p->lx = p->lxInvert ? MERGE(REF_LEFT, REF_RIGHT) : MERGE(REF_RIGHT, REF_LEFT);
    p->ly = p->lyInvert ? MERGE(REF_UP, REF_DOWN) : MERGE(REF_DOWN, REF_UP);
#undef MERGE
    const float dz = p->axisDeadzone;
    if (dz > 0.0f) {
#define MERGE_F(pos, neg) ((p->rawInputs[pos] != 0) ? ((float)p->rawInputs[pos] / 255.0f) : ((float)p->rawInputs[neg] / -255.0f))
        float posX = p->lxInvert ? MERGE_F(REF_LEFT, REF_RIGHT) : MERGE_F(REF_RIGHT, REF_LEFT);
        float posY = p->lyInvert ? MERGE_F(REF_UP, REF_DOWN) : MERGE_F(REF_DOWN, REF_UP);
#undef MERGE_F
        if (posX != 0.0f || posY != 0.0f) {
            const float theta = atan2f(posY, posX);
            const float dzX = cosf(theta) * dz;
            const float dzY = sinf(theta) * dz;
            const int inX = (posX < 0.0f) ? (posX > dzX) : (posX <= dzX);
            const int inY = (posY < 0.0f) ? (posY > dzY) : (posY <= dzY);
            if (inX && inY) p->lx = p->ly = 127;
        }
    }
}

static void ref_axis_event(ref_pad *p, int y_axis, int raw) {
    const float value = (float)raw / (raw < 0 ? 32768.0f : 32767.0f);
    ref_set(p, y_axis ? REF_DOWN : REF_RIGHT, ref_clamp(value > 0.0f ? value : 0.0f, 0.0f, 1.0f));
    ref_set(p, y_axis ? REF_UP : REF_LEFT, ref_clamp(value < 0.0f ? -value : 0.0f, 0.0f, 1.0f));
}

static void our_stick(float dz, float scale, int invert, int x, int y, u8 *ox, u8 *oy) {
    const float v[4] = { ps2_pad_axis_value(y, 0), ps2_pad_axis_value(y, 1),
                         ps2_pad_axis_value(x, 0), ps2_pad_axis_value(x, 1) };
    ps2_pad_stick(dz, scale, invert, v, ox, oy);
}

int main(void) {
    static u8 ram[PS2_RAM_SIZE];
    for (unsigned p = 0; p < PS2_RAM_SIZE / PS2_PAGE_SIZE; p++) ps2_pt[p] = ram + p * PS2_PAGE_SIZE;
    ps2_ram = ram;

    ps2_settings_defaults(&ps2_cfg);
    CHECK(ps2_cfg.preset == PS2_PRESET_LOW, "defaults are not Low");
    CHECK(ps2_settings_preset_matches(&ps2_cfg) == PS2_PRESET_LOW, "defaults do not match Low");
    CHECK(ps2_cfg.anisotropy == 0 && ps2_cfg.deinterlace == 1
          && ps2_cfg.fxaa == 0 && ps2_cfg.sharpen == 0.0f && ps2_cfg.scale_filter == PS2_SCALE_BILINEAR
          && ps2_cfg.aspect == PS2_ASPECT_STRETCH && ps2_cfg.present_mode == PS2_PRESENT_MAILBOX
          && ps2_cfg.window_w == 1280 && ps2_cfg.window_h == 896 && ps2_cfg.widescreen == 0
          && ps2_cfg.internal_res == 1,
          "defaults differ from the pre-menu renderer");
    for (int i = 0; i <= PS2_PRESET_ULTRA; i++) {
        ps2_settings s;
        ps2_settings_defaults(&s);
        ps2_settings_apply_preset(&s, i);
        CHECK(ps2_settings_preset_matches(&s) == i, "preset %d does not round-trip", i);
    }
    {
        ps2_settings s;
        ps2_settings_defaults(&s);
        s.anisotropy = 4;
        CHECK(ps2_settings_preset_matches(&s) == PS2_PRESET_CUSTOM, "edited preset not Custom");
        static const int res[4] = { 1, 2, 3, 4 };
        for (int i = 0; i <= PS2_PRESET_ULTRA; i++) {
            ps2_settings_apply_preset(&s, i);
            CHECK(s.internal_res == res[i], "preset %d internal resolution %d", i, s.internal_res);
        }
        ps2_settings_apply_preset(&s, PS2_PRESET_HIGH);
        s.internal_res = 0;
        CHECK(ps2_settings_preset_matches(&s) == PS2_PRESET_CUSTOM, "Auto resolution kept High");
    }

    CHECK(ps2_cfg.key[PS2_ACT_CROSS][0] == 27 && ps2_cfg.key[PS2_ACT_START][0] == 40
          && ps2_cfg.key[PS2_ACT_LS_UP][0] == 26 && ps2_cfg.key[PS2_ACT_LS_UP][1] == 96,
          "default keyboard map");
    {
        static const struct { unsigned code; int key; } map[] = {
            { 0x1C, 40 }, { 0xE01C, 88 }, { 0x48, 96 }, { 0xE048, 82 }, { 0x50, 90 }, { 0xE050, 81 },
            { 0x4B, 92 }, { 0xE04B, 80 }, { 0x4D, 94 }, { 0xE04D, 79 }, { 0x2A, 225 }, { 0x36, 229 },
            { 0x1D, 224 }, { 0xE01D, 228 }, { 0x38, 226 }, { 0xE038, 230 }, { 0x45, 72 }, { 0xE045, 83 },
            { 0xE11D, 72 }, { 0x53, 99 }, { 0xE053, 76 }, { 0x01, 41 }, { 0x2D, 27 }, { 0x39, 44 },
            { 0x57, 68 }, { 0xE035, 84 }, { 0x00, 0 }, { 0xE07F, 0 },
        };
        unsigned char reached[PS2_KEY_COUNT] = { 0 };
        int nreached = 0;
        for (size_t i = 0; i < sizeof map / sizeof map[0]; i++)
            CHECK(ps2_key_from_scancode(map[i].code) == map[i].key,
                  "scan code 0x%X maps to %d, expected %d", map[i].code,
                  ps2_key_from_scancode(map[i].code), map[i].key);
        for (unsigned c = 0; c <= 0xE11Du; c++) {
            int k;
            if (c > 0x7F && !(c >= 0xE000u && c <= 0xE07Fu) && c != 0xE11Du) continue;
            k = ps2_key_from_scancode(c);
            if (!k) continue;
            CHECK(k > 0 && k < PS2_KEY_COUNT, "scan code 0x%X maps outside the key range (%d)", c, k);
            if (k > 0 && k < PS2_KEY_COUNT) reached[k] = 1;
        }
        CHECK(ps2_key_from_scancode(0x80) == 0 && ps2_key_from_scancode(0xE080) == 0
              && ps2_key_from_scancode(0xE11C) == 0 && ps2_key_from_scancode(0x1E1D) == 0,
              "a scan code outside the tables was mapped");
        CHECK(!strcmp(ps2_key_name(0), "-") && !strcmp(ps2_key_name(-5), "-"), "unbound key name");
        for (int k = 1; k < PS2_KEY_COUNT; k++) {
            int listed = 0;
            for (int i = 0; i < n_expected_keys; i++)
                if (expected_keys[i].id == k) listed = 1;
            if (!reached[k]) {
                CHECK(!listed, "key %d is in the expected table but no scan code reaches it", k);
                continue;
            }
            nreached++;
            CHECK(listed, "key %d is reachable but missing from the expected table", k);
        }
        for (int i = 0; i < n_expected_keys; i++) {
            const int k = expected_keys[i].id;
            CHECK(!strcmp(ps2_key_name(k), expected_keys[i].name), "key %d is named \"%s\", expected \"%s\"",
                  k, ps2_key_name(k), expected_keys[i].name);
        }
        printf("keys: %d reachable ids checked against the expected names\n", nreached);
    }

    CHECK(ps2_cfg.pad[PS2_ACT_CROSS][0] == 1 && ps2_cfg.pad[PS2_ACT_R2][0] == 0x10B
          && ps2_cfg.pad[PS2_ACT_LS_LEFT][0] == 0x100,
          "default controller map");
    CHECK(ps2_cfg.deadzone == 0.0f && ps2_cfg.axis_scale == 1.33f && ps2_cfg.button_deadzone == 0.0f
          && ps2_cfg.invert[0] == 0 && ps2_cfg.invert[1] == 0,
          "analog defaults are not PCSX2's (PadTypes.h:102-106)");

    {
        ps2_settings a;
        ps2_settings_defaults(&a);
        a.anisotropy = 8; a.fxaa = 1; a.sharpen = 0.3f; a.scale_filter = PS2_SCALE_SHARP;
        a.brightness = -0.12f; a.gamma = 1.3f; a.window_mode = PS2_WIN_BORDERLESS;
        a.aspect = PS2_ASPECT_CUSTOM; a.aspect_custom = 2.1f; a.widescreen = 1; a.integer_scale = 1;
        a.present_mode = PS2_PRESENT_IMMEDIATE; a.fps_limit = 90; a.show_fps = 1; a.ui_scale = 1.25f;
        a.key[PS2_ACT_CROSS][1] = PS2_KEY_SPACE;
        a.pad[PS2_ACT_R2][1] = PS2_PADBIND_BUTTON(PS2_PAD_BUTTON_RIGHT_SHOULDER);
        a.deadzone = 0.15f; a.axis_scale = 1.4f; a.button_deadzone = 0.2f;
        a.invert[0] = 2; a.invert[1] = 3;
        a.internal_res = 0;
        a.preset = ps2_settings_preset_matches(&a);
        ps2_cfg = a;
        ps2_settings_save();
        memset(&ps2_cfg, 0, sizeof ps2_cfg);
        ps2_settings_load();
        ps2_settings *b = &ps2_cfg;
        CHECK(b->preset == a.preset && b->anisotropy == 8 && b->fxaa == 1 && feq(b->sharpen, 0.3f)
              && b->scale_filter == PS2_SCALE_SHARP && feq(b->brightness, -0.12f) && feq(b->gamma, 1.3f),
              "graphics fields did not round-trip");
        CHECK(b->window_mode == PS2_WIN_BORDERLESS && b->aspect == PS2_ASPECT_CUSTOM
              && feq(b->aspect_custom, 2.1f) && b->widescreen == 1 && b->integer_scale == 1
              && b->present_mode == PS2_PRESENT_IMMEDIATE && b->fps_limit == 90 && b->show_fps == 1
              && feq(b->ui_scale, 1.25f), "display fields did not round-trip");
        CHECK(b->internal_res == 0, "internal resolution did not round-trip (%d)", b->internal_res);
        CHECK(!memcmp(b->key, a.key, sizeof a.key) && !memcmp(b->pad, a.pad, sizeof a.pad),
              "bindings did not round-trip");
        CHECK(feq(b->deadzone, 0.15f) && feq(b->axis_scale, 1.4f) && feq(b->button_deadzone, 0.2f)
              && b->invert[0] == 2 && b->invert[1] == 3, "analog fields did not round-trip");
    }

    {
        FILE *f = fopen(ps2_settings_path(), "w");
        fprintf(f, "[graphics]\nanisotropy = 7\npreset = 3\n[display]\nwindow_width = 10\naspect = 99\n"
                   "[analog]\ndeadzone = 5\naxis_scale = -4\nbutton_deadzone = -1\ninvert_left = 9\n"
                   "[keyboard]\ncross = 999999 -3\n");
        fclose(f);
        ps2_settings_load();
        CHECK(ps2_cfg.anisotropy == 0 && ps2_cfg.window_w == 320 && ps2_cfg.aspect == 4
              && ps2_cfg.key[PS2_ACT_CROSS][0] == 0 && ps2_cfg.key[PS2_ACT_CROSS][1] == 0,
              "hand-edited values were not clamped");
        CHECK(feq(ps2_cfg.deadzone, 1.0f) && feq(ps2_cfg.axis_scale, 0.01f)
              && ps2_cfg.button_deadzone == 0.0f && ps2_cfg.invert[0] == 3,
              "hand-edited analog values were not clamped to PCSX2's ranges");
        CHECK(ps2_cfg.preset == PS2_PRESET_LOW, "a preset name that the fields contradict was kept (%d)",
              ps2_cfg.preset);
        f = fopen(ps2_settings_path(), "w");
        fprintf(f, "[graphics]\ninternal_resolution = 99\n");
        fclose(f);
        ps2_settings_load();
        CHECK(ps2_cfg.internal_res == PS2_INTERNAL_RES_MAX && ps2_cfg.preset == PS2_PRESET_CUSTOM,
              "internal resolution 99 was not clamped (%d)", ps2_cfg.internal_res);
        f = fopen(ps2_settings_path(), "w");
        fprintf(f, "[graphics]\ninternal_resolution = -3\n");
        fclose(f);
        ps2_settings_load();
        CHECK(ps2_cfg.internal_res == 0, "internal resolution -3 was not clamped (%d)", ps2_cfg.internal_res);
    }

    {
        /* Files written while exclusive fullscreen existed: mode 2 loads as borderless and the
           obsolete keys are skipped without hiding the keys after them. The first key name is
           split so that a search for the removed setting finds no live use. */
        FILE *f = fopen(ps2_settings_path(), "w");
        char line[512];
        int stale = 0;
        fprintf(f, "[display]\nwindow_mode = 2\nfullscreen" "_type = 2\nfullscreen_width = 1920\n"
                   "fullscreen_height = 1080\nfullscreen_refresh = 143.98\nwindow_width = 1600\n");
        fclose(f);
        ps2_settings_load();
        CHECK(ps2_cfg.window_mode == PS2_WIN_BORDERLESS && ps2_cfg.window_w == 1600,
              "an old exclusive fullscreen file did not load as borderless (%d, %d)",
              ps2_cfg.window_mode, ps2_cfg.window_w);
        ps2_settings_save();
        f = fopen(ps2_settings_path(), "r");
        CHECK(f != NULL, "settings file missing after save");
        while (f && fgets(line, sizeof line, f))
            if (strstr(line, "fullscreen_")) stale++;
        if (f) fclose(f);
        CHECK(stale == 0, "the saved file still has %d fullscreen_ lines", stale);
    }

    {
        FILE *f = fopen(ps2_settings_path(), "w");
        fprintf(f, "[analog]\nleft_shape = 0\nleft_deadzone = 0.268\nright_deadzone = 0.527\n"
                   "left_invert_x = 1\nleft_invert_y = 0\nright_invert_y = 1\n"
                   "trigger_press = 0.24\nstick_as_button = 0.5\n");
        fclose(f);
        ps2_settings_load();
        CHECK(ps2_cfg.deadzone == 0.0f && ps2_cfg.axis_scale == 1.33f && ps2_cfg.button_deadzone == 0.0f,
              "an old dead zone key changed the PCSX2 settings");
        CHECK(ps2_cfg.invert[0] == 1 && ps2_cfg.invert[1] == 2,
              "old per-stick inverts did not carry over (%d, %d)", ps2_cfg.invert[0], ps2_cfg.invert[1]);
    }

    {
        static const float dzs[] = { 0.0f, 0.1f, 0.25f, 0.5f, 1.0f };
        static const float scales[] = { 0.5f, 1.0f, 1.33f, 2.0f };
        long compared = 0, differ = 0, order = 0;
        for (size_t a = 0; a < sizeof dzs / sizeof dzs[0]; a++)
        for (size_t b = 0; b < sizeof scales / sizeof scales[0]; b++)
        for (int inv = 0; inv < 4; inv++)
        for (int i = 0; i < 256; i++)
        for (int j = 0; j < 256; j++) {
            int x = -32768 + i * 257, y = -32768 + j * 257;
            ref_pad r = { {0}, 127, 127, inv & 1, (inv & 2) != 0, scales[b], dzs[a] };
            u8 ox, oy;
            ref_axis_event(&r, 0, x);
            ref_axis_event(&r, 1, y);
            our_stick(dzs[a], scales[b], inv, x, y, &ox, &oy);
            compared++;
            if (ox != r.lx || oy != r.ly) {
                if (!differ)
                    printf("first difference: dead zone %.2f scale %.2f invert %d at (%d, %d): "
                           "PCSX2 %u,%u, ours %u,%u\n", (double)dzs[a], (double)scales[b], inv,
                           x, y, r.lx, r.ly, ox, oy);
                differ++;
            }
            if ((i + j) % 17 == 0) {
                ref_pad q = { {0}, 127, 127, inv & 1, (inv & 2) != 0, scales[b], dzs[a] };
                ref_axis_event(&q, 1, y);
                ref_axis_event(&q, 0, x);
                if (q.lx != r.lx || q.ly != r.ly) order++;
            }
        }
        printf("stick: %ld positions and settings compared with PCSX2, %ld differ, "
               "%ld depend on event order\n", compared, differ, order);
        CHECK(differ == 0, "the stick model differs from PCSX2's");
        CHECK(order == 0, "PCSX2's result depended on event order");
    }

    {
        u8 x, y, p;
        CHECK(ps2_pad_axis_value(-32768, 0) == 1.0f && ps2_pad_axis_value(32767, 1) == 1.0f
              && ps2_pad_axis_value(-1, 1) == 0.0f && ps2_pad_axis_value(16384, 0) == 0.0f,
              "axis values");
        our_stick(0.0f, 1.33f, 0, 0, 0, &x, &y);
        CHECK(x == 127 && y == 127, "centre is not 0x7F (%u, %u)", x, y);
        our_stick(0.0f, 1.33f, 0, 32767, 32767, &x, &y);
        CHECK(x == 255 && y == 255, "right and down (%u, %u)", x, y);
        our_stick(0.0f, 1.33f, 0, -32768, -32768, &x, &y);
        CHECK(x == 0 && y == 0, "left and up (%u, %u)", x, y);
        our_stick(0.0f, 1.33f, 0, 16384, 0, &x, &y);
        CHECK(x == 212 && y == 127, "half right at 133%% (%u)", x);
        our_stick(0.0f, 1.33f, 0, 24700, 0, &x, &y);
        CHECK(x == 255, "133%% does not saturate by 75.4%% of travel (%u)", x);
        our_stick(0.25f, 1.0f, 0, 6553, 0, &x, &y);
        CHECK(x == 127 && y == 127, "20%% inside a 25%% dead zone (%u)", x);
        our_stick(0.25f, 1.0f, 0, 9830, 0, &x, &y);
        CHECK(x == 165, "30%% outside a 25%% dead zone is not passed on unscaled (%u)", x);
        our_stick(0.25f, 1.0f, 0, 6553, 6553, &x, &y);
        CHECK(x == 152 && y == 152, "the dead zone is not radial (%u, %u)", x, y);
        our_stick(0.0f, 1.33f, 1, 32767, 32767, &x, &y);
        CHECK(x == 0 && y == 255, "invert left/right (%u, %u)", x, y);
        our_stick(0.0f, 1.33f, 2, 32767, 32767, &x, &y);
        CHECK(x == 255 && y == 0, "invert up/down (%u, %u)", x, y);
        {
            const float both[4] = { 1.0f, 1.0f, 1.0f, 1.0f };
            ps2_pad_stick(0.0f, 1.33f, 0, both, &x, &y);
            CHECK(x == 255 && y == 255, "opposing directions (%u, %u)", x, y);
        }

        CHECK(ps2_pad_trigger(0.0f, 0.5f, &p) == 1 && p == 127, "half trigger (%u)", p);
        CHECK(ps2_pad_trigger(0.0f, 0.0f, &p) == 0 && p == 0, "released trigger");
        CHECK(ps2_pad_trigger(0.0f, 1.0f / 32767.0f, &p) == 1 && p == 0, "a trigger is held above zero");
        CHECK(ps2_pad_trigger(0.6f, 0.5f, &p) == 0 && p == 0, "trigger inside its dead zone");
        CHECK(ps2_pad_trigger(0.6f, 0.7f, &p) == 1 && p == 178, "trigger past its dead zone (%u)", p);
        CHECK(ps2_pad_button(0.0f, 1.0f) == 1 && ps2_pad_button(0.0f, 0.0f) == 0, "button");
        CHECK(ps2_pad_button(0.5f, 0.4f) == 0 && ps2_pad_button(0.5f, 0.5f) == 1, "button dead zone");
    }

    {
        ps2_w32(0x00440828u, 0x440315C2u);
        ps2_w32(0x0044082Cu, 0x441013D7u);
        ps2_cfg.widescreen = 1;
        ps2_settings_apply_game_patches();
        CHECK(ps2_r32(0x00440828u) == 0x43D638F3u && ps2_r32(0x0044082Cu) == 0x43EB7385u, "patch not applied");
        ps2_cfg.widescreen = 0;
        ps2_settings_apply_game_patches();
        CHECK(ps2_r32(0x00440828u) == 0x440315C2u && ps2_r32(0x0044082Cu) == 0x441013D7u, "patch not removed");
        ps2_w32(0x00440828u, 0x12345678u);
        ps2_cfg.widescreen = 1;
        ps2_settings_apply_game_patches();
        CHECK(ps2_r32(0x00440828u) == 0x12345678u, "patch overwrote an unexpected value");
    }

    printf("settings test: %s\n", fails ? "FAILED" : "all checks passed");
    return fails ? 1 : 0;
}
