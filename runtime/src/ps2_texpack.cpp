// Texture pack support: the content key of a decoded texture, a PNG reader and
// writer on Windows WIC, the PS2_TEX_DUMP texture dump, and the replacement of
// decoded textures from a pack directory. See ps2_texpack.h for the API.
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
#include "ps2_settings.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <atomic>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

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
    unsigned long long written, present, failed;
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
            fputs("key,w,h,psm,tbp,tbw,cbp,x0,y0,field,src\n", g.csv);
    } else {
        ps2_log("texdump: cannot open index.csv in %s; textures are written without an index",
                g.dir);
    }
    ps2_log("texdump: PS2_TEX_DUMP is on, writing decoded textures to %s", g.dir);
}

// ---- Texture replacement ----------------------------------------------------

enum class State : uint8_t { Idle, Loaded, Rejected, Refused };

struct Entry {
    uint32_t w, h;
    std::wstring path;
    std::string name;   // file name, for logs; valid names are ASCII
    State state;
    uint32_t k;
    uint32_t handle;
};

// Index of the pack directory and the counters. Everything is behind one lock;
// decoding a file and creating its renderer image happen under it too.
struct Pack {
    SRWLOCK lock = SRWLOCK_INIT;
    std::atomic<int> state{0};   // 0 undecided, 1 active, 2 inactive
    ps2_texpack_image_fn image_fn;
    std::unordered_map<uint64_t, Entry> entries;
    std::unordered_set<uint64_t> sizes;   // (w << 32 | h) of every indexed file
    ps2_texpack_counters c;
    uint32_t loads_logged, dups_logged;
    bool refusal_logged;
};

Pack P;

constexpr int kMaxScanDepth = 16;
constexpr uint32_t kMaxFileDim = 4096;

uint64_t size_key(uint32_t w, uint32_t h) {
    return (uint64_t)w << 32 | h;
}

int hex_digit(wchar_t c) {
    if (c >= L'0' && c <= L'9') return c - L'0';
    if (c >= L'a' && c <= L'f') return c - L'a' + 10;
    if (c >= L'A' && c <= L'F') return c - L'A' + 10;
    return -1;
}

bool parse_dec(const wchar_t *&p, uint32_t &out) {
    uint32_t v = 0;
    int n = 0;
    while (*p >= L'0' && *p <= L'9') {
        if (++n > 5)
            return false;
        v = v * 10u + (uint32_t)(*p - L'0');
        p++;
    }
    out = v;
    return n > 0;
}

// "<16 hex digits>_<w>x<h>.png" with w and h in 1..kMaxFileDim.
bool parse_name(const wchar_t *n, uint64_t &key, uint32_t &w, uint32_t &h) {
    key = 0;
    for (int i = 0; i < 16; i++) {
        int d = hex_digit(n[i]);
        if (d < 0)
            return false;
        key = key << 4 | (uint64_t)d;
    }
    const wchar_t *p = n + 16;
    if (*p++ != L'_' || !parse_dec(p, w) || *p++ != L'x' || !parse_dec(p, h))
        return false;
    if (_wcsicmp(p, L".png") != 0)
        return false;
    return w >= 1 && w <= kMaxFileDim && h >= 1 && h <= kMaxFileDim;
}

bool is_png_name(const wchar_t *n) {
    size_t len = wcslen(n);
    return len >= 4 && _wcsicmp(n + len - 4, L".png") == 0;
}

// Indexes the .png files below dir. Files of one directory come before its
// subdirectories, so which file of a duplicate pair wins does not depend on how
// the file system orders mixed listings. Returns false when dir cannot be read.
bool scan(const std::wstring &dir, int depth) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW((dir + L"\\*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE)
        return false;
    std::vector<std::wstring> subdirs;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            // Reparse points are skipped so that a link cannot form a cycle.
            if (wcscmp(fd.cFileName, L".") != 0 && wcscmp(fd.cFileName, L"..") != 0 &&
                !(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) && depth < kMaxScanDepth)
                subdirs.push_back(dir + L"\\" + fd.cFileName);
            continue;
        }
        if (!is_png_name(fd.cFileName))
            continue;
        uint64_t key;
        uint32_t w, ph;
        if (!parse_name(fd.cFileName, key, w, ph)) {
            P.c.ignored++;
            continue;
        }
        std::string name;
        for (const wchar_t *c = fd.cFileName; *c; c++)
            name.push_back((char)*c);
        if (P.entries.count(key)) {
            P.c.duplicates++;
            if (P.dups_logged++ < 8)
                ps2_log("texpack: duplicate %s ignored; the first file found is used", name.c_str());
            continue;
        }
        Entry e;
        e.w = w;
        e.h = ph;
        e.path = dir + L"\\" + fd.cFileName;
        e.name = name;
        e.state = State::Idle;
        e.k = 0;
        e.handle = 0;
        P.entries.emplace(key, std::move(e));
        P.sizes.insert(size_key(w, ph));
        P.c.files++;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    for (const std::wstring &s : subdirs)
        scan(s, depth + 1);
    return true;
}

// Called with the lock held, once.
void activate() {
    int result = 2;
    const char *on = getenv("PS2_TEX_PACK");
    bool enabled = (on && *on) ? atoi(on) != 0 : ps2_cfg.texture_pack != 0;
    if (enabled) {
        const char *d = getenv("PS2_TEX_PACK_DIR");
        if (!d || !*d)
            d = "texpack";
        wchar_t wd[kPathCap];
        if (strlen(d) >= (size_t)kPathCap - 64 || !widen(d, wd, kPathCap)) {
            ps2_log("texpack: cannot read %s; texture replacement stays off", d);
        } else {
            size_t n = wcslen(wd);
            while (n > 1 && (wd[n - 1] == L'/' || wd[n - 1] == L'\\'))
                wd[--n] = 0;
            if (!scan(wd, 0)) {
                ps2_log("texpack: cannot read %s; texture replacement stays off", d);
            } else if (P.c.files == 0) {
                ps2_log("texpack: %s holds no replacement files; texture replacement stays off", d);
            } else {
                ps2_log("texpack: replacing textures from %s: %u files (%u names ignored, "
                        "%u duplicates)", d, P.c.files, P.c.ignored, P.c.duplicates);
                result = 1;
            }
        }
    }
    if (result != 1) {
        P.entries.clear();
        P.sizes.clear();
        P.c = ps2_texpack_counters{};
    }
    P.state.store(result, std::memory_order_release);
}

void reject(Entry &e, const char *reason) {
    e.state = State::Rejected;
    P.c.rejected++;
    ps2_log("texpack: rejected %s: %s", e.name.c_str(), reason);
}

// Reads and validates the file of e and hands it to the renderer. Returns the
// handle, or 0 with e marked rejected or refused.
uint32_t load(Entry &e, int mips) {
    uint32_t W = 0, H = 0;
    uint8_t *px = read_png_w(e.path.c_str(), &W, &H);
    if (!px) {
        reject(e, "the file cannot be decoded");
        return 0;
    }
    const char *why = nullptr;
    if (W % e.w != 0 || H % e.h != 0)
        why = "the size is not a whole multiple of the name's size";
    else if (W / e.w != H / e.h)
        why = "the width and height scale differently";
    else if (W / e.w > PS2_TEXPACK_MAX_SCALE)
        why = "the scale is above the maximum of 8";
    else if ((uint64_t)W * H * 4u > PS2_TEXPACK_MAX_IMAGE_BYTES)
        why = "the image is larger than 8 MiB";
    if (why) {
        free(px);
        reject(e, why);
        return 0;
    }
    uint32_t k = W / e.w;
    uint32_t handle = P.image_fn(px, W, H, k, mips);   // takes px in every case
    if (!handle) {
        e.state = State::Refused;
        P.c.refused++;
        if (!P.refusal_logged) {
            P.refusal_logged = true;
            ps2_log("texpack: the renderer refused %s (replacement store full)", e.name.c_str());
        }
        return 0;
    }
    e.state = State::Loaded;
    e.k = k;
    e.handle = handle;
    P.c.loaded++;
    if (P.loads_logged++ < 16)
        ps2_log("texpack: loaded %s at %ux", e.name.c_str(), k);
    return handle;
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
                fprintf(g.csv, "%016llx,%u,%u,%u,%u,%u,%u,%u,%u,%u,%s\n", (unsigned long long)key, w,
                        h, (unsigned)((t >> 20) & 0x3Fu), (unsigned)(t & 0x3FFFu),
                        (unsigned)((t >> 14) & 0x3Fu), (unsigned)((t >> 37) & 0x3FFFu), meta->x0,
                        meta->y0, meta->field, meta->src == PS2_TEXPACK_SRC_GS ? "gs" : "rec");
                fflush(g.csv);
            }
        } else {
            g.failed++;
        }
    }
    ReleaseSRWLockExclusive(&g.lock);
}

void ps2_texpack_set_image_fn(ps2_texpack_image_fn fn) {
    AcquireSRWLockExclusive(&P.lock);
    P.image_fn = fn;
    ReleaseSRWLockExclusive(&P.lock);
}

int ps2_texpack_active(void) {
    int s = P.state.load(std::memory_order_acquire);
    if (s)
        return s == 1;
    AcquireSRWLockExclusive(&P.lock);
    if (!P.state.load(std::memory_order_relaxed))
        activate();
    s = P.state.load(std::memory_order_relaxed);
    ReleaseSRWLockExclusive(&P.lock);
    return s == 1;
}

uint32_t ps2_texpack_lookup(const uint8_t *rgba, uint32_t w, uint32_t h, int mips,
                            uint32_t *scale) {
    if (!ps2_texpack_active())
        return 0;
    uint32_t result = 0;
    AcquireSRWLockExclusive(&P.lock);
    do {
        if (!P.image_fn || !P.sizes.count(size_key(w, h)))
            break;
        uint64_t key = ps2_texpack_key(rgba, w, h);
        P.c.lookups++;
        auto it = P.entries.find(key);
        if (it == P.entries.end() || it->second.w != w || it->second.h != h) {
            P.c.missing++;
            break;
        }
        Entry &e = it->second;
        if (e.state == State::Rejected || e.state == State::Refused)
            break;
        if (e.state == State::Idle && !load(e, mips))
            break;
        P.c.replaced++;
        *scale = e.k;
        result = e.handle;
    } while (0);
    ReleaseSRWLockExclusive(&P.lock);
    return result;
}

void ps2_texpack_counters_get(ps2_texpack_counters *out) {
    AcquireSRWLockExclusive(&P.lock);
    *out = P.c;
    ReleaseSRWLockExclusive(&P.lock);
}

void ps2_texpack_report(void) {
    read_env();
    if (g.enabled) {
        AcquireSRWLockExclusive(&g.lock);
        if (g.written || g.present || g.failed)
            ps2_log("texdump: %llu textures written, %llu already present, %llu write failures",
                    g.written, g.present, g.failed);
        ReleaseSRWLockExclusive(&g.lock);
    }
    // The state is not decided here: before the first texture decode there is
    // nothing to report.
    if (P.state.load(std::memory_order_acquire) != 1)
        return;
    ps2_texpack_counters c;
    ps2_texpack_counters_get(&c);
    ps2_log("texpack: %llu textures looked up, %llu replaced from %u files, %llu without a file, "
            "%u files rejected, %u refused",
            (unsigned long long)c.lookups, (unsigned long long)c.replaced, c.loaded,
            (unsigned long long)c.missing, c.rejected, c.refused);
}

}  // extern "C"
