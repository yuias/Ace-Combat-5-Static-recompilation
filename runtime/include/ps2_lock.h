#ifndef PS2_LOCK_H
#define PS2_LOCK_H
/* Include after the project headers: windows.h defines macros (min, max,
   near, far, small) that project code must not see first. */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

/* Exclusive-only SRW lock. Not reentrant: never take a lock you hold. */
typedef SRWLOCK ps2_lock;
#define PS2_LOCK_INIT SRWLOCK_INIT

static inline void ps2_lock_take(ps2_lock *l) { AcquireSRWLockExclusive(l); }
static inline void ps2_lock_release(ps2_lock *l) { ReleaseSRWLockExclusive(l); }
#endif
