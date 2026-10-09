#ifndef PS2_GAMEPAD_H
#define PS2_GAMEPAD_H
/* Gamepads through Microsoft GameInput: the hub in ps2_gamepad.cpp, the
   device backend in ps2_gamepad_gi.cpp. No windows.h or GameInput.h here.
   Button and axis ids are PS2_PAD_BUTTON_* / PS2_PAD_AXIS_* (ps2_settings.h);
   axis values are -32768..32767 for sticks (+Y down) and 0..32767 for
   triggers, the ranges stored binds were made with. One pad is selected at a
   time. Except ps2_gamepad_status, call everything on the thread that called
   ps2_gamepad_init (the renderer thread); before init every call is a
   harmless no-op returning 0 / NULL. */
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    PS2_PEV_ADDED = 1,    /* id: a gamepad arrived */
    PS2_PEV_REMOVED,      /* id: a gamepad left */
    PS2_PEV_BUTTON_DOWN,  /* id, button: selected pad only */
    PS2_PEV_BUTTON_UP,    /* id, button: selected pad only */
    PS2_PEV_AXIS          /* id, axis, value: selected pad only */
};

typedef struct {
    int      type;
    uint32_t id;
    int      button;
    int      axis;
    int      value;
} ps2_pad_event;

enum { PS2_GAMEPAD_UNAVAILABLE = -1, PS2_GAMEPAD_STARTING = 0,
       PS2_GAMEPAD_READY = 1 };

#define PS2_GAMEPAD_MAX  16   /* devices listed at once */
#define PS2_GAMEPAD_NAME 128  /* bytes, with the terminator */

typedef struct {
    uint32_t id;                      /* nonzero; never reused while initialised */
    char     name[PS2_GAMEPAD_NAME];  /* UTF-8, never empty */
} ps2_pad_info;

/* Starts the backend and returns at once; GameInput comes up on its own
   thread. A second call before ps2_gamepad_shutdown does nothing. */
void ps2_gamepad_init(void);
/* Stops callbacks, releases every device, then GameInput. May wait for the
   backend's start-up thread (about a second at most). */
void ps2_gamepad_shutdown(void);
/* PS2_GAMEPAD_*; STARTING before init and until the backend reports.
   Callable from any thread. */
int  ps2_gamepad_status(void);
/* Applies hotplug, selection and the selected pad's reading; queues events.
   focused = 0 makes the pad read as neutral and queues no input events. */
void ps2_gamepad_update(int focused);
/* Oldest queued event: 1 = *ev filled, 0 = queue empty. */
int  ps2_gamepad_next_event(ps2_pad_event *ev);
/* Connected pads in arrival order; returns how many were written. */
int  ps2_gamepad_list(ps2_pad_info *out, int max);
uint32_t    ps2_gamepad_current(void);       /* selected pad id, 0 = none */
const char *ps2_gamepad_current_name(void);  /* NULL = none */
/* Selects a listed pad and logs "pad: <name> selected"; unknown ids and the
   current id do nothing. */
void ps2_gamepad_select(uint32_t id);
int  ps2_gamepad_button(int button);         /* 1 = held on the selected pad */
int  ps2_gamepad_axis(int axis);             /* selected pad's value, else 0 */

#ifdef __cplusplus
}
#endif
#endif
