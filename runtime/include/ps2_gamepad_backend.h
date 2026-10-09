#ifndef PS2_GAMEPAD_BACKEND_H
#define PS2_GAMEPAD_BACKEND_H
// Between the gamepad hub (ps2_gamepad.cpp) and its device backend
// (ps2_gamepad_gi.cpp, or a fake in the hub test). No windows.h here.
#ifndef __cplusplus
#error ps2_gamepad_backend.h is C++ only
#endif
#include <stdint.h>
#include "ps2_gamepad.h"  // PS2_GAMEPAD_READY / _UNAVAILABLE, name size

// GameInputGamepadButtons bit values; ps2_gamepad_gi.cpp static_asserts each
// one against GameInput.h.
enum : uint32_t {
    PS2_GI_MENU = 0x00000001u, PS2_GI_VIEW = 0x00000002u,
    PS2_GI_A = 0x00000004u, PS2_GI_B = 0x00000008u,
    PS2_GI_X = 0x00000010u, PS2_GI_Y = 0x00000020u,
    PS2_GI_DPAD_UP = 0x00000040u, PS2_GI_DPAD_DOWN = 0x00000080u,
    PS2_GI_DPAD_LEFT = 0x00000100u, PS2_GI_DPAD_RIGHT = 0x00000200u,
    PS2_GI_LEFT_SHOULDER = 0x00000400u, PS2_GI_RIGHT_SHOULDER = 0x00000800u,
    PS2_GI_LEFT_THUMBSTICK = 0x00001000u, PS2_GI_RIGHT_THUMBSTICK = 0x00002000u,
    PS2_GI_PADDLE_LEFT1 = 0x04000000u, PS2_GI_PADDLE_LEFT2 = 0x08000000u,
    PS2_GI_PADDLE_RIGHT1 = 0x10000000u, PS2_GI_PADDLE_RIGHT2 = 0x20000000u
};

// One gamepad reading in GameInput's layout and ranges.
struct ps2_gi_reading {
    uint32_t buttons;                         // PS2_GI_* bits (others ignored)
    float left_trigger, right_trigger;        // 0..1
    float left_x, left_y, right_x, right_y;   // -1..1, +Y up
};

// Backend.
// Returns at once; from now until ps2_gpb_stop returns it may call
// ps2_gph_device / ps2_gph_status from any thread.
void ps2_gpb_start();
// Afterwards no ps2_gph_* call is running or will follow. Call without
// holding any hub lock (it waits for running callbacks).
void ps2_gpb_stop();
// Releases the backend's own objects; call after the hub released every
// device handle it held.
void ps2_gpb_destroy();
// Current reading of device dev; false = none (not ready, no reading yet,
// failure). Hub thread only.
bool ps2_gpb_read(void *dev, ps2_gi_reading *out);
// Drops one reference on dev. Any thread.
void ps2_gpb_release(void *dev);

// Hub, callable from any thread.
// connected = 1: dev carries one reference that now belongs to the hub;
//   name is UTF-8 (NULL or "" is stored as "Controller").
// connected = 0: dev is a device that left; no reference is passed.
void ps2_gph_device(void *dev, int connected, const char *name);
// PS2_GAMEPAD_READY or PS2_GAMEPAD_UNAVAILABLE.
void ps2_gph_status(int status);
#endif
