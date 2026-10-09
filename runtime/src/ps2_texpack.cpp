// Texture pack support: the content key of a decoded texture, a PNG reader and
// writer on Windows WIC, and the PS2_TEX_DUMP texture dump. See ps2_texpack.h
// for the API.
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <wincodec.h>
#include "ps2_texpack.h"
#include "ps2_runtime.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unordered_set>

namespace {

// COM is initialised per calling thread, and so is the WIC factory: a COM
// interface pointer is only safe to use on the apartment that created it.
thread_local bool tl_com_tried;
thread_local IWICImagingFactory *tl_factory;

IWICImagingFactory *factory() {
    if (tl_factory)
        return tl_factory;
    if (!tl_com_tried) {
        tl_com_tried = true;
        // RPC_E_CHANGED_MODE: the thread already has an apartment of the other
        // kind, which WIC works in as well.
        HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        (void)hr;
    }
    IWICImagingFactory *f = nullptr;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(&f))))
        return nullptr;
    tl_factory = f;
    return f;
}

template <class T> void release(T *&p) {
    if (p) {
        p->Release();
        p = nullptr;
    }
}

bool widen(const char *utf8, wchar_t *out, int cap) {
    return MultiByteToWideChar(CP_UTF8, 0, utf8, -1, out, cap) > 0;
}

constexpr int kPathCap = 1024;

bool write_png_w(const wchar_t *path, const uint8_t *rgba, uint32_t w, uint32_t h) {
    if (!w || !h || (uint64_t)w * h > 0x10000000ull)
        return false;
    IWICImagingFactory *f = factory();
    if (!f)
        return false;

    // The PNG encoder takes BGRA; swap the channels in a copy.
    size_t stride = (size_t)w * 4u;
    uint8_t *bgra = (uint8_t *)malloc(stride * h);
    if (!bgra)
        return false;
    for (size_t i = 0, n = stride * h; i < n; i += 4) {
        bgra[i + 0] = rgba[i + 2];
        bgra[i + 1] = rgba[i + 1];
        bgra[i + 2] = rgba[i + 0];
        bgra[i + 3] = rgba[i + 3];
    }

    // Written under a temporary name so that a file under the final name is
    // always complete; the dump treats an existing file as done.
    wchar_t tmp[kPathCap + 8];
    swprintf(tmp, sizeof tmp / sizeof tmp[0], L"%ls.tmp", path);

    IWICStream *stream = nullptr;
    IWICBitmapEncoder *enc = nullptr;
    IWICBitmapFrameEncode *frame = nullptr;
    IPropertyBag2 *props = nullptr;
    bool ok = false;
    do {
        if (FAILED(f->CreateStream(&stream))) break;
        if (FAILED(stream->InitializeFromFilename(tmp, GENERIC_WRITE))) break;
        if (FAILED(f->CreateEncoder(GUID_ContainerFormatPng, nullptr, &enc))) break;
        if (FAILED(enc->Initialize(stream, WICBitmapEncoderNoCache))) break;
        if (FAILED(enc->CreateNewFrame(&frame, &props))) break;
        if (FAILED(frame->Initialize(props))) break;
        if (FAILED(frame->SetSize(w, h))) break;
        WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
        if (FAILED(frame->SetPixelFormat(&fmt)) || fmt != GUID_WICPixelFormat32bppBGRA) break;
        if (FAILED(frame->WritePixels(h, (UINT)stride, (UINT)(stride * h), bgra))) break;
        if (FAILED(frame->Commit())) break;
        if (FAILED(enc->Commit())) break;
        ok = true;
    } while (0);
    release(props);
    release(frame);
    release(enc);
    release(stream);  // closes the file
    free(bgra);
    if (ok)
        ok = MoveFileExW(tmp, path, MOVEFILE_REPLACE_EXISTING) != 0;
    if (!ok)
        DeleteFileW(tmp);
    return ok;
}

uint8_t *read_png_w(const wchar_t *path, uint32_t *ow, uint32_t *oh) {
    IWICImagingFactory *f = factory();
    if (!f)
        return nullptr;
    IWICBitmapDecoder *dec = nullptr;
    IWICBitmapFrameDecode *frame = nullptr;
    IWICFormatConverter *conv = nullptr;
    uint8_t *out = nullptr;
    do {
        if (FAILED(f->CreateDecoderFromFilename(path, nullptr, GENERIC_READ,
                                                WICDecodeMetadataCacheOnDemand, &dec))) break;
        if (FAILED(dec->GetFrame(0, &frame))) break;
        UINT w = 0, h = 0;
        if (FAILED(frame->GetSize(&w, &h)) || !w || !h || (uint64_t)w * h > 0x10000000ull) break;
        if (FAILED(f->CreateFormatConverter(&conv))) break;
        if (FAILED(conv->Initialize(frame, GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone,
                                    nullptr, 0.0, WICBitmapPaletteTypeCustom))) break;
        size_t stride = (size_t)w * 4u;
        out = (uint8_t *)malloc(stride * h);
        if (!out) break;
        if (FAILED(conv->CopyPixels(nullptr, (UINT)stride, (UINT)(stride * h), out))) {
            free(out);
            out = nullptr;
            break;
        }
        for (size_t i = 0, n = stride * h; i < n; i += 4) {
            uint8_t t = out[i];
            out[i] = out[i + 2];
            out[i + 2] = t;
        }
        *ow = w;
        *oh = h;
    } while (0);
    release(conv);
    release(frame);
    release(dec);
    return out;
}

// State of the dump. The game calls in from one thread, but the lock costs
// nothing at the rate textures are dumped.
struct Dump {
    SRWLOCK lock = SRWLOCK_INIT;
    bool env_read;
    bool enabled;
    bool started;       // directory prepared and announced
    bool usable;        // directory exists
    char dir[kPathCap];
    wchar_t wdir[kPathCap];
    FILE *csv;
    std::unordered_set<uint64_t> seen;
    unsigned long long written, present, failed, uncovered;
};

Dump g;

void read_env() {
    if (g.env_read)
        return;
    g.env_read = true;
    const char *e = getenv("PS2_TEX_DUMP");
    if (!e || !*e || strlen(e) >= sizeof g.dir)
        return;
    strcpy(g.dir, e);
    if (!widen(g.dir, g.wdir, kPathCap))
        return;
    // A trailing separator would break the path joins below.
    size_t n = wcslen(g.wdir);
    while (n > 1 && (g.wdir[n - 1] == L'/' || g.wdir[n - 1] == L'\\'))
        g.wdir[--n] = 0;
    g.enabled = true;
}

bool make_dirs(wchar_t *path) {
    for (wchar_t *p = path + 1; *p; p++) {
        if (*p != L'/' && *p != L'\\')
            continue;
        wchar_t c = *p;
        *p = 0;
        // A drive root such as "C:" is not creatable; the failure is harmless.
        CreateDirectoryW(path, nullptr);
        *p = c;
    }
    if (!CreateDirectoryW(path, nullptr) && GetLastError() != ERROR_ALREADY_EXISTS)
        return false;
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

void start() {
    g.started = true;
    wchar_t tmp[kPathCap];
    wcscpy(tmp, g.wdir);
    g.usable = make_dirs(tmp);
    if (!g.usable) {
        ps2_log("texdump: cannot create the directory %s; nothing will be written", g.dir);
        return;
    }
    wchar_t csv[kPathCap + 16];
    swprintf(csv, sizeof csv / sizeof csv[0], L"%ls\\index.csv", g.wdir);
    g.csv = _wfopen(csv, L"ab");
    if (g.csv) {
        fseek(g.csv, 0, SEEK_END);
        if (ftell(g.csv) == 0)
            fputs("key,w,h,psm,tbp,tbw,cbp,x0,y0,field\n", g.csv);
    } else {
        ps2_log("texdump: cannot open index.csv in %s; textures are written without an index",
                g.dir);
    }
    ps2_log("texdump: PS2_TEX_DUMP is on, writing decoded textures to %s", g.dir);
}

}  // namespace

extern "C" {

uint64_t ps2_texpack_key(const uint8_t *rgba, uint32_t w, uint32_t h) {
    constexpr uint64_t kPrime = 0x100000001B3ull;
    uint64_t k = 0xCBF29CE484222325ull;
    for (size_t i = 0, n = (size_t)w * h * 4u; i < n; i++)
        k = (k ^ rgba[i]) * kPrime;
    for (uint32_t v : {w, h})
        for (int s = 0; s < 32; s += 8)
            k = (k ^ ((v >> s) & 0xFFu)) * kPrime;
    return k;
}

int ps2_texpack_write_png(const char *path_utf8, const uint8_t *rgba, uint32_t w, uint32_t h) {
    wchar_t wp[kPathCap];
    if (!widen(path_utf8, wp, kPathCap))
        return 0;
    return write_png_w(wp, rgba, w, h) ? 1 : 0;
}

uint8_t *ps2_texpack_read_png(const char *path_utf8, uint32_t *w, uint32_t *h) {
    wchar_t wp[kPathCap];
    if (!widen(path_utf8, wp, kPathCap))
        return nullptr;
    return read_png_w(wp, w, h);
}

int ps2_texpack_dump_enabled(void) {
    read_env();
    return g.enabled ? 1 : 0;
}

void ps2_texpack_dump(const uint8_t *rgba, uint32_t w, uint32_t h, const ps2_texpack_meta *meta) {
    read_env();
    if (!g.enabled)
        return;
    uint64_t key = ps2_texpack_key(rgba, w, h);
    AcquireSRWLockExclusive(&g.lock);
    if (!g.started)
        start();
    if (g.usable && g.seen.insert(key).second) {
        wchar_t path[kPathCap + 48];
        swprintf(path, sizeof path / sizeof path[0], L"%ls\\%016llx_%ux%u.png", g.wdir,
                 (unsigned long long)key, w, h);
        if (GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES) {
            g.present++;
        } else if (write_png_w(path, rgba, w, h)) {
            g.written++;
            if (g.csv) {
                uint64_t t = meta->tex0;
                fprintf(g.csv, "%016llx,%u,%u,%u,%u,%u,%u,%u,%u,%u\n", (unsigned long long)key, w,
                        h, (unsigned)((t >> 20) & 0x3Fu), (unsigned)(t & 0x3FFFu),
                        (unsigned)((t >> 14) & 0x3Fu), (unsigned)((t >> 37) & 0x3FFFu), meta->x0,
                        meta->y0, meta->field);
                fflush(g.csv);
            }
        } else {
            g.failed++;
        }
    }
    ReleaseSRWLockExclusive(&g.lock);
}

void ps2_texpack_note_uncovered(void) {
    read_env();
    if (!g.enabled)
        return;
    AcquireSRWLockExclusive(&g.lock);
    g.uncovered++;
    ReleaseSRWLockExclusive(&g.lock);
}

void ps2_texpack_report(void) {
    read_env();
    if (!g.enabled)
        return;
    AcquireSRWLockExclusive(&g.lock);
    if (g.written || g.present || g.failed || g.uncovered)
        ps2_log("texdump: %llu textures written, %llu already present, %llu write failures; "
                "%llu bindings not covered (GS-decode fallback)",
                g.written, g.present, g.failed, g.uncovered);
    ReleaseSRWLockExclusive(&g.lock);
}

}  // extern "C"
