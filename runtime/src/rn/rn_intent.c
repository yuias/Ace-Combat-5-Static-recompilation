#include "ps2_hle.h"
#include "ps2_hook.h"
#include "ps2_gfxq.h"
#include "ps2_capture.h"
#include "ps2_addr.h"
#include "rn_int.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int rn_intents_pending;

#define F_PRIM_WRITER PS2_A(RN_PRIM_WRITER)
#define F_RECT_HELPER PS2_A(RN_RECT_HELPER)
#define F_LINE_HELPER PS2_A(RN_LINE_HELPER)

/* The size comes from the list's _END entry, so a JP body of a different
   length follows its region; the entry words are checked through
   ps2_addr_code_matches(). */
typedef struct { u32 addr, size; int aid; } known_fn;
#define KNOWN_FN(n) { PS2_A(n), PS2_A(n##_END) - PS2_A(n), PS2_AID_##n }

static const known_fn fn_writer = KNOWN_FN(RN_PRIM_WRITER);
static const known_fn fn_rect = KNOWN_FN(RN_RECT_HELPER);
static const known_fn fn_line = KNOWN_FN(RN_LINE_HELPER);

#define PEND_MAX 8192u
#define ARENA_BYTES (4u << 20)
typedef struct {
    u32 start, end;
    u32 off, len;
    u32 emitted;
} pend;
static pend pends[PEND_MAX];
static u32 n_pend;
static int pends_sorted = 1;
static u8 *arena;
static u32 arena_n;

static u32 n_clouds;
static int claim_open;
static u32 claim_start, claim_end;

static u32 helper_caller[2];
static int group_depth;

static struct {
    u64 recorded, emitted, dropped, overflow, frames;
    u64 bytes;
    u64 walked_out;
    u64 depth_resets;
} st_int;

static int by_start(const void *a, const void *b) {
    u32 x = ((const pend *)a)->start, y = ((const pend *)b)->start;
    return x < y ? -1 : x > y;
}

static void send(const u8 *rec, u32 len) {
    if (g_cap_deep) ps2_cap_intent(rec, len);
    ps2_gfxq_intent(rec, len);
}

static void send_claim_end(void) {
    rn_intent_hdr h;
    memset(&h, 0, sizeof h);
    h.kind = RN_INT_CLAIM_END;
    h.len = sizeof h;
    send((const u8 *)&h, sizeof h);
    claim_open = 0;
}

static void update_pending(void) {
    rn_intents_pending = n_pend > 0 || claim_open;
}

static u8 *pend_add(u32 start, u32 end, u32 len) {
    pend *p;
    u8 *rec;
    if (n_pend >= PEND_MAX || arena_n + len > ARENA_BYTES || !arena) {
        st_int.overflow++;
        return NULL;
    }
    p = &pends[n_pend];
    p->start = start;
    p->end = end;
    p->off = arena_n;
    p->len = len;
    p->emitted = 0;
    if (n_pend && start < pends[n_pend - 1].start) pends_sorted = 0;
    rec = arena + arena_n;
    arena_n += len;
    n_pend++;
    st_int.recorded++;
    st_int.bytes += len;
    update_pending();
    return rec;
}

static void hdr_init(rn_intent_hdr *h, u16 kind, u32 len, u32 site, u32 pkt) {
    memset(h, 0, sizeof *h);
    h->kind = kind;
    h->len = len;
    h->site = site;
    h->emitter = rn_tap_open_emitter(pkt);
}

void rn_intent_frame_end(void) {
    if (claim_open) send_claim_end();
    if (group_depth) {
        if (!st_int.depth_resets++)
            ps2_log("rn: a frame ended inside a 2D group writer (%d deep); reset",
                    group_depth);
        group_depth = 0;
    }
    for (u32 i = 0; i < n_pend; i++)
        if (!pends[i].emitted) st_int.dropped++;
    n_pend = 0;
    arena_n = 0;
    n_clouds = 0;
    pends_sorted = 1;
    st_int.frames++;
    update_pending();
}

void rn_dma_transfer_slow(int ch, u32 madr, u32 qwc, int after) {
    (void)ch;
    if (after) {
        if (claim_open && madr >= claim_start && madr < claim_end + 16u
            && madr + qwc * 16u + 16u >= claim_end) send_claim_end();
        update_pending();
        return;
    }
    if (!n_pend) return;
    if (!pends_sorted) {
        qsort(pends, n_pend, sizeof pends[0], by_start);
        pends_sorted = 1;
    }
    {
        u32 lo = 0, hi = n_pend;
        while (hi - lo > 1u) {
            u32 mid = lo + (hi - lo) / 2u;
            if (pends[mid].start <= madr) lo = mid;
            else hi = mid;
        }
        if (pends[lo].start <= madr && madr < pends[lo].end && !pends[lo].emitted) {
            pend *p = &pends[lo];
            if (claim_open) send_claim_end();
            send(arena + p->off, p->len);
            p->emitted = 1;
            st_int.emitted++;
            claim_open = 1;
            claim_start = p->start;
            claim_end = p->end;
        }
    }
    update_pending();
}

void rn_intent_tag(u32 tadr, u32 emitter) {
    if (!claim_open) return;
    if (tadr && tadr >= claim_start && tadr < claim_end) return;
    if (tadr && (emitter == RN_EMIT_NONE || emitter == RN_EMIT_OFFCHAIN)) return;
    st_int.walked_out++;
    send_claim_end();
    update_pending();
}

static int tap_helper(ps2_ctx *ctx, void *u) {
    helper_caller[(uintptr_t)u] = (u32)ctx->r[31].ud[0];
    return 0;
}

static u32 writer_ra;
static int tap_writer_enter(ps2_ctx *ctx, void *u) {
    (void)u;
    writer_ra = (u32)ctx->r[31].ud[0];
    return 0;
}

static int tap_writer(ps2_ctx *ctx, void *u) {
    int ok0 = 0, ok1 = 0, ok2 = 0, ok3 = 0;
    u32 pkt = ps2_hook_entry_arg(0, &ok0);
    u32 prim = ps2_hook_entry_arg(1, &ok1) & 0xFFFFu;
    s32 count = (s16)(ps2_hook_entry_arg(2, &ok2) & 0xFFFFu);
    u32 verts = ps2_hook_entry_arg(3, &ok3);
    u32 end = (u32)ctx->r[2].ud[0];
    u32 site = writer_ra;
    u32 need;
    rn_intent_hdr h;
    rn_int_prim2d body;
    u8 *rec;
    (void)u;
    if (group_depth > 0) return 0;
    if (!ok0 || !ok1 || !ok2 || !ok3 || count <= 0 || count > 4096 || end <= pkt)
        return 0;
    need = sizeof h + sizeof body + (u32)count * sizeof(rn_vtx2d);
    if (!(rec = pend_add(pkt, end, need))) return 0;
    if (site - fn_rect.addr < fn_rect.size) site = helper_caller[0];
    else if (site - fn_line.addr < fn_line.size) site = helper_caller[1];
    hdr_init(&h, RN_INT_PRIM2D, need, site, pkt);
    memset(&body, 0, sizeof body);
    body.prim = prim;
    body.count = (u32)count;
    if (prim & 0x10u) body.tex0 = ((u64)ps2_r32(pkt + 44u) << 32) | ps2_r32(pkt + 40u);
    memcpy(rec, &h, sizeof h);
    memcpy(rec + sizeof h, &body, sizeof body);
    ps2_get_mem(rec + sizeof h + sizeof body, verts, (size_t)count * sizeof(rn_vtx2d));
    return 0;
}

typedef struct { u32 addr; int aid; u16 flags; } group_writer;
#define GROUP(n, fl) { PS2_A(n), PS2_AID_##n, fl }
static const group_writer groups[] = {
    GROUP(RN_GROUP_2D6E00, 0),
    GROUP(RN_GROUP_134598, RN_G_WORLD),
    GROUP(RN_GROUP_134EB0, RN_G_WORLD),
    GROUP(RN_GROUP_141AC8, 0),
    GROUP(RN_GROUP_141EC8, 0),
    GROUP(RN_GROUP_135378, 0),
    GROUP(RN_GROUP_1354D0, 0),
    GROUP(RN_GROUP_135928, 0),
    GROUP(RN_GROUP_135EC0, 0),
    GROUP(RN_GROUP_13E300, 0),
    GROUP(RN_GROUP_136330, 0),
    GROUP(RN_GROUP_136688, 0),
    GROUP(RN_GROUP_136A68, 0),
    GROUP(RN_GROUP_136E88, RN_G_WORLD),
    GROUP(RN_GROUP_137010, RN_G_WORLD),
    GROUP(RN_GROUP_137258, RN_G_WORLD),
    GROUP(RN_GROUP_137C10, RN_G_WORLD),
    GROUP(RN_GROUP_138A58, RN_G_WORLD),
    GROUP(RN_GROUP_13E920, RN_G_WORLD),
    GROUP(RN_GROUP_13FB70, 0),
    GROUP(RN_GROUP_140780, RN_G_WORLD),
    GROUP(RN_GROUP_13F660, 0),
    GROUP(RN_GROUP_13F878, 0),
    GROUP(RN_GROUP_140A20, 0),
    GROUP(RN_GROUP_1410D0, RN_G_WORLD),
    GROUP(RN_GROUP_141478, RN_G_WORLD),
    GROUP(RN_GROUP_149298, RN_G_WORLD),
    GROUP(RN_GROUP_139250, 0),
    GROUP(RN_GROUP_13E6F8, 0),
    GROUP(RN_GROUP_13DBD8, 0),
    GROUP(RN_GROUP_13DE38, 0),
    GROUP(RN_GROUP_142A38, RN_G_WORLD),
};
#define N_GROUPS (sizeof groups / sizeof groups[0])

static int tap_group_enter(ps2_ctx *ctx, void *u) {
    (void)ctx; (void)u;
    group_depth++;
    return 0;
}

static int tap_group(ps2_ctx *ctx, void *u) {
    const group_writer *g = &groups[(uintptr_t)u];
    int ok = 0;
    u32 pkt = ps2_hook_entry_arg(1, &ok);
    u32 end = (u32)ctx->r[2].ud[0];
    rn_intent_hdr h;
    u8 *rec;
    if (group_depth > 0) group_depth--;
    if (group_depth > 0) return 0;
    if (!ok || end <= pkt || end - pkt > (16u << 20)) return 0;
    if (!(rec = pend_add(pkt, end, sizeof h))) return 0;
    hdr_init(&h, RN_INT_GROUP2D, sizeof h, g->addr, pkt);
    h.flags = g->flags;
    memcpy(rec, &h, sizeof h);
    return 0;
}

#define F_SKY_DOME PS2_A(RN_SKY_DOME)
#define F_SKY_HAZE PS2_A(RN_SKY_HAZE)

static void record_sky(u32 dome, u32 pkt, u32 end, u32 site, rn_int_skydome *body) {
    rn_intent_hdr h;
    rn_sky_ring ring;
    const u32 rings = 29u;
    u32 need = sizeof h + sizeof *body + rings * sizeof ring;
    u8 *out;
    if (end <= pkt || end - pkt > (1u << 20)) return;
    if (!(out = pend_add(pkt, end, need))) return;
    hdr_init(&h, RN_INT_SKYDOME, need, site, pkt);
    ps2_get_mem(body->screen, dome, sizeof body->screen);
    ps2_get_mem(body->clip, dome + 64u, sizeof body->clip);
    ps2_get_mem(body->seg, PS2_A(RN_SKY_SEGS), sizeof body->seg);
    body->rings = rings;
    body->segs = 32u;
    memcpy(out, &h, sizeof h);
    memcpy(out + sizeof h, body, sizeof *body);
    for (u32 r = 0; r < rings; r++) {
        u32 rad = ps2_r32(0x70003A30u + 8u * r), hgt = ps2_r32(0x70003A34u + 8u * r);
        memcpy(&ring.radius, &rad, 4);
        memcpy(&ring.height, &hgt, 4);
        ring.rgba = ps2_r32(0x70003B30u + 4u * r);
        ring.pad = 0;
        memcpy(out + sizeof h + sizeof *body + r * sizeof ring, &ring, sizeof ring);
    }
}

static int tap_sky_dome(ps2_ctx *ctx, void *u) {
    int ok0 = 0, ok1 = 0;
    u32 dome = ps2_hook_entry_arg(0, &ok0);
    u32 pkt = ps2_hook_entry_arg(1, &ok1);
    rn_int_skydome body;
    (void)u;
    if (!ok0 || !ok1) return 0;
    memset(&body, 0, sizeof body);
    body.pass = RN_SKY_DOME;
    record_sky(dome, pkt, (u32)ctx->r[2].ud[0], F_SKY_DOME, &body);
    return 0;
}

static float haze_lo, haze_hi;

static int tap_sky_haze_enter(ps2_ctx *ctx, void *u) {
    (void)u;
    haze_lo = ctx->f[12].f;
    haze_hi = ctx->f[13].f;
    return 0;
}

static int tap_sky_haze(ps2_ctx *ctx, void *u) {
    int ok0 = 0, ok1 = 0, ok2 = 0;
    u32 dome = ps2_hook_entry_arg(0, &ok0);
    u32 pkt = ps2_hook_entry_arg(1, &ok1);
    u32 cam = ps2_hook_entry_arg(2, &ok2);
    float rows[16], base, span, step;
    rn_int_skydome body;
    (void)u;
    if (!ok0 || !ok1 || !ok2) return 0;
    if (haze_hi < 4096.0f) {
        base = 0.0f < haze_lo ? haze_lo : 0.0f;
    } else {
        base = haze_lo;
        if (4096.0f < haze_hi - haze_lo) base = haze_hi - 4096.0f;
    }
    span = haze_hi - base;
    step = span * 0.125f;
    ps2_get_mem(rows, cam, sizeof rows);
    memset(&body, 0, sizeof body);
    body.pass = RN_SKY_HAZE;
    body.ring0 = 14u;
    body.ring1 = 20u;
    body.layers = 8u;
    for (u32 l = 0; l < 8u; l++) {
        float h = -(base + step * (float)(s32)l);
        float z = rows[10] * h + rows[14], w = rows[11] * h + rows[15];
        s32 zi = ps2_cvt_w_s(z / w);
        u32 packed = (u32)ps2_cvt_w_s((float)zi * 16.0f) >> 4;
        body.z[l] = packed & 0x00FFFFFFu;
    }
    record_sky(dome, pkt, (u32)ctx->r[2].ud[0], F_SKY_HAZE, &body);
    return 0;
}

#define F_CLIP_TRI PS2_A(RN_CLIP_TRI)
#define F_DRAW_FAN PS2_A(RN_DRAW_FAN)
static rn_int_tri3d tri_in;
static u32 tri_obj;
static int tri_ok;

static int tap_clip_tri(ps2_ctx *ctx, void *u) {
    u32 obj = (u32)ctx->r[4].ud[0];
    (void)u;
    tri_obj = obj;
    ps2_get_mem(tri_in.screen, obj, sizeof tri_in.screen);
    ps2_get_mem(tri_in.clip, obj + 64u, sizeof tri_in.clip);
    tri_in.prim = ps2_r32(obj + 128u);
    for (u32 i = 0; i < 3u; i++) {
        u32 base = PS2_A(RN_SKY_RINGTAB) + 64u * i;
        ps2_get_mem(tri_in.colour + 4u * i, base, 16);
        ps2_get_mem(tri_in.uv + 4u * i, base + 16u, 16);
        ps2_get_mem(tri_in.pos + 4u * i, base + 32u, 16);
    }
    tri_ok = 1;
    return 0;
}

static int tap_draw_fan(ps2_ctx *ctx, void *u) {
    int ok0 = 0, ok1 = 0;
    u32 obj = ps2_hook_entry_arg(0, &ok0);
    u32 pkt = ps2_hook_entry_arg(1, &ok1);
    u32 end = (u32)ctx->r[2].ud[0];
    rn_intent_hdr h;
    u32 need = sizeof h + sizeof tri_in;
    u8 *rec;
    (void)u;
    if (!ok0 || !ok1 || !tri_ok || obj != tri_obj || end <= pkt || end - pkt > 65536u)
        return 0;
    tri_ok = 0;
    if (!(rec = pend_add(pkt, end, need))) return 0;
    hdr_init(&h, RN_INT_TRI3D, need, F_DRAW_FAN, pkt);
    memcpy(rec, &h, sizeof h);
    memcpy(rec + sizeof h, &tri_in, sizeof tri_in);
    return 0;
}

#define F_CLOUD_PROJECT PS2_A(RN_CLOUD_PROJECT)
#define F_SPRITE_ROWS   PS2_A(RN_SPRITE_ROWS)
#define F_CLOUD_FIELD   PS2_A(RN_CLOUD_FIELD)
#define CLOUD_FIELD_SIZE 0xA58u

#define CLOUD_MAX 512u
static struct {
    u32 xy0;
    u64 xy1z;
    float pos[4];
    float size;
} clouds[CLOUD_MAX];
static u32 cloud_rec;
static float cloud_size;

static int tap_cloud_project_enter(ps2_ctx *ctx, void *u) {
    (void)u;
    cloud_rec = (u32)ctx->r[5].ud[0];
    cloud_size = ctx->f[12].f;
    return 0;
}

static int tap_cloud_project(ps2_ctx *ctx, void *u) {
    (void)u;
    if (ctx->f[0].f < 0.0f || n_clouds >= CLOUD_MAX) return 0;
    clouds[n_clouds].xy0 = ps2_r32(cloud_rec + 0x10u);
    clouds[n_clouds].xy1z = ps2_r64(cloud_rec + 0x18u);
    ps2_get_mem(clouds[n_clouds].pos, 0x70000000u, 16);
    clouds[n_clouds].pos[3] = 1.0f;
    clouds[n_clouds].size = cloud_size;
    n_clouds++;
    return 0;
}

static struct { u32 ra, pkt, xy0; u64 xy1z; } sprite_in;

static int tap_sprite_rows_enter(ps2_ctx *ctx, void *u) {
    (void)u;
    sprite_in.ra = (u32)ctx->r[31].ud[0];
    sprite_in.pkt = (u32)ctx->r[4].ud[0];
    sprite_in.xy0 = (u32)ctx->r[5].ud[0];
    sprite_in.xy1z = ctx->r[6].ud[0];
    return 0;
}

static int tap_sprite_rows(ps2_ctx *ctx, void *u) {
    u32 end = (u32)ctx->r[2].ud[0], start;
    rn_intent_hdr h;
    rn_int_billboard b;
    u32 need = sizeof h + sizeof b;
    u8 *rec;
    (void)u;
    if (sprite_in.ra - F_CLOUD_FIELD >= CLOUD_FIELD_SIZE) return 0;
    start = sprite_in.pkt - 80u;
    if (end <= start || end - start > 65536u) return 0;
    for (u32 i = 0; i < n_clouds; i++) {
        if (clouds[i].xy0 != sprite_in.xy0 || clouds[i].xy1z != sprite_in.xy1z) continue;
        if (!(rec = pend_add(start, end, need))) return 0;
        hdr_init(&h, RN_INT_BILLBOARD, need, F_CLOUD_FIELD, start);
        memset(&b, 0, sizeof b);
        ps2_get_mem(b.rows, 0x70000010u, sizeof b.rows);
        memcpy(b.pos, clouds[i].pos, sizeof b.pos);
        b.size = clouds[i].size;
        ps2_get_mem(b.scale, 0x700000B0u, sizeof b.scale);
        b.zmin = 16u;
        b.uv[0] = 8u; b.uv[1] = 8u; b.uv[2] = 0x7F8u; b.uv[3] = 0x7F8u;
        b.rgba = ps2_r32(sprite_in.pkt - 8u);
        memcpy(rec, &h, sizeof h);
        memcpy(rec + sizeof h, &b, sizeof b);
        return 0;
    }
    return 0;
}

#define F_CLOUD_PLANES  PS2_A(RN_CLOUD_PLANES)
#define F_CLOUD_VERTEX  PS2_A(RN_CLOUD_VERTEX)

#define PLOG_MAX 4096u
static struct { u64 xyz; float pos[4]; } plog[PLOG_MAX];
static u32 plog_n;
#define PHASH 8192u
static struct { u32 gen, idx; } phash[PHASH];
static u32 phash_gen;
static struct { u32 pkt, rows; } planes_in;
static u64 st_planes_ok, st_planes_miss, st_planes_quads;

static u32 phash_slot(u64 xyz) { return (u32)((xyz * 0x9E3779B97F4A7C15ull) >> 51); }
static struct { u32 out; float pos[4]; } pvtx_in;

static int tap_cloud_planes_enter(ps2_ctx *ctx, void *u) {
    (void)u;
    planes_in.pkt = (u32)ctx->r[5].ud[0];
    planes_in.rows = (u32)ctx->r[6].ud[0];
    plog_n = 0;
    phash_gen++;
    return 0;
}

static int tap_cloud_vertex_enter(ps2_ctx *ctx, void *u) {
    (void)u;
    pvtx_in.out = (u32)ctx->r[4].ud[0];
    ps2_get_mem(pvtx_in.pos, (u32)ctx->r[7].ud[0], 16);
    return 0;
}

static int tap_cloud_vertex(ps2_ctx *ctx, void *u) {
    (void)u;
    if ((u32)ctx->r[2].ud[0] == 0xFFFFFFFFu || plog_n >= PLOG_MAX) return 0;
    plog[plog_n].xyz = ps2_r64(pvtx_in.out);
    memcpy(plog[plog_n].pos, pvtx_in.pos, 16);
    for (u32 n = 0, s = phash_slot(plog[plog_n].xyz); n < PHASH; n++, s = (s + 1u) & (PHASH - 1u))
        if (phash[s].gen != phash_gen) {
            phash[s].gen = phash_gen;
            phash[s].idx = plog_n;
            break;
        }
    plog_n++;
    return 0;
}

static int tap_cloud_planes(ps2_ctx *ctx, void *u) {
    static rn_world_vtx vtx[PLOG_MAX];
    u32 end = (u32)ctx->r[2].ud[0], start = planes_in.pkt - 64u, q, nv = 0, z = 0;
    rn_intent_hdr h;
    rn_int_worldtri body;
    u32 need;
    u8 *rec;
    (void)u;
    if (end <= planes_in.pkt + 96u || end - start > (1u << 20)) return 0;
    for (q = planes_in.pkt + 96u; q + 128u <= end && nv + 6u <= PLOG_MAX; q += 128u) {
        rn_world_vtx c[4];
        u32 type;
        if (ps2_r64(q) != 0xE400000000008001ull) break;
        type = ps2_r32(q + 16u) & 7u;
        if (type != 4u && type != 5u) break;
        for (u32 k = 0; k < 4u; k++) {
            u32 v = q + 32u + 24u * k;
            u64 st = ps2_r64(v), rgbaq = ps2_r64(v + 8u), xyz = ps2_r64(v + 16u);
            float s, t, qq;
            u32 i;
            memcpy(&s, (u8 *)&st, 4);
            memcpy(&t, (u8 *)&st + 4, 4);
            memcpy(&qq, (u8 *)&rgbaq + 4, 4);
            i = plog_n;
            for (u32 n = 0, sl = phash_slot(xyz); n < PHASH && phash[sl].gen == phash_gen;
                 n++, sl = (sl + 1u) & (PHASH - 1u))
                if (plog[phash[sl].idx].xyz == xyz) { i = phash[sl].idx; break; }
            if (i == plog_n || qq == 0.0f) {
                static int shown;
                if (shown++ < 8)
                    ps2_log("rn: cloud plane corner not in the projection log: xyz "
                            "%016llX q %g (%u logged)", (unsigned long long)xyz,
                            (double)qq, plog_n);
                st_planes_miss++;
                return 0;
            }
            memcpy(c[k].pos, plog[i].pos, 16);
            c[k].uv[0] = s / qq;
            c[k].uv[1] = t / qq;
            c[k].rgba = (u32)rgbaq;
            c[k].pad = 0;
            z = (u32)(xyz >> 32);
        }
        st_planes_quads++;
        vtx[nv++] = c[0]; vtx[nv++] = c[1]; vtx[nv++] = c[2];
        if (type == 4u) { vtx[nv++] = c[1]; vtx[nv++] = c[2]; vtx[nv++] = c[3]; }
        else            { vtx[nv++] = c[0]; vtx[nv++] = c[2]; vtx[nv++] = c[3]; }
    }
    if (q < end) {
        static int shown;
        if (shown++ < 8)
            ps2_log("rn: cloud plane packet not parsed to its end: %08X of %08X..%08X",
                    q, start, end);
        st_planes_miss++;
        return 0;
    }
    if (!nv) return 0;
    st_planes_ok++;
    need = sizeof h + sizeof body + nv * sizeof vtx[0];
    if (!(rec = pend_add(start, end, need))) return 0;
    hdr_init(&h, RN_INT_WORLDTRI, need, F_CLOUD_PLANES, start);
    memset(&body, 0, sizeof body);
    ps2_get_mem(body.rows, planes_in.rows, sizeof body.rows);
    body.z = z;
    body.n = nv;
    memcpy(rec, &h, sizeof h);
    memcpy(rec + sizeof h, &body, sizeof body);
    memcpy(rec + sizeof h + sizeof body, vtx, nv * sizeof vtx[0]);
    return 0;
}

void rn_intent_init(void) {
    const char *e = getenv("PS2_RN_INTENTS");
    if (e && *e == '0') {
        ps2_log("rn: render intents off (PS2_RN_INTENTS=0)");
        return;
    }
    if (!rn_taps_on) return;
    if (!ps2_addr_code_matches(fn_writer.aid)
        || !ps2_addr_code_matches(fn_rect.aid)
        || !ps2_addr_code_matches(fn_line.aid)) {
        ps2_log("rn: the primitive writer at %08X is not the one the intents "
                "expect; no render intents", F_PRIM_WRITER);
        return;
    }
    arena = (u8 *)malloc(ARENA_BYTES);
    if (!arena) {
        ps2_log("rn: no memory for the intent arena; no render intents");
        return;
    }
    if (ps2_hook_before(F_PRIM_WRITER, tap_writer_enter, NULL, 100, "rn-intents") < 0
        || ps2_hook_after(F_PRIM_WRITER, tap_writer, NULL, 100, "rn-intents") < 0
        || ps2_hook_before(F_RECT_HELPER, tap_helper, (void *)(uintptr_t)0, 100,
                           "rn-intents") < 0
        || ps2_hook_before(F_LINE_HELPER, tap_helper, (void *)(uintptr_t)1, 100,
                           "rn-intents") < 0
        ) {
        ps2_log("rn: the hook layer refused an intent tap; render intents are off");
        return;
    }
    for (uintptr_t i = 0; i < N_GROUPS; i++) {
        if (!ps2_addr_code_matches(groups[i].aid)) {
            ps2_log("rn: 2D group writer %08X does not match; not claimed",
                    groups[i].addr);
            continue;
        }
        if (ps2_hook_before(groups[i].addr, tap_group_enter, (void *)i, 100,
                            "rn-intents") < 0
            || ps2_hook_after(groups[i].addr, tap_group, (void *)i, 100,
                              "rn-intents") < 0)
            ps2_log("rn: the hook layer refused 2D group writer %08X", groups[i].addr);
    }
    if (ps2_addr_code_matches(PS2_AID_RN_SKY_DOME) && ps2_addr_code_matches(PS2_AID_RN_SKY_HAZE)) {
        if (ps2_hook_after(F_SKY_DOME, tap_sky_dome, NULL, 100, "rn-intents") < 0
            || ps2_hook_before(F_SKY_HAZE, tap_sky_haze_enter, NULL, 100, "rn-intents") < 0
            || ps2_hook_after(F_SKY_HAZE, tap_sky_haze, NULL, 100, "rn-intents") < 0)
            ps2_log("rn: the hook layer refused a sky tap");
    } else {
        ps2_log("rn: the sky writers %08X / %08X do not match; not recorded",
                F_SKY_DOME, F_SKY_HAZE);
    }
    if (ps2_addr_code_matches(PS2_AID_RN_CLIP_TRI) && ps2_addr_code_matches(PS2_AID_RN_DRAW_FAN)) {
        if (ps2_hook_before(F_CLIP_TRI, tap_clip_tri, NULL, 100, "rn-intents") < 0
            || ps2_hook_after(F_DRAW_FAN, tap_draw_fan, NULL, 100, "rn-intents") < 0)
            ps2_log("rn: the hook layer refused the clip-and-draw taps");
    } else {
        ps2_log("rn: the clip-and-draw helpers %08X / %08X do not match; not recorded",
                F_CLIP_TRI, F_DRAW_FAN);
    }
    if (ps2_addr_code_matches(PS2_AID_RN_CLOUD_PROJECT)
        && ps2_addr_code_matches(PS2_AID_RN_SPRITE_ROWS)) {
        if (ps2_hook_before(F_CLOUD_PROJECT, tap_cloud_project_enter, NULL, 100, "rn-intents") < 0
            || ps2_hook_after(F_CLOUD_PROJECT, tap_cloud_project, NULL, 100, "rn-intents") < 0
            || ps2_hook_before(F_SPRITE_ROWS, tap_sprite_rows_enter, NULL, 100, "rn-intents") < 0
            || ps2_hook_after(F_SPRITE_ROWS, tap_sprite_rows, NULL, 100, "rn-intents") < 0)
            ps2_log("rn: the hook layer refused the cloud taps");
    } else {
        ps2_log("rn: the cloud writers %08X / %08X do not match; not recorded",
                F_CLOUD_PROJECT, F_SPRITE_ROWS);
    }
    if (ps2_addr_code_matches(PS2_AID_RN_CLOUD_PLANES)
        && ps2_addr_code_matches(PS2_AID_RN_CLOUD_VERTEX)) {
        if (ps2_hook_before(F_CLOUD_PLANES, tap_cloud_planes_enter, NULL, 100, "rn-intents") < 0
            || ps2_hook_after(F_CLOUD_PLANES, tap_cloud_planes, NULL, 100, "rn-intents") < 0
            || ps2_hook_before(F_CLOUD_VERTEX, tap_cloud_vertex_enter, NULL, 100, "rn-intents") < 0
            || ps2_hook_after(F_CLOUD_VERTEX, tap_cloud_vertex, NULL, 100, "rn-intents") < 0)
            ps2_log("rn: the hook layer refused the cloud plane taps");
    } else {
        ps2_log("rn: the cloud plane writers %08X / %08X do not match; not recorded",
                F_CLOUD_PLANES, F_CLOUD_VERTEX);
    }
    ps2_log("rn: render intents on (primitive writer %08X)", F_PRIM_WRITER);
}

void rn_intent_report(void) {
    if (!st_int.frames) return;
    ps2_log("rn: intents -- %llu recorded (%.1f per frame, %.1f KB per frame), "
            "%llu handed over with their packets, %llu never transferred, "
            "%llu refused for space; %llu claims ended by the walk leaving their "
            "bytes, %llu frames ended inside a 2D group writer",
            (unsigned long long)st_int.recorded,
            (double)st_int.recorded / (double)st_int.frames,
            (double)st_int.bytes / 1024.0 / (double)st_int.frames,
            (unsigned long long)st_int.emitted, (unsigned long long)st_int.dropped,
            (unsigned long long)st_int.overflow, (unsigned long long)st_int.walked_out,
            (unsigned long long)st_int.depth_resets);
    if (st_planes_ok || st_planes_miss)
        ps2_log("rn: cloud planes -- %llu packets recorded (%llu quads), %llu left "
                "emulated because a corner or the packet could not be accounted for",
                (unsigned long long)st_planes_ok, (unsigned long long)st_planes_quads,
                (unsigned long long)st_planes_miss);
}
