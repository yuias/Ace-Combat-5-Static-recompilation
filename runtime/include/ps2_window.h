#ifndef PS2_WINDOW_H
#define PS2_WINDOW_H
/* The game window on Win32, implemented in ps2_window.cpp. No windows.h
   here, so includers never see its macros. One window per process; every
   function must be called on the thread that called ps2_window_create, and
   is a harmless no-op (or returns zeros) when no window exists. */
#include <stdint.h>
#include <vulkan/vulkan.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    PS2_WEV_CLOSE = 1,  /* close button, Alt+F4, taskbar Close; the window stays */
    PS2_WEV_RESIZED,    /* w, h: new client size in physical pixels */
    PS2_WEV_MINIMIZED,
    PS2_WEV_RESTORED,   /* first non-minimized size after a minimize */
    PS2_WEV_KEY_DOWN    /* key, repeat, alt */
};

typedef struct {
    int type;
    int w, h;
    int key;     /* USB HID id (PS2_KEY_*), never 0 */
    int repeat;  /* 1 for auto-repeat */
    int alt;     /* either Alt key held */
} ps2_win_event;

/* Timer id the window uses during the modal size/move loop. */
#define PS2_WINDOW_MODAL_TIMER 1u

/* Sets per-monitor DPI awareness v2 for the process, then creates the window
   hidden, with a client area of client_w x client_h physical pixels, centred
   on the primary monitor's work area. Call before anything else in the
   process creates a window. 0 = ok, -1 = failed or a window already exists. */
int  ps2_window_create(const char *title_utf8, int client_w, int client_h);
void ps2_window_destroy(void);
/* Shows and activates the window and keeps the display awake while it lives. */
void ps2_window_show(void);
/* Dispatches every pending Win32 message of the calling thread. */
void ps2_window_pump(void);
/* Pops the oldest queued event. 1 = *ev filled, 0 = queue empty. */
int  ps2_window_next_event(ps2_win_event *ev);
/* PS2_KEY_COUNT entries indexed by HID id, 1 = held. Never NULL; cleared
   when the window loses keyboard focus (WM_KILLFOCUS). */
const unsigned char *ps2_window_key_state(void);
int   ps2_window_has_focus(void);
void  ps2_window_client_size(int *w, int *h);
float ps2_window_dpi_scale(void);          /* GetDpiForWindow / 96, 1 if none */
void  ps2_window_set_title(const char *title_utf8);
/* on = borderless window covering its monitor. off = windowed: restores the
   placement saved when fullscreen was entered; without a saved placement (or
   when already windowed) sets the client area to client_w x client_h centred
   on the window's monitor. Never shows a hidden window. */
void  ps2_window_set_fullscreen(int on, int client_w, int client_h);
void *ps2_window_hwnd(void);               /* HWND, or NULL */

/* Instance extensions needed for the surface: VK_KHR_surface and
   VK_KHR_win32_surface. */
const char *const *ps2_window_vk_extensions(uint32_t *count);
VkResult ps2_window_create_vk_surface(VkInstance inst, VkSurfaceKHR *out);

/* Called with every message the window receives, after the window's own
   bookkeeping. The return value is used only for WM_SETCURSOR: nonzero means
   the hook set the cursor. NULL removes the hook. */
typedef intptr_t (*ps2_window_msg_hook)(void *hwnd, unsigned msg,
                                        uintptr_t wparam, intptr_t lparam);
void ps2_window_set_msg_hook(ps2_window_msg_hook fn);
/* Called during the modal size/move loop (and the system-menu loop), when
   ps2_window_pump does not return, so the caller can keep presenting.
   resized = 1 when the client size changed. NULL removes the hook. */
typedef void (*ps2_window_redraw_hook)(int resized);
void ps2_window_set_redraw_hook(ps2_window_redraw_hook fn);

#ifdef __cplusplus
}
#endif
#endif
