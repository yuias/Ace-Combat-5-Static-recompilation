#ifndef PS2_OS_H
#define PS2_OS_H
/* Host threads, locks, clocks and directory listing, implemented on Win32 in
   ps2_os.c. No windows.h here, so includers never see its macros. */
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Same size and layout as SRWLOCK / CONDITION_VARIABLE; all-zero is the
   initialized state, so static and memset-zeroed objects are ready to use. */
typedef struct { void *opaque; } ps2_mutex_t;
typedef struct { void *opaque; } ps2_cond_t;
typedef void *ps2_thread_t;                 /* thread HANDLE */

#define PS2_MUTEX_INIT { 0 }
#define PS2_COND_INIT  { 0 }

/* Exclusive and not reentrant; unlock on the thread that locked. */
void ps2_mutex_lock(ps2_mutex_t *m);
int  ps2_mutex_trylock(ps2_mutex_t *m);    /* 0 = acquired, 1 = busy */
void ps2_mutex_unlock(ps2_mutex_t *m);

void ps2_cond_init(ps2_cond_t *c);
void ps2_cond_wait(ps2_cond_t *c, ps2_mutex_t *m);   /* may wake spuriously */
/* Waits at most ms milliseconds. 0 = woken (possibly spuriously), 1 = timed out. */
int  ps2_cond_wait_ms(ps2_cond_t *c, ps2_mutex_t *m, unsigned ms);
void ps2_cond_signal(ps2_cond_t *c);
void ps2_cond_broadcast(ps2_cond_t *c);

/* Default stack size (the exe's), like pthread_create with NULL attributes.
   Returns 0 on success, -1 on failure. */
int  ps2_thread_create(ps2_thread_t *t, void *(*fn)(void *), void *arg);
void ps2_thread_join(ps2_thread_t t);      /* waits, then closes the handle */
void ps2_thread_detach(ps2_thread_t t);    /* closes the handle */
/* Ends the calling thread without unwinding; only for threads made by
   ps2_thread_create. */
__attribute__((noreturn)) void ps2_thread_exit(void);
unsigned long ps2_thread_self_id(void);

uint64_t ps2_mono_ns(void);                /* monotonic, arbitrary origin */
void ps2_sleep_ms(unsigned ms);

/* Directory of the running exe with a trailing backslash, in the ANSI code
   page (the encoding narrow fopen expects); "" if unknown. Computed once;
   callable from any thread. */
const char *ps2_exe_dir(void);

typedef struct ps2_dir ps2_dir;
ps2_dir *ps2_dir_open(const char *path);   /* NULL if path is empty or not listable */
/* Next entry name, "." and ".." included; valid until the next call or
   close. NULL at the end. */
const char *ps2_dir_next(ps2_dir *d);
void ps2_dir_close(ps2_dir *d);            /* accepts NULL */

#ifdef __cplusplus
}
#endif
#endif
