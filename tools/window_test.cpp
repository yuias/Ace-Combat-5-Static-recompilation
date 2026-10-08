// Headless test of the Win32 window layer: one hidden window, messages sent
// with SendMessageW. Nothing here needs a display focus or user input.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <stdio.h>
#include <string.h>

#include "ps2_settings.h"
#include "ps2_window.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL: " __VA_ARGS__); printf("\n"); fails++; } } while (0)

// Stand-in for the table in ps2_settings.c: the result encodes the code the
// window layer built (extended keys get 0x100), so the test checks how the
// layer builds the code rather than the table.
extern "C" int ps2_key_from_scancode(unsigned c) {
    if (c == 0xE11Du)
        return 0x1FF;
    return (int)((c & 0xFFu) | ((c & 0xFF00u) == 0xE000u ? 0x100u : 0u));
}

static int drain(void) {
    ps2_win_event ev;
    int n = 0;
    while (ps2_window_next_event(&ev))
        n++;
    return n;
}

static LPARAM key_lp(unsigned scan, bool ext, bool repeat, bool alt) {
    return (LPARAM)(((unsigned)scan << 16) | (ext ? 1u << 24 : 0u) |
                    (repeat ? 1u << 30 : 0u) | (alt ? 1u << 29 : 0u));
}

static int hook_cursor_seen;
static int hook_keydown_seen;
static intptr_t msg_hook(void *, unsigned msg, uintptr_t, intptr_t) {
    if (msg == WM_SETCURSOR) {
        hook_cursor_seen++;
        return 1;
    }
    if (msg == WM_KEYDOWN)
        hook_keydown_seen++;
    return 0;
}

static int redraw_calls;
static int redraw_last_resized = -1;
static void redraw_hook(int resized) {
    redraw_calls++;
    redraw_last_resized = resized;
}

static bool any_key_down(void) {
    const unsigned char *k = ps2_window_key_state();
    for (int i = 0; i < PS2_KEY_COUNT; i++)
        if (k[i])
            return true;
    return false;
}

static void client_of(HWND hwnd, int *w, int *h) {
    RECT rc;
    GetClientRect(hwnd, &rc);
    *w = rc.right - rc.left;
    *h = rc.bottom - rc.top;
}

int main(void) {
    ps2_win_event ev;

    // 1. creation
    CHECK(ps2_window_create("window test", 640, 480) == 0, "create");
    CHECK(ps2_window_create("again", 640, 480) == -1, "second create must fail");
    HWND hwnd = (HWND)ps2_window_hwnd();
    CHECK(hwnd && IsWindow(hwnd), "hwnd");
    if (!hwnd)
        return 1;
    CHECK(!IsWindowVisible(hwnd), "window must start hidden");
    int cw = 0, ch = 0;
    ps2_window_client_size(&cw, &ch);
    CHECK(cw == 640 && ch == 480, "client size %d x %d", cw, ch);
    CHECK(AreDpiAwarenessContextsEqual(GetWindowDpiAwarenessContext(hwnd),
                                       DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2),
          "per-monitor v2 DPI awareness");
    CHECK(ps2_window_key_state() != NULL, "key state never NULL");
    drain();

    // 2. key down, repeat, key up
    SendMessageW(hwnd, WM_KEYDOWN, 'X', key_lp(0x2D, false, false, false));
    CHECK(ps2_window_key_state()[0x2D] == 1, "key state after down");
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_KEY_DOWN && ev.key == 0x2D &&
              ev.repeat == 0 && ev.alt == 0,
          "KEY_DOWN event (type %d key %d repeat %d alt %d)", ev.type, ev.key, ev.repeat, ev.alt);
    CHECK(drain() == 0, "no extra events after one key down");
    SendMessageW(hwnd, WM_KEYDOWN, 'X', key_lp(0x2D, false, true, false));
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_KEY_DOWN && ev.key == 0x2D &&
              ev.repeat == 1,
          "auto-repeat flag");
    drain();
    SendMessageW(hwnd, WM_KEYUP, 'X', key_lp(0x2D, false, false, false));
    CHECK(ps2_window_key_state()[0x2D] == 0, "key state after up");
    CHECK(drain() == 0, "key up queues nothing");

    // 3. scan code assembly
    SendMessageW(hwnd, WM_KEYDOWN, VK_UP, key_lp(0x48, true, false, false));
    CHECK(ps2_window_next_event(&ev) && ev.key == 0x148, "extended bit: key %d", ev.key);
    SendMessageW(hwnd, WM_KEYUP, VK_UP, key_lp(0x48, true, false, false));
    SendMessageW(hwnd, WM_KEYDOWN, VK_NUMPAD8, key_lp(0x48, false, false, false));
    CHECK(ps2_window_next_event(&ev) && ev.key == 0x48, "plain scan: key %d", ev.key);
    SendMessageW(hwnd, WM_KEYUP, VK_NUMPAD8, key_lp(0x48, false, false, false));
    SendMessageW(hwnd, WM_KEYDOWN, 'X', key_lp(0xAD, false, false, false));
    CHECK(ps2_window_next_event(&ev) && ev.key == 0x2D, "break-code bit stripped: key %d", ev.key);
    SendMessageW(hwnd, WM_KEYUP, 'X', key_lp(0x2D, false, false, false));
    SendMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, key_lp(0, false, false, false));
    CHECK(ps2_window_next_event(&ev) && ev.key == 0x01, "VK fallback for scan 0: key %d", ev.key);
    SendMessageW(hwnd, WM_KEYUP, VK_ESCAPE, key_lp(0, false, false, false));
    drain();

    // 4. Alt flag
    SendMessageW(hwnd, WM_SYSKEYDOWN, VK_RETURN, key_lp(0x1C, false, false, true));
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_KEY_DOWN && ev.alt == 1,
          "alt flag from WM_SYSKEYDOWN");
    SendMessageW(hwnd, WM_SYSKEYUP, VK_RETURN, key_lp(0x1C, false, false, true));
    drain();

    // 5. focus loss clears the key state
    SendMessageW(hwnd, WM_SETFOCUS, 0, 0);
    CHECK(ps2_window_has_focus() == 1, "focus after WM_SETFOCUS");
    SendMessageW(hwnd, WM_KEYDOWN, 'X', key_lp(0x2D, false, false, false));
    CHECK(any_key_down(), "a key is held before focus loss");
    SendMessageW(hwnd, WM_KILLFOCUS, 0, 0);
    CHECK(!any_key_down(), "key state cleared by WM_KILLFOCUS");
    CHECK(ps2_window_has_focus() == 0, "no focus after WM_KILLFOCUS");
    SendMessageW(hwnd, WM_SETFOCUS, 0, 0);
    CHECK(ps2_window_has_focus() == 1, "focus after WM_SETFOCUS again");
    drain();

    // 6. close request leaves the window alone
    SendMessageW(hwnd, WM_CLOSE, 0, 0);
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_CLOSE, "CLOSE event");
    CHECK(drain() == 0, "exactly one CLOSE event");
    CHECK(IsWindow(hwnd), "WM_CLOSE must not destroy the window");

    // 7. Alt / F10 must not start menu mode
    CHECK(SendMessageW(hwnd, WM_SYSCOMMAND, SC_KEYMENU, 0) == 0, "SC_KEYMENU swallowed");
    drain();

    // 8. minimize, restore and resize coalescing
    SendMessageW(hwnd, WM_SIZE, SIZE_MINIMIZED, 0);
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_MINIMIZED, "MINIMIZED event");
    CHECK(drain() == 0, "only MINIMIZED");
    SendMessageW(hwnd, WM_SIZE, SIZE_RESTORED, MAKELPARAM(640, 480));
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_RESTORED, "RESTORED event");
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_RESIZED && ev.w == 640 && ev.h == 480,
          "RESIZED after RESTORED (%d x %d)", ev.w, ev.h);
    CHECK(drain() == 0, "nothing after RESIZED");
    SendMessageW(hwnd, WM_SIZE, SIZE_RESTORED, MAKELPARAM(700, 500));
    SendMessageW(hwnd, WM_SIZE, SIZE_RESTORED, MAKELPARAM(800, 600));
    SendMessageW(hwnd, WM_SIZE, SIZE_RESTORED, MAKELPARAM(900, 700));
    CHECK(ps2_window_next_event(&ev) && ev.type == PS2_WEV_RESIZED && ev.w == 900 && ev.h == 700,
          "coalesced RESIZED keeps the last size (%d x %d)", ev.w, ev.h);
    CHECK(drain() == 0, "resize flood coalesced into one event");

    // 9. borderless fullscreen and back, on the hidden window
    ps2_window_set_fullscreen(1, 640, 480);
    {
        MONITORINFO mi;
        memset(&mi, 0, sizeof mi);
        mi.cbSize = sizeof mi;
        GetMonitorInfoW(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), &mi);
        client_of(hwnd, &cw, &ch);
        CHECK(cw == mi.rcMonitor.right - mi.rcMonitor.left &&
                  ch == mi.rcMonitor.bottom - mi.rcMonitor.top,
              "fullscreen client %d x %d", cw, ch);
        LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
        CHECK((style & WS_POPUP) != 0, "fullscreen has WS_POPUP");
        CHECK((style & WS_CAPTION) == 0, "fullscreen has no caption");
        CHECK(!IsWindowVisible(hwnd), "fullscreen must not show a hidden window");
    }
    ps2_window_set_fullscreen(0, 640, 480);
    {
        client_of(hwnd, &cw, &ch);
        CHECK(cw == 640 && ch == 480, "windowed again: client %d x %d", cw, ch);
        LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
        CHECK((style & WS_OVERLAPPEDWINDOW) == WS_OVERLAPPEDWINDOW, "overlapped style restored");
        CHECK((style & WS_POPUP) == 0, "WS_POPUP removed");
        CHECK(!IsWindowVisible(hwnd), "leaving fullscreen must not show a hidden window");
    }
    // Already windowed: the call only resizes.
    ps2_window_set_fullscreen(0, 800, 600);
    client_of(hwnd, &cw, &ch);
    CHECK(cw == 800 && ch == 600, "resize while windowed: client %d x %d", cw, ch);
    ps2_window_set_fullscreen(0, 640, 480);
    drain();

    // 10. redraw hook only runs inside the modal loop. No message pumping
    // here: WM_ENTERSIZEMOVE starts a real timer.
    ps2_window_set_redraw_hook(redraw_hook);
    SendMessageW(hwnd, WM_TIMER, PS2_WINDOW_MODAL_TIMER, 0);
    CHECK(redraw_calls == 0, "no redraw before the modal loop (%d)", redraw_calls);
    SendMessageW(hwnd, WM_ENTERSIZEMOVE, 0, 0);
    SendMessageW(hwnd, WM_TIMER, PS2_WINDOW_MODAL_TIMER, 0);
    CHECK(redraw_calls == 1 && redraw_last_resized == 0, "timer redraw (%d calls, resized %d)",
          redraw_calls, redraw_last_resized);
    SendMessageW(hwnd, WM_SIZE, SIZE_RESTORED, MAKELPARAM(640, 480));
    CHECK(redraw_calls == 2 && redraw_last_resized == 1, "size redraw (%d calls, resized %d)",
          redraw_calls, redraw_last_resized);
    SendMessageW(hwnd, WM_EXITSIZEMOVE, 0, 0);
    SendMessageW(hwnd, WM_TIMER, PS2_WINDOW_MODAL_TIMER, 0);
    CHECK(redraw_calls == 2, "no redraw after the modal loop (%d)", redraw_calls);
    ps2_window_set_redraw_hook(NULL);
    drain();

    // 11. message hook
    ps2_window_set_msg_hook(msg_hook);
    CHECK(SendMessageW(hwnd, WM_SETCURSOR, (WPARAM)hwnd, HTCLIENT) == 1 && hook_cursor_seen == 1,
          "WM_SETCURSOR result comes from the hook");
    SendMessageW(hwnd, WM_KEYDOWN, 'X', key_lp(0x2D, false, false, false));
    CHECK(hook_keydown_seen == 1 && ps2_window_key_state()[0x2D] == 1,
          "key messages reach the hook and still update the key state");
    SendMessageW(hwnd, WM_KEYUP, 'X', key_lp(0x2D, false, false, false));
    ps2_window_set_msg_hook(NULL);
    drain();

    // 12. a full queue drops events instead of overflowing
    for (int i = 0; i < 300; i++)
        SendMessageW(hwnd, WM_KEYDOWN, 'X', key_lp(0x2D, false, i > 0, false));
    int n = drain();
    CHECK(n == 256, "queue held %d events", n);
    SendMessageW(hwnd, WM_KEYUP, 'X', key_lp(0x2D, false, false, false));

    // 13. Vulkan extension names
    {
        uint32_t count = 0;
        const char *const *ext = ps2_window_vk_extensions(&count);
        CHECK(count == 2 && ext, "two instance extensions (%u)", count);
        if (count == 2 && ext) {
            CHECK(strcmp(ext[0], "VK_KHR_surface") == 0, "first extension %s", ext[0]);
            CHECK(strcmp(ext[1], "VK_KHR_win32_surface") == 0, "second extension %s", ext[1]);
        }
    }

    // 14. UTF-8 title
    ps2_window_set_title("\xE3\x83\x86\xE3\x82\xB9\xE3\x83\x88");
    {
        wchar_t buf[64] = {0};
        GetWindowTextW(hwnd, buf, 64);
        CHECK(wcscmp(buf, L"テスト") == 0, "title round trip");
    }

    // 15. destroy and create again
    ps2_window_destroy();
    CHECK(!IsWindow(hwnd), "window destroyed");
    CHECK(ps2_window_hwnd() == NULL, "no hwnd after destroy");
    CHECK(ps2_window_create("window test 2", 640, 480) == 0, "second create after destroy");
    ps2_window_destroy();

    if (fails) {
        printf("window test: %d checks failed\n", fails);
        return 1;
    }
    printf("window test: all checks passed\n");
    return 0;
}
