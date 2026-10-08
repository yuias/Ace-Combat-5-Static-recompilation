// The one place the settings menu touches windows.h types, so ps2_ui.cpp
// can stay free of windows.h and its macros.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <cstdint>

#include "imgui.h"
#include "imgui_impl_win32.h"

// imgui_impl_win32.h leaves this declaration out so it needs no windows.h.
extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg, WPARAM wParam,
                                                             LPARAM lParam);

// mode 0: forward only mouse-button-up messages (WM_[LRMX]BUTTONUP), so the
//         backend can release a mouse capture it took while the menu was open
// mode 1: forward everything except keyboard messages (WM_KEYDOWN, WM_KEYUP,
//         WM_CHAR, WM_SYSKEYDOWN, WM_SYSKEYUP, WM_SYSCHAR)
// mode 2: forward everything
intptr_t ps2_ui_win32_wndproc(void *hwnd, unsigned msg, uintptr_t wparam,
                              intptr_t lparam, int mode) {
    switch (msg) {
    case WM_LBUTTONUP:
    case WM_RBUTTONUP:
    case WM_MBUTTONUP:
    case WM_XBUTTONUP:
        break;
    case WM_KEYDOWN:
    case WM_KEYUP:
    case WM_CHAR:
    case WM_SYSKEYDOWN:
    case WM_SYSKEYUP:
    case WM_SYSCHAR:
        if (mode < 2) return 0;
        break;
    default:
        if (mode < 1) return 0;
        break;
    }
    return ImGui_ImplWin32_WndProcHandler(static_cast<HWND>(hwnd), msg,
                                          static_cast<WPARAM>(wparam),
                                          static_cast<LPARAM>(lparam));
}
