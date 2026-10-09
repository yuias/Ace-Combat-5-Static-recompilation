// Win32 game window: window class, message handling, event queue, key state,
// borderless fullscreen and the Vulkan surface. See ps2_window.h for the API.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <imm.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_win32.h>
#include "ps2_window.h"
#include "ps2_settings.h"

#include <string.h>

namespace {

const wchar_t kClassName[] = L"ps2recomp_window";
constexpr int kQueueSize = 256;
// The smallest client size ps2_settings_sanitize keeps.
constexpr int kMinClientW = 320;
constexpr int kMinClientH = 224;
constexpr DWORD kWindowedStyle = WS_OVERLAPPEDWINDOW;

struct State {
    HWND hwnd;
    bool focused;
    bool minimized;
    bool fullscreen;
    bool in_modal;
    bool shown;
    unsigned char keys[PS2_KEY_COUNT];
    ps2_win_event queue[kQueueSize];
    int q_head;   // index of the oldest event
    int q_count;
    WINDOWPLACEMENT saved_placement;
    bool saved_valid;
    ps2_window_msg_hook msg_hook;
    ps2_window_redraw_hook redraw_hook;
};

State g;

void queue_push(const ps2_win_event &ev) {
    if (ev.type == PS2_WEV_RESIZED && g.q_count > 0) {
        ps2_win_event &newest = g.queue[(g.q_head + g.q_count - 1) % kQueueSize];
        if (newest.type == PS2_WEV_RESIZED) {
            newest = ev;
            return;
        }
    }
    if (g.q_count >= kQueueSize)
        return;
    g.queue[(g.q_head + g.q_count) % kQueueSize] = ev;
    g.q_count++;
}

void push_simple(int type) {
    ps2_win_event ev;
    memset(&ev, 0, sizeof ev);
    ev.type = type;
    queue_push(ev);
}

UINT dpi_of(HWND hwnd) {
    UINT dpi = GetDpiForWindow(hwnd);
    return dpi ? dpi : 96;
}

// Outer size of a window with the given style whose client area is cw x ch.
SIZE outer_size(int cw, int ch, DWORD style, DWORD ex_style, UINT dpi) {
    RECT rc = {0, 0, cw, ch};
    AdjustWindowRectExForDpi(&rc, style, FALSE, ex_style, dpi);
    SIZE s = {rc.right - rc.left, rc.bottom - rc.top};
    return s;
}

// Top-left of an outer rect of size (w, h) centred on the work area; a window
// larger than the work area starts at its top-left so it is never shrunk.
POINT centred_origin(const RECT &work, int w, int h) {
    POINT p;
    int ww = work.right - work.left;
    int wh = work.bottom - work.top;
    p.x = w <= ww ? work.left + (ww - w) / 2 : work.left;
    p.y = h <= wh ? work.top + (wh - h) / 2 : work.top;
    return p;
}

RECT work_area_of(HMONITOR mon) {
    MONITORINFO mi;
    memset(&mi, 0, sizeof mi);
    mi.cbSize = sizeof mi;
    if (!GetMonitorInfoW(mon, &mi))
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &mi.rcWork, 0);
    return mi.rcWork;
}

// Sizes and centres a windowed (overlapped) window on the work area of mon.
void place_windowed(HWND hwnd, HMONITOR mon, int cw, int ch, UINT dpi, UINT flags) {
    DWORD style = (DWORD)GetWindowLongPtrW(hwnd, GWL_STYLE);
    DWORD ex_style = (DWORD)GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
    SIZE s = outer_size(cw, ch, style, ex_style, dpi);
    POINT p = centred_origin(work_area_of(mon), s.cx, s.cy);
    SetWindowPos(hwnd, NULL, p.x, p.y, s.cx, s.cy,
                 SWP_NOZORDER | SWP_NOOWNERZORDER | SWP_NOACTIVATE | flags);
}

void cover_monitor(HWND hwnd, UINT extra_flags) {
    MONITORINFO mi;
    memset(&mi, 0, sizeof mi);
    mi.cbSize = sizeof mi;
    if (!GetMonitorInfoW(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), &mi))
        return;
    SetWindowPos(hwnd, HWND_TOP, mi.rcMonitor.left, mi.rcMonitor.top,
                 mi.rcMonitor.right - mi.rcMonitor.left,
                 mi.rcMonitor.bottom - mi.rcMonitor.top,
                 SWP_NOOWNERZORDER | extra_flags);
}

int utf8_to_wide(const char *s, wchar_t *out, int out_len) {
    out[0] = 0;
    if (!s)
        return 0;
    int n = (int)strlen(s);
    if (n > out_len - 1)
        n = out_len - 1;  // at most one UTF-16 unit per UTF-8 byte
    if (n == 0)
        return 0;
    int w = MultiByteToWideChar(CP_UTF8, 0, s, n, out, out_len - 1);
    if (w < 0)
        w = 0;
    out[w] = 0;
    return w;
}

// Returns the HID id for a WM_KEY* message, or 0 when it has no mapping.
int translate_key(WPARAM wp, LPARAM lp) {
    // Bit 7 is stripped because the on-screen keyboard can send break codes.
    unsigned sc = (unsigned)((lp >> 16) & 0x7F);
    unsigned code;
    if (sc == 0)
        code = MapVirtualKeyW((UINT)wp, MAPVK_VK_TO_VSC_EX);
    else
        code = (lp & (1 << 24)) ? (0xE000u | sc) : sc;
    int hid = ps2_key_from_scancode(code);
    return (hid > 0 && hid < PS2_KEY_COUNT) ? hid : 0;
}

void redraw(int resized) {
    if (g.redraw_hook)
        g.redraw_hook(resized);
}

LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    bool handled = false;
    LRESULT ret = 0;

    switch (msg) {
    case WM_KEYDOWN:
    case WM_SYSKEYDOWN: {
        int hid = translate_key(wp, lp);
        if (hid) {
            g.keys[hid] = 1;
            ps2_win_event ev;
            memset(&ev, 0, sizeof ev);
            ev.type = PS2_WEV_KEY_DOWN;
            ev.key = hid;
            ev.repeat = (int)((lp >> 30) & 1);
            ev.alt = (msg == WM_SYSKEYDOWN && ((lp >> 29) & 1)) ||
                     GetKeyState(VK_MENU) < 0;
            queue_push(ev);
        }
        break;
    }
    case WM_KEYUP:
    case WM_SYSKEYUP: {
        int hid = translate_key(wp, lp);
        if (hid) {
            g.keys[hid] = 0;
            if (hid == 225 || hid == 229) {
                // Windows sends one key-up when both Shifts were held.
                if (!(GetKeyState(VK_LSHIFT) & 0x8000))
                    g.keys[225] = 0;
                if (!(GetKeyState(VK_RSHIFT) & 0x8000))
                    g.keys[229] = 0;
            }
        }
        break;
    }
    case WM_SETFOCUS:
        g.focused = true;
        break;
    case WM_KILLFOCUS:
        g.focused = false;
        memset(g.keys, 0, sizeof g.keys);
        break;
    case WM_CLOSE:
        // The renderer decides whether to destroy the window.
        push_simple(PS2_WEV_CLOSE);
        handled = true;
        break;
    case WM_SIZE:
        if (wp == SIZE_MINIMIZED) {
            if (!g.minimized) {
                g.minimized = true;
                push_simple(PS2_WEV_MINIMIZED);
            }
        } else if (wp == SIZE_RESTORED || wp == SIZE_MAXIMIZED) {
            if (g.minimized) {
                g.minimized = false;
                push_simple(PS2_WEV_RESTORED);
            }
            ps2_win_event ev;
            memset(&ev, 0, sizeof ev);
            ev.type = PS2_WEV_RESIZED;
            ev.w = (int)LOWORD(lp);
            ev.h = (int)HIWORD(lp);
            queue_push(ev);
            if (g.in_modal)
                redraw(1);
        }
        handled = true;
        break;
    case WM_MOVE:
        if (g.in_modal)
            redraw(0);
        break;
    case WM_ENTERSIZEMOVE:
    case WM_ENTERMENULOOP:
        g.in_modal = true;
        SetTimer(hwnd, PS2_WINDOW_MODAL_TIMER, USER_TIMER_MINIMUM, NULL);
        break;
    case WM_EXITSIZEMOVE:
    case WM_EXITMENULOOP:
        g.in_modal = false;
        KillTimer(hwnd, PS2_WINDOW_MODAL_TIMER);
        break;
    case WM_TIMER:
        if (wp == PS2_WINDOW_MODAL_TIMER && g.in_modal)
            redraw(0);
        handled = true;
        break;
    case WM_PAINT: {
        // Validating the update region is required, otherwise WM_PAINT
        // is sent again forever.
        PAINTSTRUCT ps;
        BeginPaint(hwnd, &ps);
        EndPaint(hwnd, &ps);
        if (g.in_modal)
            redraw(0);
        handled = true;
        break;
    }
    case WM_ERASEBKGND:
        ret = 1;
        handled = true;
        break;
    case WM_SYSCOMMAND:
        // Alt or F10 must not enter menu mode, which would stall the pump.
        if ((wp & 0xFFF0) == SC_KEYMENU)
            handled = true;
        break;
    case WM_SYSCHAR:
        handled = true;
        break;
    case WM_MENUCHAR:
        ret = MAKELRESULT(0, MNC_CLOSE);
        handled = true;
        break;
    case WM_GETMINMAXINFO:
        if (!g.fullscreen) {
            MINMAXINFO *mmi = reinterpret_cast<MINMAXINFO *>(lp);
            SIZE s = outer_size(kMinClientW, kMinClientH, kWindowedStyle, 0, dpi_of(hwnd));
            mmi->ptMinTrackSize.x = s.cx;
            mmi->ptMinTrackSize.y = s.cy;
        }
        handled = true;
        break;
    case WM_DPICHANGED:
        if (g.fullscreen) {
            cover_monitor(hwnd, SWP_NOACTIVATE);
        } else {
            // Keep the client size in physical pixels across the DPI change.
            const RECT *sug = reinterpret_cast<const RECT *>(lp);
            RECT client;
            GetClientRect(hwnd, &client);
            SIZE s = outer_size(client.right, client.bottom,
                                (DWORD)GetWindowLongPtrW(hwnd, GWL_STYLE),
                                (DWORD)GetWindowLongPtrW(hwnd, GWL_EXSTYLE),
                                HIWORD(wp));
            SetWindowPos(hwnd, NULL, sug->left, sug->top, s.cx, s.cy,
                         SWP_NOZORDER | SWP_NOACTIVATE);
        }
        handled = true;
        break;
    case WM_DISPLAYCHANGE:
        if (g.fullscreen)
            cover_monitor(hwnd, SWP_NOACTIVATE);
        break;
    default:
        break;
    }

    intptr_t hook_ret = 0;
    if (g.msg_hook)
        hook_ret = g.msg_hook(hwnd, msg, (uintptr_t)wp, (intptr_t)lp);

    if (msg == WM_SETCURSOR && hook_ret != 0)
        return TRUE;
    if (handled)
        return ret;
    return DefWindowProcW(hwnd, msg, wp, lp);
}

}  // namespace

extern "C" {

int ps2_window_create(const char *title_utf8, int client_w, int client_h) {
    if (g.hwnd)
        return -1;

    // An API call instead of a manifest because the build has no manifest
    // step. Window sizes are physical pixels everywhere in the runtime. It
    // fails if the awareness is already set.
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    HINSTANCE inst = GetModuleHandleW(NULL);
    WNDCLASSEXW wc;
    memset(&wc, 0, sizeof wc);
    wc.cbSize = sizeof wc;
    wc.lpfnWndProc = window_proc;
    wc.hInstance = inst;
    // IDC_ARROW and IDI_APPLICATION are narrow-string macros unless UNICODE
    // is defined, which this file does not do; the casts keep the same ids.
    wc.hCursor = LoadCursorW(NULL, reinterpret_cast<LPCWSTR>(IDC_ARROW));
    wc.hIcon = LoadIconW(NULL, reinterpret_cast<LPCWSTR>(IDI_APPLICATION));
    wc.hbrBackground = NULL;
    wc.lpszClassName = kClassName;
    if (!RegisterClassExW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS)
        return -1;

    UINT sys_dpi = GetDpiForSystem();
    if (!sys_dpi)
        sys_dpi = 96;
    SIZE s = outer_size(client_w, client_h, kWindowedStyle, 0, sys_dpi);
    RECT work = work_area_of(MonitorFromPoint(POINT{0, 0}, MONITOR_DEFAULTTOPRIMARY));
    POINT p = centred_origin(work, s.cx, s.cy);

    wchar_t title[1024];
    utf8_to_wide(title_utf8, title, 1024);
    HWND hwnd = CreateWindowExW(0, kClassName, title, kWindowedStyle,
                                p.x, p.y, s.cx, s.cy, NULL, NULL, inst, NULL);
    if (!hwnd) {
        UnregisterClassW(kClassName, inst);
        memset(&g, 0, sizeof g);
        return -1;
    }
    g.hwnd = hwnd;

    UINT win_dpi = dpi_of(hwnd);
    if (win_dpi != sys_dpi)
        place_windowed(hwnd, MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST),
                       client_w, client_h, win_dpi, 0);

    // IME off: the game has no text input. The Dear ImGui Win32 backend
    // re-enables it only while one of its text fields wants it.
    ImmAssociateContextEx(hwnd, NULL, 0);
    return 0;
}

void ps2_window_destroy(void) {
    if (!g.hwnd)
        return;
    if (g.shown)
        SetThreadExecutionState(ES_CONTINUOUS);
    KillTimer(g.hwnd, PS2_WINDOW_MODAL_TIMER);
    DestroyWindow(g.hwnd);
    UnregisterClassW(kClassName, GetModuleHandleW(NULL));
    memset(&g, 0, sizeof g);
}

void ps2_window_show(void) {
    if (!g.hwnd)
        return;
    ShowWindow(g.hwnd, SW_SHOW);
    SetForegroundWindow(g.hwnd);
    // Keep the screen saver and display sleep away while the game runs;
    // gamepad input does not reset the idle timer.
    SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED);
    g.shown = true;
}

void ps2_window_pump(void) {
    MSG m;
    while (PeekMessageW(&m, NULL, 0, 0, PM_REMOVE)) {
        // TranslateMessage is needed for WM_CHAR (menu text input).
        TranslateMessage(&m);
        DispatchMessageW(&m);
    }
}

int ps2_window_next_event(ps2_win_event *ev) {
    if (g.q_count == 0)
        return 0;
    *ev = g.queue[g.q_head];
    g.q_head = (g.q_head + 1) % kQueueSize;
    g.q_count--;
    return 1;
}

const unsigned char *ps2_window_key_state(void) {
    return g.keys;
}

int ps2_window_has_focus(void) {
    return g.hwnd && g.focused ? 1 : 0;
}

void ps2_window_client_size(int *w, int *h) {
    RECT rc = {0, 0, 0, 0};
    if (g.hwnd)
        GetClientRect(g.hwnd, &rc);
    if (w)
        *w = rc.right - rc.left;
    if (h)
        *h = rc.bottom - rc.top;
}

float ps2_window_dpi_scale(void) {
    if (!g.hwnd)
        return 1.0f;
    UINT dpi = GetDpiForWindow(g.hwnd);
    return dpi ? (float)dpi / 96.0f : 1.0f;
}

void ps2_window_set_title(const char *title_utf8) {
    if (!g.hwnd)
        return;
    wchar_t title[1024];
    utf8_to_wide(title_utf8, title, 1024);
    SetWindowTextW(g.hwnd, title);
}

void ps2_window_set_fullscreen(int on, int client_w, int client_h) {
    HWND hwnd = g.hwnd;
    if (!hwnd)
        return;

    if (on) {
        if (g.fullscreen) {
            cover_monitor(hwnd, 0);
            return;
        }
        memset(&g.saved_placement, 0, sizeof g.saved_placement);
        g.saved_placement.length = sizeof g.saved_placement;
        g.saved_valid = GetWindowPlacement(hwnd, &g.saved_placement) != 0;
        // WS_SYSMENU and WS_MINIMIZEBOX keep taskbar Close and minimize working.
        LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
        style = (style & ~(LONG_PTR)WS_OVERLAPPEDWINDOW) | WS_POPUP | WS_SYSMENU | WS_MINIMIZEBOX;
        SetWindowLongPtrW(hwnd, GWL_STYLE, style);
        g.fullscreen = true;
        cover_monitor(hwnd, SWP_FRAMECHANGED);
        return;
    }

    if (g.fullscreen) {
        LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
        style = (style & ~(LONG_PTR)(WS_POPUP | WS_SYSMENU | WS_MINIMIZEBOX)) | WS_OVERLAPPEDWINDOW;
        SetWindowLongPtrW(hwnd, GWL_STYLE, style);
        g.fullscreen = false;
        bool visible = IsWindowVisible(hwnd) != 0;
        if (g.saved_valid) {
            WINDOWPLACEMENT wpl = g.saved_placement;
            if (!visible)
                wpl.showCmd = SW_HIDE;
            else if (wpl.showCmd != SW_SHOWMAXIMIZED)
                wpl.showCmd = SW_SHOWNORMAL;
            SetWindowPlacement(hwnd, &wpl);
            SetWindowPos(hwnd, NULL, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER |
                             SWP_FRAMECHANGED);
            g.saved_valid = false;
            return;
        }
        g.saved_valid = false;
        SetWindowPos(hwnd, NULL, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOOWNERZORDER |
                         SWP_NOACTIVATE | SWP_FRAMECHANGED);
        place_windowed(hwnd, MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST),
                       client_w, client_h, dpi_of(hwnd), 0);
        return;
    }

    // Already windowed: serves the menu's "Window size" combo. The visibility
    // check keeps a hidden window hidden.
    if (IsWindowVisible(hwnd) && IsZoomed(hwnd))
        ShowWindow(hwnd, SW_RESTORE);
    place_windowed(hwnd, MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST),
                   client_w, client_h, dpi_of(hwnd), 0);
}

void *ps2_window_hwnd(void) {
    return g.hwnd;
}

const char *const *ps2_window_vk_extensions(uint32_t *count) {
    static const char *const exts[] = {
        VK_KHR_SURFACE_EXTENSION_NAME,
        VK_KHR_WIN32_SURFACE_EXTENSION_NAME,
    };
    if (count)
        *count = (uint32_t)(sizeof exts / sizeof exts[0]);
    return exts;
}

VkResult ps2_window_create_vk_surface(VkInstance inst, VkSurfaceKHR *out) {
    if (!g.hwnd)
        return VK_ERROR_INITIALIZATION_FAILED;
    VkWin32SurfaceCreateInfoKHR ci;
    memset(&ci, 0, sizeof ci);
    ci.sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR;
    ci.hinstance = GetModuleHandleW(NULL);
    ci.hwnd = g.hwnd;
    return vkCreateWin32SurfaceKHR(inst, &ci, NULL, out);
}

void ps2_window_set_msg_hook(ps2_window_msg_hook fn) {
    g.msg_hook = fn;
}

void ps2_window_set_redraw_hook(ps2_window_redraw_hook fn) {
    g.redraw_hook = fn;
}

}  // extern "C"
