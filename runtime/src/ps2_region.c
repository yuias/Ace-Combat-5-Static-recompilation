#include "ps2_region.h"
#include "ps2_vfs.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

const char *ps2_region_config_path(const char *name, char *buf, size_t n) {
    snprintf(buf, n, "%s/%s", ps2_region_config, name);
    return buf;
}

/* Copies the BOOT2 executable name ("SLUS_208.51") out of SYSTEM.CNF text.
   Returns 0 when there is no BOOT2 line. */
static int boot2_exe(const char *text, char *out, size_t cap) {
    const char *p = text;
    while (*p) {
        const char *eol = p + strcspn(p, "\r\n");
        const char *q = p;
        while (q < eol && (*q == ' ' || *q == '\t')) q++;
        if (eol - q >= 5 && !strncmp(q, "BOOT2", 5)) {
            q += 5;
            while (q < eol && (*q == ' ' || *q == '\t')) q++;
            if (q < eol && *q == '=') {
                q++;
                while (q < eol && (*q == ' ' || *q == '\t')) q++;
                /* "cdrom0:\NAME;1" -- skip the device and leading slashes */
                const char *colon = memchr(q, ':', (size_t)(eol - q));
                if (colon) q = colon + 1;
                while (q < eol && (*q == '\\' || *q == '/')) q++;
                size_t n = 0;
                while (q + n < eol && q[n] != ';' && q[n] != ' ' && q[n] != '\t')
                    n++;
                if (!n || n >= cap) return 0;
                memcpy(out, q, n);
                out[n] = '\0';
                return 1;
            }
        }
        p = eol + strspn(eol, "\r\n");
    }
    return 0;
}

static int same_name(const char *a, const char *b) {
    for (; *a && *b; a++, b++)
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
    return *a == *b;
}

int ps2_region_check_disc(void) {
    char text[PS2_VFS_SECTOR + 1];
    char exe[64];
    const ps2_disc_file *f = ps2_vfs_find("\\SYSTEM.CNF;1");
    int n;

    if (!f) return 0;
    /* SYSTEM.CNF is far smaller than one sector. */
    n = ps2_vfs_read(f, 0, f->size < PS2_VFS_SECTOR ? f->size : PS2_VFS_SECTOR,
                     text);
    if (n <= 0) return 0;
    text[n] = '\0';
    if (!boot2_exe(text, exe, sizeof exe)) return 0;

    if (same_name(exe, ps2_region_exe)) {
        ps2_log("region: %s, disc boots %s", ps2_game_id, exe);
        return 0;
    }
    ps2_log("region: this build is for %s (%s), the disc boots %s; "
            "recompile for that disc", ps2_game_id, ps2_region_exe, exe);
    {
        const char *allow = getenv("PS2_ALLOW_REGION_MISMATCH");
        if (allow && *allow && strcmp(allow, "0") != 0) {
            ps2_log("region: PS2_ALLOW_REGION_MISMATCH is set, continuing");
            return 0;
        }
    }
    ps2_log("region: set PS2_ALLOW_REGION_MISMATCH=1 to run anyway");
    return -1;
}
