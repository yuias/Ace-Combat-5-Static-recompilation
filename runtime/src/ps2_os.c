#include "ps2_os.h"

#include <stdlib.h>
#include <string.h>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <process.h>

/* The header hides windows.h, so the opaque types must stay layout-compatible
   with the Win32 ones they are cast to. */
_Static_assert(sizeof(ps2_mutex_t) == sizeof(SRWLOCK) &&
               _Alignof(ps2_mutex_t) == _Alignof(SRWLOCK),
               "ps2_mutex_t must match SRWLOCK");
_Static_assert(sizeof(ps2_cond_t) == sizeof(CONDITION_VARIABLE) &&
               _Alignof(ps2_cond_t) == _Alignof(CONDITION_VARIABLE),
               "ps2_cond_t must match CONDITION_VARIABLE");

void ps2_mutex_lock(ps2_mutex_t *m)
{
    AcquireSRWLockExclusive((PSRWLOCK)m);
}

int ps2_mutex_trylock(ps2_mutex_t *m)
{
    return TryAcquireSRWLockExclusive((PSRWLOCK)m) ? 0 : 1;
}

void ps2_mutex_unlock(ps2_mutex_t *m)
{
    ReleaseSRWLockExclusive((PSRWLOCK)m);
}

void ps2_cond_init(ps2_cond_t *c)
{
    InitializeConditionVariable((PCONDITION_VARIABLE)c);
}

void ps2_cond_wait(ps2_cond_t *c, ps2_mutex_t *m)
{
    SleepConditionVariableSRW((PCONDITION_VARIABLE)c, (PSRWLOCK)m, INFINITE, 0);
}

int ps2_cond_wait_ms(ps2_cond_t *c, ps2_mutex_t *m, unsigned ms)
{
    return SleepConditionVariableSRW((PCONDITION_VARIABLE)c, (PSRWLOCK)m, ms, 0) ? 0 : 1;
}

void ps2_cond_signal(ps2_cond_t *c)
{
    WakeConditionVariable((PCONDITION_VARIABLE)c);
}

void ps2_cond_broadcast(ps2_cond_t *c)
{
    WakeAllConditionVariable((PCONDITION_VARIABLE)c);
}

typedef struct {
    void *(*fn)(void *);
    void *arg;
} thread_start;

static unsigned __stdcall thread_main(void *p)
{
    thread_start s = *(thread_start *)p;
    free(p);
    s.fn(s.arg);
    return 0;
}

int ps2_thread_create(ps2_thread_t *t, void *(*fn)(void *), void *arg)
{
    thread_start *s = (thread_start *)malloc(sizeof(*s));
    if (!s)
        return -1;
    s->fn = fn;
    s->arg = arg;
    /* Stack size 0 = the exe's default reserve. */
    uintptr_t h = _beginthreadex(NULL, 0, thread_main, s, 0, NULL);
    if (!h) {
        free(s);
        return -1;
    }
    *t = (ps2_thread_t)h;
    return 0;
}

void ps2_thread_join(ps2_thread_t t)
{
    WaitForSingleObject((HANDLE)t, INFINITE);
    CloseHandle((HANDLE)t);
}

void ps2_thread_detach(ps2_thread_t t)
{
    CloseHandle((HANDLE)t);
}

void ps2_thread_exit(void)
{
    _endthreadex(0);
    __builtin_unreachable();
}

unsigned long ps2_thread_self_id(void)
{
    return (unsigned long)GetCurrentThreadId();
}

uint64_t ps2_mono_ns(void)
{
    LARGE_INTEGER c, f;
    QueryPerformanceCounter(&c);
    QueryPerformanceFrequency(&f);
    uint64_t cnt = (uint64_t)c.QuadPart;
    uint64_t freq = (uint64_t)f.QuadPart;
    /* Split to avoid overflowing 64 bits when the counter is large. */
    return cnt / freq * 1000000000ull + cnt % freq * 1000000000ull / freq;
}

void ps2_sleep_ms(unsigned ms)
{
    Sleep(ms);
}

static INIT_ONCE exe_dir_once = INIT_ONCE_STATIC_INIT;
static char exe_dir_buf[1024];

static BOOL CALLBACK exe_dir_init(PINIT_ONCE once, PVOID param, PVOID *ctx)
{
    (void)once; (void)param; (void)ctx;
    DWORD n = GetModuleFileNameA(NULL, exe_dir_buf, sizeof exe_dir_buf);
    /* 0 is a failure and == size is a truncated path; neither is usable. */
    if (n == 0 || n >= sizeof exe_dir_buf) {
        exe_dir_buf[0] = '\0';
        return TRUE;
    }
    char *cut = NULL;
    for (char *p = exe_dir_buf; *p; p++)
        if (*p == '\\' || *p == '/')
            cut = p;
    if (cut)
        cut[1] = '\0';
    else
        exe_dir_buf[0] = '\0';
    return TRUE;
}

const char *ps2_exe_dir(void)
{
    InitOnceExecuteOnce(&exe_dir_once, exe_dir_init, NULL, NULL);
    return exe_dir_buf;
}

struct ps2_dir {
    HANDLE h;
    WIN32_FIND_DATAA fd;
    int pending;
};

ps2_dir *ps2_dir_open(const char *path)
{
    if (!path || !path[0])
        return NULL;
    size_t n = strlen(path);
    char *pattern = (char *)malloc(n + 3);
    if (!pattern)
        return NULL;
    memcpy(pattern, path, n);
    if (path[n - 1] != '/' && path[n - 1] != '\\')
        pattern[n++] = '/';
    pattern[n++] = '*';
    pattern[n] = '\0';

    ps2_dir *d = (ps2_dir *)calloc(1, sizeof(*d));
    if (!d) {
        free(pattern);
        return NULL;
    }
    d->h = FindFirstFileA(pattern, &d->fd);
    free(pattern);
    if (d->h == INVALID_HANDLE_VALUE) {
        free(d);
        return NULL;
    }
    d->pending = 1;
    return d;
}

const char *ps2_dir_next(ps2_dir *d)
{
    if (d->pending) {
        d->pending = 0;
        return d->fd.cFileName;
    }
    if (!FindNextFileA(d->h, &d->fd))
        return NULL;
    return d->fd.cFileName;
}

void ps2_dir_close(ps2_dir *d)
{
    if (!d)
        return;
    FindClose(d->h);
    free(d);
}
