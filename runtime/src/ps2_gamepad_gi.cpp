// Gamepad backend on Microsoft GameInput (API v3): creates IGameInput on its
// own thread, reports connected pads to the hub through a device callback, and
// answers the hub's per-frame reading requests.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <GameInput.h>
#include "ps2_gamepad.h"
#include "ps2_gamepad_backend.h"
#include "ps2_os.h"
#include <atomic>
#include <cstdio>

extern "C" void ps2_log(const char *fmt, ...);

using namespace GameInput::v3;

static_assert(GAMEINPUT_API_VERSION == 3,
              "the package's v3 GameInput.h must win over the Windows SDK's older header");

#define PS2_GI_BIT_CHECK(ours, theirs) \
    static_assert((ours) == (uint32_t)(theirs), #ours " must match " #theirs)
PS2_GI_BIT_CHECK(PS2_GI_MENU, GameInputGamepadMenu);
PS2_GI_BIT_CHECK(PS2_GI_VIEW, GameInputGamepadView);
PS2_GI_BIT_CHECK(PS2_GI_A, GameInputGamepadA);
PS2_GI_BIT_CHECK(PS2_GI_B, GameInputGamepadB);
PS2_GI_BIT_CHECK(PS2_GI_X, GameInputGamepadX);
PS2_GI_BIT_CHECK(PS2_GI_Y, GameInputGamepadY);
PS2_GI_BIT_CHECK(PS2_GI_DPAD_UP, GameInputGamepadDPadUp);
PS2_GI_BIT_CHECK(PS2_GI_DPAD_DOWN, GameInputGamepadDPadDown);
PS2_GI_BIT_CHECK(PS2_GI_DPAD_LEFT, GameInputGamepadDPadLeft);
PS2_GI_BIT_CHECK(PS2_GI_DPAD_RIGHT, GameInputGamepadDPadRight);
PS2_GI_BIT_CHECK(PS2_GI_LEFT_SHOULDER, GameInputGamepadLeftShoulder);
PS2_GI_BIT_CHECK(PS2_GI_RIGHT_SHOULDER, GameInputGamepadRightShoulder);
PS2_GI_BIT_CHECK(PS2_GI_LEFT_THUMBSTICK, GameInputGamepadLeftThumbstick);
PS2_GI_BIT_CHECK(PS2_GI_RIGHT_THUMBSTICK, GameInputGamepadRightThumbstick);
PS2_GI_BIT_CHECK(PS2_GI_PADDLE_LEFT1, GameInputGamepadPaddleLeft1);
PS2_GI_BIT_CHECK(PS2_GI_PADDLE_LEFT2, GameInputGamepadPaddleLeft2);
PS2_GI_BIT_CHECK(PS2_GI_PADDLE_RIGHT1, GameInputGamepadPaddleRight1);
PS2_GI_BIT_CHECK(PS2_GI_PADDLE_RIGHT2, GameInputGamepadPaddleRight2);

namespace {

ps2_thread_t g_thread;
bool g_thread_started;
std::atomic<int> g_stop(0);
// Published only after the device callback is registered, so the renderer
// never reads through an IGameInput that is still being set up.
std::atomic<IGameInput *> g_gi(nullptr);
GameInputCallbackToken g_token;

void log_unavailable(const char *what, HRESULT hr) {
    const char *hint = "";
    if (hr == E_NOINTERFACE)
        hint = ": the installed GameInput runtime is older than API version 3; "
               "install GameInputRedist.msi (see the README)";
    else if (hr == HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND) ||
             hr == HRESULT_FROM_WIN32(ERROR_MOD_NOT_FOUND))
        hint = ": no GameInput runtime is installed; install GameInputRedist.msi "
               "(see the README)";
    ps2_log("pad: GameInput unavailable: %s failed (0x%08lX)%s; controllers are unavailable",
            what, (unsigned long)hr, hint);
}

// Runs on GameInput's threads.
void CALLBACK on_device(GameInputCallbackToken, void *, IGameInputDevice *device,
                        uint64_t, GameInputDeviceStatus current,
                        GameInputDeviceStatus) {
    if (current & GameInputDeviceConnected) {
        char fallback[32];
        const char *name = nullptr;
        const GameInputDeviceInfo *info = nullptr;
        if (SUCCEEDED(device->GetDeviceInfo(&info)) && info) {
            if (info->displayName && info->displayName[0])
                name = info->displayName;
            else {
                snprintf(fallback, sizeof fallback, "Gamepad %04X:%04X",
                         (unsigned)info->vendorId, (unsigned)info->productId);
                name = fallback;
            }
        } else {
            name = "Gamepad";
        }
        // The device pointer is only valid during this callback; the hub owns
        // the extra reference from here on.
        device->AddRef();
        ps2_gph_device(device, 1, name);
    } else {
        ps2_gph_device(device, 0, nullptr);
    }
}

// GameInputCreate takes about a second, so it runs here instead of on the
// renderer thread.
void *init_main(void *) {
    uint64_t t0 = ps2_mono_ns();
    IGameInput *g = nullptr;
    HRESULT hr = GameInputCreate(&g);
    if (FAILED(hr) || !g) {
        log_unavailable("GameInputCreate", FAILED(hr) ? hr : E_FAIL);
        ps2_gph_status(PS2_GAMEPAD_UNAVAILABLE);
        return nullptr;
    }
    if (g_stop.load()) {
        g->Release();
        return nullptr;
    }
    // Input only while the game is in the foreground; the hub additionally
    // gates on keyboard focus.
    g->SetFocusPolicy(GameInputDefaultFocusPolicy);
    // Blocking enumeration reports the pads that are already present through
    // on_device before this returns.
    hr = g->RegisterDeviceCallback(nullptr, GameInputKindGamepad,
                                   GameInputDeviceConnected,
                                   GameInputBlockingEnumeration, nullptr,
                                   on_device, &g_token);
    if (FAILED(hr)) {
        log_unavailable("RegisterDeviceCallback", hr);
        g->Release();
        ps2_gph_status(PS2_GAMEPAD_UNAVAILABLE);
        return nullptr;
    }
    g_gi.store(g, std::memory_order_release);
    ps2_log("pad: GameInput ready after %u ms",
            (unsigned)((ps2_mono_ns() - t0) / 1000000ull));
    ps2_gph_status(PS2_GAMEPAD_READY);
    return nullptr;
}

} // namespace

void ps2_gpb_start() {
    g_stop.store(0);
    g_gi.store(nullptr);
    g_token = 0;
    g_thread_started = false;
    if (ps2_thread_create(&g_thread, init_main, nullptr) != 0) {
        ps2_log("pad: GameInput unavailable: cannot start its thread; controllers are unavailable");
        ps2_gph_status(PS2_GAMEPAD_UNAVAILABLE);
        return;
    }
    g_thread_started = true;
}

void ps2_gpb_stop() {
    g_stop.store(1);
    if (g_thread_started) {
        ps2_thread_join(g_thread);
        g_thread_started = false;
    }
    IGameInput *g = g_gi.load(std::memory_order_acquire);
    if (g && g_token) {
        g->StopCallback(g_token);
        // Waits for a running callback, which takes the hub's pending lock.
        g->UnregisterCallback(g_token);
        g_token = 0;
    }
}

void ps2_gpb_destroy() {
    IGameInput *g = g_gi.exchange(nullptr);
    if (g) g->Release();
}

bool ps2_gpb_read(void *dev, ps2_gi_reading *out) {
    IGameInput *g = g_gi.load(std::memory_order_acquire);
    if (!g) return false;
    IGameInputReading *r = nullptr;
    // GAMEINPUT_E_READING_NOT_FOUND is normal until the pad first reports.
    if (FAILED(g->GetCurrentReading(GameInputKindGamepad, (IGameInputDevice *)dev, &r)) || !r)
        return false;
    GameInputGamepadState s{};
    bool ok = r->GetGamepadState(&s);
    r->Release();
    out->buttons = (uint32_t)s.buttons;
    out->left_trigger = s.leftTrigger;
    out->right_trigger = s.rightTrigger;
    out->left_x = s.leftThumbstickX;
    out->left_y = s.leftThumbstickY;
    out->right_x = s.rightThumbstickX;
    out->right_y = s.rightThumbstickY;
    return ok;
}

void ps2_gpb_release(void *dev) {
    ((IGameInputDevice *)dev)->Release();
}
