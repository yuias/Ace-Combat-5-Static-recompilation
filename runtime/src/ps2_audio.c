#include "ps2_runtime.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <objbase.h>      /* CoInitializeEx, CoCreateInstance (LEAN_AND_MEAN drops it) */
#include <initguid.h>     /* the next two headers then define their GUIDs here */
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <avrt.h>

void ps2_spu2_mix(s16 *out, u32 n);
void ps2_spu2_init(void);

#define AUDIO_RATE      48000
#define AUDIO_CHANNELS  2
#define AUDIO_CHUNK     512                 /* max frames per ps2_spu2_mix call */
#define AUDIO_BUFFER_HNS 300000             /* 30 ms requested buffer */
#define AUDIO_RETRY_MS  1000                /* device-outage retry period */
#define AUDIO_WAIT_MS   200                 /* render wait timeout */

/* mmdeviceapi.h does not declare IID_IUnknown; avoid linking uuid for it. */
static const IID audio_IID_IUnknown =
    {0x00000000, 0x0000, 0x0000, {0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};

static HANDLE audio_thread, wake_event, buf_event, ready_event; /* auto-reset, except ready (manual) */
static _Atomic int audio_live;      /* 1 only while a started stream is being serviced */
static _Atomic int audio_quit;      /* set by ps2_audio_stop */
static _Atomic int audio_reopen;    /* set by the notification client */
static _Atomic u64 audio_bytes;     /* bytes handed to the device, excluding silent prefill */
static _Atomic int audio_started;   /* render thread exists; written by start/stop only */

/* render-thread only */
static IMMDeviceEnumerator *enumr;
static IMMDevice *dev;
static IAudioClient *client;
static IAudioRenderClient *render;
static UINT32 buffer_frames;
static int outage_logged;

static FILE *wav_fp;
static u32 wav_bytes;

static void wav_open(void) {
    const char *path = getenv("PS2_AUDIO_WAV");
    u8 hdr[44];
    if (!path) return;
    wav_fp = fopen(path, "wb");
    if (!wav_fp) { ps2_log("audio: cannot open %s", path); return; }
    memset(hdr, 0, sizeof hdr);
    fwrite(hdr, 1, sizeof hdr, wav_fp);
    ps2_log("audio: tapping the mix to %s", path);
}

static void wav_put(const void *buf, int len) {
    if (!wav_fp) return;
    fwrite(buf, 1, (size_t)len, wav_fp);
    wav_bytes += (u32)len;
}

static void wav_close(void) {
    u8 h[44];
    u32 v;
    if (!wav_fp) return;
    memcpy(h, "RIFF", 4);   v = 36u + wav_bytes;      memcpy(h + 4, &v, 4);
    memcpy(h + 8, "WAVEfmt ", 8);
    v = 16;                 memcpy(h + 16, &v, 4);
    { u16 t = 1;            memcpy(h + 20, &t, 2);
      t = AUDIO_CHANNELS;   memcpy(h + 22, &t, 2); }
    v = AUDIO_RATE;         memcpy(h + 24, &v, 4);
    v = AUDIO_RATE * AUDIO_CHANNELS * 2u; memcpy(h + 28, &v, 4);
    { u16 t = AUDIO_CHANNELS * 2u; memcpy(h + 32, &t, 2);
      t = 16;               memcpy(h + 34, &t, 2); }
    memcpy(h + 36, "data", 4);            memcpy(h + 40, &wav_bytes, 4);
    fseek(wav_fp, 0, SEEK_SET);
    fwrite(h, 1, sizeof h, wav_fp);
    fclose(wav_fp);
    wav_fp = NULL;
}

static void audio_close(void) {
    atomic_store(&audio_live, 0);
    if (client) IAudioClient_Stop(client);
    if (render) { IAudioRenderClient_Release(render); render = NULL; }
    if (client) { IAudioClient_Release(client); client = NULL; }
    if (dev) { IMMDevice_Release(dev); dev = NULL; }
}

/* Failure path for audio_open: log once per outage, drop partial state. */
static HRESULT open_failed(const char *what, HRESULT hr) {
    if (!outage_logged) {
        if (hr == E_NOTFOUND)
            ps2_log("audio: no output device, retrying");
        else
            ps2_log("audio: %s 0x%08lX, retrying", what, (unsigned long)hr);
        outage_logged = 1;
    }
    audio_close();
    return hr;
}

static HRESULT audio_open(const wchar_t *endpoint_id) {
    WAVEFORMATEX wf = {0};
    BYTE *data;
    HRESULT hr;
    int was_out;

    hr = endpoint_id
        ? IMMDeviceEnumerator_GetDevice(enumr, endpoint_id, &dev)
        : IMMDeviceEnumerator_GetDefaultAudioEndpoint(enumr, eRender, eConsole, &dev);
    if (FAILED(hr)) return open_failed(endpoint_id ? "GetDevice" : "GetDefaultAudioEndpoint", hr);

    hr = IMMDevice_Activate(dev, &IID_IAudioClient, CLSCTX_ALL, NULL, (void **)&client);
    if (FAILED(hr)) return open_failed("Activate", hr);

    wf.wFormatTag = WAVE_FORMAT_PCM;
    wf.nChannels = AUDIO_CHANNELS;
    wf.nSamplesPerSec = AUDIO_RATE;
    wf.wBitsPerSample = 16;
    wf.nBlockAlign = AUDIO_CHANNELS * 2;
    wf.nAvgBytesPerSec = AUDIO_RATE * wf.nBlockAlign;

    /* Shared mode needs periodicity 0; the engine converts the s16 stream. */
    hr = IAudioClient_Initialize(client, AUDCLNT_SHAREMODE_SHARED,
                                 AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
                                 AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM |
                                 AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                                 AUDIO_BUFFER_HNS, 0, &wf, NULL);
    if (FAILED(hr)) return open_failed("Initialize", hr);

    hr = IAudioClient_GetBufferSize(client, &buffer_frames);
    if (FAILED(hr)) return open_failed("GetBufferSize", hr);

    hr = IAudioClient_SetEventHandle(client, buf_event);
    if (FAILED(hr)) return open_failed("SetEventHandle", hr);

    hr = IAudioClient_GetService(client, &IID_IAudioRenderClient, (void **)&render);
    if (FAILED(hr)) return open_failed("GetService", hr);

    /* Start with a silent buffer so the first period does not underrun. */
    hr = IAudioRenderClient_GetBuffer(render, buffer_frames, &data);
    if (FAILED(hr)) return open_failed("GetBuffer", hr);
    hr = IAudioRenderClient_ReleaseBuffer(render, buffer_frames,
                                          AUDCLNT_BUFFERFLAGS_SILENT);
    if (FAILED(hr)) return open_failed("ReleaseBuffer", hr);

    hr = IAudioClient_Start(client);
    if (FAILED(hr)) return open_failed("Start", hr);

    /* Read the flag before clearing it: it decides which line to log. */
    was_out = outage_logged;
    outage_logged = 0;
    atomic_store(&audio_live, 1);
    if (was_out)
        ps2_log("audio: output device back, stream reopened");
    else
        ps2_log("audio: %u Hz %u-channel s16 WASAPI shared stream open (%u-frame buffer), SPU2 mixer attached",
                AUDIO_RATE, AUDIO_CHANNELS, (unsigned)buffer_frames);
    return S_OK;
}

static HRESULT audio_fill(void) {
    UINT32 pad, avail, done = 0;
    BYTE *data;
    HRESULT hr;

    hr = IAudioClient_GetCurrentPadding(client, &pad);
    if (FAILED(hr)) return hr;
    avail = buffer_frames - pad;
    if (!avail) return S_OK;

    hr = IAudioRenderClient_GetBuffer(render, avail, &data);
    if (FAILED(hr)) return hr;

    while (done < avail) {
        UINT32 n = avail - done;
        if (n > AUDIO_CHUNK) n = AUDIO_CHUNK;
        ps2_spu2_mix((s16 *)data + (size_t)done * AUDIO_CHANNELS, n);
        done += n;
    }
    /* data is invalid after ReleaseBuffer, so the tap has to copy it first. */
    wav_put(data, (int)(avail * AUDIO_CHANNELS * sizeof(s16)));
    hr = IAudioRenderClient_ReleaseBuffer(render, avail, 0);
    if (SUCCEEDED(hr))
        atomic_fetch_add(&audio_bytes, (u64)avail * AUDIO_CHANNELS * sizeof(s16));
    return hr;
}

/* Notification client: static, never freed. Callbacks arrive on an MMDevice
   thread, so they only set a flag and wake the render thread. */
static HRESULT STDMETHODCALLTYPE nc_QueryInterface(IMMNotificationClient *self,
                                                   REFIID riid, void **out) {
    if (IsEqualIID(riid, &audio_IID_IUnknown) ||
        IsEqualIID(riid, &IID_IMMNotificationClient)) {
        *out = self;
        return S_OK;
    }
    *out = NULL;
    return E_NOINTERFACE;
}
static ULONG STDMETHODCALLTYPE nc_AddRef(IMMNotificationClient *self) { (void)self; return 1; }
static ULONG STDMETHODCALLTYPE nc_Release(IMMNotificationClient *self) { (void)self; return 1; }
static HRESULT STDMETHODCALLTYPE nc_OnDeviceStateChanged(IMMNotificationClient *self,
                                                         LPCWSTR id, DWORD state) {
    (void)self; (void)id; (void)state;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE nc_OnDeviceAdded(IMMNotificationClient *self, LPCWSTR id) {
    (void)self; (void)id;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE nc_OnDeviceRemoved(IMMNotificationClient *self, LPCWSTR id) {
    (void)self; (void)id;
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE nc_OnDefaultDeviceChanged(IMMNotificationClient *self,
                                                           EDataFlow flow, ERole role,
                                                           LPCWSTR id) {
    (void)self; (void)id;
    /* Windows sends one call per role; one of them is enough. A removed
       current device surfaces as AUDCLNT_E_DEVICE_INVALIDATED instead. */
    if (flow == eRender && role == eConsole) {
        atomic_store(&audio_reopen, 1);
        SetEvent(wake_event);
    }
    return S_OK;
}
static HRESULT STDMETHODCALLTYPE nc_OnPropertyValueChanged(IMMNotificationClient *self,
                                                           LPCWSTR id, const PROPERTYKEY key) {
    (void)self; (void)id; (void)key;
    return S_OK;
}

static IMMNotificationClientVtbl notify_vtbl = {
    .QueryInterface         = nc_QueryInterface,
    .AddRef                 = nc_AddRef,
    .Release                = nc_Release,
    .OnDeviceStateChanged   = nc_OnDeviceStateChanged,
    .OnDeviceAdded          = nc_OnDeviceAdded,
    .OnDeviceRemoved        = nc_OnDeviceRemoved,
    .OnDefaultDeviceChanged = nc_OnDefaultDeviceChanged,
    .OnPropertyValueChanged = nc_OnPropertyValueChanged,
};
static IMMNotificationClient notify_client = { .lpVtbl = &notify_vtbl };

static DWORD WINAPI audio_main(LPVOID arg) {
    DWORD task_index = 0;
    HANDLE mmcss;
    HRESULT hr;
    int registered = 0;
    (void)arg;

    hr = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    if (FAILED(hr)) {
        ps2_log("audio: CoInitializeEx 0x%08lX", (unsigned long)hr);
        SetEvent(ready_event);
        return 0;
    }
    mmcss = AvSetMmThreadCharacteristicsW(L"Pro Audio", &task_index);

    hr = CoCreateInstance(&CLSID_MMDeviceEnumerator, NULL, CLSCTX_ALL,
                          &IID_IMMDeviceEnumerator, (void **)&enumr);
    if (FAILED(hr)) {
        ps2_log("audio: CoCreateInstance(MMDeviceEnumerator) 0x%08lX", (unsigned long)hr);
        SetEvent(ready_event);
        goto out;
    }

    hr = IMMDeviceEnumerator_RegisterEndpointNotificationCallback(enumr, &notify_client);
    if (FAILED(hr))
        ps2_log("audio: RegisterEndpointNotificationCallback 0x%08lX, default device changes not followed",
                (unsigned long)hr);
    else
        registered = 1;

    audio_open(NULL);
    SetEvent(ready_event);

    for (;;) {
        HANDLE h[2] = { wake_event, buf_event };
        DWORD w;
        if (atomic_load(&audio_quit)) break;
        if (atomic_exchange(&audio_reopen, 0) && client) {
            audio_close();
            audio_open(NULL);                   /* failure -> outage state */
            continue;
        }
        if (!client) {                          /* outage: poll for a device */
            WaitForSingleObject(wake_event, AUDIO_RETRY_MS);
            if (atomic_load(&audio_quit)) break;
            atomic_store(&audio_reopen, 0);
            audio_open(NULL);
            continue;
        }
        w = WaitForMultipleObjects(2, h, FALSE, AUDIO_WAIT_MS);
        if (w == WAIT_FAILED) {
            ps2_log("audio: wait failed (%lu)", (unsigned long)GetLastError());
            break;
        }
        if (w == WAIT_OBJECT_0) continue;       /* quit or reopen request */
        /* Buffer event or timeout: a timeout fill is harmless and detects loss. */
        hr = audio_fill();
        if (FAILED(hr)) {
            if (hr != AUDCLNT_E_BUFFER_TOO_LARGE) {
                ps2_log("audio: device lost (0x%08lX), reopening", (unsigned long)hr);
                audio_close();
                audio_open(NULL);
            }
        }
    }

out:
    audio_close();
    if (registered)
        IMMDeviceEnumerator_UnregisterEndpointNotificationCallback(enumr, &notify_client);
    if (enumr) { IMMDeviceEnumerator_Release(enumr); enumr = NULL; }
    if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
    CoUninitialize();
    return 0;
}

void ps2_audio_start(void) {
    if (atomic_load(&audio_started)) return;
    if (getenv("PS2_NO_AUDIO")) {
        ps2_log("audio: disabled by PS2_NO_AUDIO");
        return;
    }
    ps2_spu2_init();
    wav_open();
    wake_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    buf_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    ready_event = CreateEventW(NULL, TRUE, FALSE, NULL);
    audio_thread = (wake_event && buf_event && ready_event)
        ? CreateThread(NULL, 0, audio_main, NULL, 0, NULL) : NULL;
    if (!audio_thread) {
        ps2_log("audio: CreateThread failed");
        if (wake_event) CloseHandle(wake_event);
        if (buf_event) CloseHandle(buf_event);
        if (ready_event) CloseHandle(ready_event);
        wake_event = buf_event = ready_event = NULL;
        wav_close();
        return;
    }
    atomic_store(&audio_started, 1);
    /* Let the open log line land before the game boots. */
    WaitForSingleObject(ready_event, 3000);
}

int ps2_audio_running(void) { return atomic_load(&audio_live); }

void ps2_audio_stop(void) {
    if (!atomic_load(&audio_started)) return;
    atomic_store(&audio_quit, 1);
    SetEvent(wake_event);
    if (WaitForSingleObject(audio_thread, 2000) != WAIT_OBJECT_0) {
        /* The thread may still use the handles and the WAV file. */
        ps2_log("audio: render thread did not exit");
        return;
    }
    CloseHandle(audio_thread);
    CloseHandle(wake_event);
    CloseHandle(buf_event);
    CloseHandle(ready_event);
    audio_thread = wake_event = buf_event = ready_event = NULL;
    atomic_store(&audio_started, 0);
    atomic_store(&audio_quit, 0);
    wav_close();
}

void ps2_audio_report(void) {
    u64 bytes = atomic_load(&audio_bytes);
    if (!atomic_load(&audio_started) && !bytes) return;
    ps2_log("audio: %llu bytes drained by the device (%.1f s at %u Hz)",
            (unsigned long long)bytes,
            (double)bytes / (double)(AUDIO_RATE * AUDIO_CHANNELS
                                     * (int)sizeof(s16)),
            AUDIO_RATE);
}
