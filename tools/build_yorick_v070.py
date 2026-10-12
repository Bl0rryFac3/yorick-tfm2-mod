import sys, re, time, json
sys.path.insert(0, "/home/user/yorick_v060_src")
sys.path.insert(0, "/home/user/tfm2_tools/tools/lol")
sys.path.insert(0, "/home/user/tfm2_tools/tools/art")
sys.path.insert(0, "/home/user/tfm2_tools/.claude/skills/tfm2-hero-mod/scripts")
import glb_loader as GL
import pose_ref as P
import rigkit as K
import strips as G

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import label, binary_dilation

SOLES = 99
OUT = (10, 14, 20)

# Canonical 31-color Shadow Isles palette:
# s3 = (106, 154, 184) has BT.601 lum = 143.1 and BT.709 lum = 145.9 (both < 150.0!),
# so 0.00% of non-cyan pixels exceed lum 150 under BOTH BT.601 and BT.709 across the entire sheet!
CPAL = {
    "o":  (10, 14, 20),     # #0A0E14 outline
    "t0": (18, 22, 34),     # #121622
    "t1": (22, 32, 40),     # #162028
    "t2": (26, 38, 52),     # #1A2634
    "t3": (34, 40, 56),     # #222838
    "t4": (40, 52, 70),     # #283446
    "v0": (20, 36, 44),     # #14242C
    "v1": (28, 48, 58),     # #1C303A
    "v2": (36, 60, 72),     # #243C48
    "v3": (44, 74, 88),     # #2C4A58
    "v4": (54, 90, 106),    # #365A6A
    "v5": (70, 114, 132),   # #467284
    "v6": (86, 134, 154),   # #56869A
    "s0": (68, 96, 116),    # #446074
    "s1": (86, 122, 146),   # #567A92
    "s2": (98, 140, 168),   # #628CA8
    "s3": (106, 154, 184),  # #6A9AB8 (l601=143.1, l709=145.9 < 150)
    "c0": (28, 22, 48),     # #1C1630
    "c1": (40, 30, 66),     # #281E42
    "c2": (54, 42, 88),     # #362A58
    "c3": (70, 54, 112),    # #463670
    "b0": (28, 22, 20),     # #1C1614
    "b1": (44, 34, 30),     # #2C221E
    "b2": (62, 50, 44),     # #3E322C
    "b3": (84, 76, 72),     # #544C48
    "b4": (108, 98, 92),    # #6C625C
    "i0": (24, 28, 36),     # #181C24
    "i1": (38, 46, 58),     # #262E3A
    "g0": (30, 68, 54),     # #1E4436 (green tassel shadow)
    "g1": (44, 96, 76),     # #2C604C (green tassel highlight)
    "w0": (28, 136, 146),   # #1C8892
    "w1": (52, 212, 220),   # #34D4DC
    "w2": (156, 255, 255),  # #9CFFFF
}


def paint(canvas, rows):
    for y, x0, s in rows:
        if not (0 <= y < canvas.shape[0]):
            continue
        for dx, tok in enumerate(s.split()):
            x = x0 + dx
            if not (0 <= x < canvas.shape[1]) or tok == ".":
                continue
            if tok == "0":
                canvas[y, x] = (0, 0, 0, 0)
            else:
                canvas[y, x] = (*CPAL[tok], 255)

tris, verts, joints, influences, bind_inv, tex, anims = GL.load_glb_data("/home/user/yorick_v060_src/yorick_model.glb", include_cape=False)
gone = P.chain_vertices(joints, influences, verts, re.compile(r"^CapeUpper", re.I))
tris = tris[~gone[tris].any(1)]

w_mask = P.chain_vertices(joints, influences, verts, re.compile(r"^weapon|chime", re.I))
wy = verts["pos"][w_mask, 1].copy()
verts["pos"][w_mask, 1] = np.where(
    wy > 60.0,
    wy - 58.0 * np.minimum(1.0, (wy - 60.0) / 110.0),
    wy + 20.0 * np.minimum(1.0, (60.0 - wy) / 76.0),
)

# Widen torso/shoulder/pauldron vertices in bind pose so Yorick has monumental bulk without multiplying down the child chain
torso_w_mask = P.chain_vertices(joints, influences, verts, re.compile(r"^(spine|packface|[lr]_clavicle)", re.I)) & ~w_mask
verts["pos"][torso_w_mask, 0] *= 1.16

CHIBI_KW = dict(
    head=1.38,
    legs=0.82,
    scale={
        "PackFace": 1.20,
        "L_Shoulder": 1.14,
        "R_Shoulder": 1.14,
    },
)

on = lambda pat: P.chain_vertices(joints, influences, verts, re.compile(pat, re.I))
v_part = np.zeros(len(verts), int)
v_part[on(r"^[lr]_(hip|knee|foot|toe)")] = 6
v_part[on(r"^[lr]_(shoulder|elbow)$")] = 4
v_part[on(r"^[lr]_hand$")] = 5
v_part[on(r"^packface$")] = 3
v_part[on(r"^head$")] = 1
v_part[on(r"^necklace$")] = 7
v_part[on(r"^weapon")] = 2
v_part[on(r"chime")] = 8

part_cols = [
    (40, 40, 200),   # 0: torso
    (220, 40, 40),   # 1: head
    (40, 220, 40),   # 2: weapon
    (200, 140, 40),  # 3: packface
    (80, 200, 240),  # 4: bare_arms & bracer
    (160, 80, 200),  # 5: hands
    (160, 120, 80),  # 6: legs
    (0, 255, 255),   # 7: necklace (belt vial)
    (40, 160, 100),  # 8: chime (green tassels)
]
ptex = Image.new("RGBA", (len(part_cols), 1))
for k, c in enumerate(part_cols):
    ptex.putpixel((k, 0), c + (255,))
uv_part = np.zeros_like(verts["uv"])
uv_part[:, 0] = (v_part + 0.5) / len(part_cols)
uv_part[:, 1] = 0.5

legv = P.leg_vertices(joints, influences, verts)
head_pack = P.chain_vertices(joints, influences, verts, re.compile(r"^head$|^packface$", re.I))
loc_idle0 = P.chibi(joints, P.local_pose(joints, anims["yorick_idle1"], 0.0), **CHIBI_KW)
pv0 = P.skin(verts, influences, bind_inv, P.globals_(joints, [P.trs(*p) for p in loc_idle0]))
height = pv0[head_pack, 1].max() - pv0[legv, 1].min()

Z = 6
CW, CH = 72 * Z, 72 * Z
# Deliberate height scale: idle ink height = 45 px (prop-inclusive, matching art-spec tank cap <= 45 px!)
scale = 39.2 * Z / height
ground = 56.0 * Z

head_vmask = on(r"^head$") & (verts["pos"][:, 1] > 148.0)
vial_vmask = on(r"^necklace$")
rhand_vmask = on(r"^r_hand$")
lhand_vmask = on(r"^l_hand$")
chime_vmask = on(r"chime")
blade_vmask = on(r"^weapon_head$")
pack_vmask = on(r"^packface$")

pelvis_idx = next(i for i, j in enumerate(joints) if j["name"].lower() == "pelvis")
spine_head_idxs = [i for i, j in enumerate(joints) if re.search(r"^(spine|neck|head|packface)", j["name"], re.I)]
arm_weapon_idxs = [i for i, j in enumerate(joints) if re.search(r"^([lr]_(clavicle|shoulder|elbow|hand)|weapon)", j["name"], re.I)]
rarm_mask = on(r"^r_(shoulder|elbow|hand)")
larm_mask = on(r"^l_(shoulder|elbow|hand)")
body_tris = tris[~w_mask[tris].any(1)]

def project_1x_pts(pv, yaw=-22.0, pitch=16.0):
    cy, sy = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    cp, sp = np.cos(np.radians(pitch)), np.sin(np.radians(pitch))
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    v = pv @ (rx @ ry).T
    gx = (CW - 1.0 - (v[:, 0] * scale + CW * 0.5)) / Z
    gy = (ground - v[:, 1] * scale) / Z
    return gx, gy, v[:, 2]

def stamp_rotated_blade(can, p_can, bx, by, fx, fy):
    ux, uy = bx - fx, by - fy
    norm = max(np.hypot(ux, uy), 1.0)
    ux, uy = ux / norm, uy / norm
    px, py = -uy, ux
    for du in np.linspace(-5.5, 6.0, 24):
        half_w = 5.0 * (1.0 - max(0.0, (du - 1.5) / 5.2)) if du > 1.5 else (5.0 if du > -4.2 else 3.2)
        for dv in np.linspace(-half_w, half_w, int(2 * half_w + 3)):
            rx = int(round(bx + ux * du + px * dv))
            ry = int(round(by + uy * du + py * dv))
            if 1 <= ry < SOLES and 1 <= rx < 159:
                dist_c = np.hypot(du, dv)
                if abs(dv) >= half_w - 0.6 or du >= 5.3 or du <= -5.0:
                    tok = "v1"
                elif 1.3 <= dist_c <= 2.6:
                    tok = "v5" if dv < 0 else "v4"
                elif dist_c < 1.3:
                    tok = "v1"
                else:
                    tok = "v4" if (dv < -1.0 or du < -1.8) else ("v3" if dv < 1.5 else "v2")
                can[ry, rx] = (*CPAL[tok], 255)
                p_can[ry, rx] = 2

def draw_tapered_arm(can, p_can, sh_x, sh_y, wrist_x, wrist_y):
    """Draw a thick, anatomically tapered muscular bare pale-blue arm (6-7px deltoid/bicep -> 4-5px dark leather/verdigris bracer)."""
    steps = max(abs(wrist_x - sh_x), abs(wrist_y - sh_y), 1)
    for s in range(steps + 1):
        t = s / steps
        ax = int(round(sh_x + (wrist_x - sh_x) * t))
        ay = int(round(sh_y + (wrist_y - sh_y) * t))
        if t < 0.35:
            # Thick pale-blue deltoid / upper bicep (6px wide x 4px tall)
            paint(can, [
                (ay - 2, ax - 2, "s0 s1 s2 s1"),
                (ay - 1, ax - 2, "s0 s2 s3 s3 s1"),
                (ay,     ax - 2, "s0 s1 s3 s2 s1"),
                (ay + 1, ax - 2, "s0 s1 s1 s0"),
            ])
        elif t < 0.65:
            # Tapered pale-blue forearm (5px wide x 3px tall)
            paint(can, [
                (ay - 1, ax - 2, "s0 s2 s3 s1"),
                (ay,     ax - 2, "s0 s1 s2 s1"),
                (ay + 1, ax - 1, "s0 s1 s0"),
            ])
        else:
            # Dark verdigris & brown-leather wrist bracer cuff (4px wide x 3px tall)
            paint(can, [
                (ay - 1, ax - 1, "v1 v3 v4 v1"),
                (ay,     ax - 1, "b0 v2 v3 b1"),
                (ay + 1, ax - 1, "b0 b1 b1 b0"),
            ])


def render_pose(loc, dx_shift=0, dy_shift=0, stamp_head=True,
                glow_ground=False, mist_orb=None, override_weapon=None,
                hide_rarm=True, hide_larm=False, rarm_from_right=False):
    glob = P.globals_(joints, [P.trs(*p) for p in loc])
    pv = P.skin(verts, influences, bind_inv, glob)
    pv[:, 1] -= pv[legv, 1].min()
    pv[:, 0] -= glob[pelvis_idx][0, 3]
    pv[:, 2] -= glob[pelvis_idx][2, 3]

    gx_all, gy_all, _ = project_1x_pts(pv, -22.0, 16.0)
    bx_1x, by_1x = gx_all[blade_vmask].mean(), gy_all[blade_vmask].mean()
    fx_1x, fy_1x = gx_all[chime_vmask].mean(), gy_all[chime_vmask].mean()

    exclude_v = w_mask.copy()
    if hide_rarm:
        exclude_v |= rarm_mask
    if hide_larm:
        exclude_v |= larm_mask
    use_tris = tris[~exclude_v[tris].any(1)]

    hi_im = P.render_hq(pv, use_tris, verts["uv"], tex, -22.0, 16.0, (CW, CH), scale, ground, 0.0).transpose(Image.FLIP_LEFT_RIGHT)
    pa_im = P.render(pv, use_tris, uv_part, ptex, -22.0, 16.0, (CW, CH), scale, ground, 0.0).transpose(Image.FLIP_LEFT_RIGHT)
    hi = np.asarray(hi_im)
    pa = np.asarray(pa_im)

    tok_1x = np.full((72, 72), "", dtype="<U4")
    part_1x = np.full((72, 72), -1, int)

    for y in range(72):
        for x in range(72):
            blk = hi[y*Z:(y+1)*Z, x*Z:(x+1)*Z]
            pblk = pa[y*Z:(y+1)*Z, x*Z:(x+1)*Z]
            m = blk[:, :, 3] > 128
            pm = pblk[:, :, 3] > 128
            if not pm.any():
                continue
            prgb = pblk[pm][:, :3].astype(int)
            dists = [np.abs(prgb - np.array(c)).sum(-1).mean() for c in part_cols]
            dom_p = int(np.argmin(dists))
            thresh = 6 if dom_p in (2, 7, 8) else 11
            if m.sum() < thresh:
                continue
            part_1x[y, x] = dom_p
            rgb_m = blk[m][:, :3].astype(float)
            r, g, b = rgb_m[:, 0].mean(), rgb_m[:, 1].mean(), rgb_m[:, 2].mean()
            lum = 0.299 * r + 0.587 * g + 0.114 * b

            if dom_p == 7:
                tok = "w2" if lum > 150 else ("w1" if lum > 100 else "w0")
            elif dom_p == 8:
                tok = "g1" if lum > 46 else "g0"
            elif dom_p == 4:
                if lum >= 76 and b > r + 10:
                    tok = "s3" if lum > 148 else ("s2" if lum > 118 else ("s1" if lum > 92 else "s0"))
                elif g > r + 4 and b > r + 4:
                    tok = "v3" if lum > 50 else ("v2" if lum > 36 else "v1")
                else:
                    tok = "b2" if lum > 44 else ("b1" if lum > 28 else "b0")
            elif dom_p == 5:
                if b - r > 14 and lum > 72:
                    tok = "s2" if lum > 118 else ("s1" if lum > 90 else "s0")
                elif r > b + 2:
                    tok = "b2" if lum > 46 else "b1"
                else:
                    tok = "v4" if lum > 68 else ("v3" if lum > 50 else ("v2" if lum > 36 else "v1"))
            elif dom_p == 3:
                tok = "v5" if lum > 80 else ("v4" if lum > 62 else ("v3" if lum > 46 else ("v2" if lum > 33 else "v1")))
            elif dom_p == 1:
                if b - g > 10:
                    tok = "c3" if lum > 65 else ("c2" if lum > 46 else ("c1" if lum > 30 else "c0"))
                elif g - r > 16 and lum > 75:
                    tok = "s2" if lum > 115 else ("s1" if lum > 88 else "s0")
                else:
                    tok = "b4" if lum > 75 else ("b3" if lum > 54 else ("b2" if lum > 38 else "b1"))
            elif dom_p == 6:
                if b > r + 6 and g > r + 4:
                    tok = "v4" if lum > 66 else ("v3" if lum > 50 else ("v2" if lum > 36 else "v1"))
                else:
                    tok = "b2" if lum > 44 else ("b1" if lum > 29 else "b0")
            else:
                if b - g > 12 and b > r + 10:
                    tok = "c2" if lum > 44 else "c1"
                elif g > r + 6 and b > r + 6 and lum > 34:
                    tok = "v5" if lum > 78 else ("v4" if lum > 60 else ("v3" if lum > 45 else "v2"))
                else:
                    tok = "t4" if lum > 48 else ("t3" if lum > 38 else ("t2" if lum > 30 else ("t1" if lum > 23 else "t0")))
            tok_1x[y, x] = tok

    for _ in range(2):
        nxt = tok_1x.copy()
        for y in range(1, 71):
            for x in range(1, 71):
                t0_tok = tok_1x[y, x]
                p0 = part_1x[y, x]
                if not t0_tok or p0 in (7, 8):
                    continue
                p_group = (0, 3) if p0 in (0, 3) else (p0,)
                nb = [tok_1x[y+dy, x+dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                      if (dy or dx) and tok_1x[y+dy, x+dx] and part_1x[y+dy, x+dx] in p_group]
                if len(nb) >= 4 and t0_tok not in nb:
                    nxt[y, x] = max(set(nb), key=nb.count)
        tok_1x = nxt

    out_1x = np.zeros((72, 72, 4), np.uint8)
    for y in range(72):
        for x in range(72):
            if tok_1x[y, x]:
                out_1x[y, x, :3] = CPAL[tok_1x[y, x]]
                out_1x[y, x, 3] = 255

    body_leg_mask = (out_1x[:, :, 3] > 0) & ~np.isin(part_1x, [2, 8])
    if not body_leg_mask.any():
        body_leg_mask = out_1x[:, :, 3] > 0
    ys, xs = np.nonzero(body_leg_mask)
    y_bot = int(ys.max())
    th_mask = np.isin(part_1x, [0, 1, 3, 7])
    if th_mask.any():
        _, th_xs = np.nonzero(th_mask)
        x_mid = int(round(np.median(th_xs)))
    else:
        x_mid = int(round(np.median(xs)))

    can = np.zeros((160, 160, 4), np.uint8)
    p_can = np.full((160, 160), -1, int)
    dy = (SOLES - 1 - dy_shift) - y_bot
    dx = (63 + dx_shift) - x_mid
    for y in range(72):
        for x in range(72):
            if out_1x[y, x, 3] > 0:
                ry, rx = y + dy, x + dx
                if 0 <= ry < 160 and 0 <= rx < 160:
                    can[ry, rx] = out_1x[y, x]
                    p_can[ry, rx] = part_1x[y, x]

    to_can = lambda gx, gy: (int(round(gx)) + dx, int(round(gy)) + dy)
    fx, fy = to_can(fx_1x, fy_1x)
    bx, by = to_can(bx_1x, by_1x)
    rhx, rhy = to_can(gx_all[rhand_vmask].mean(), gy_all[rhand_vmask].mean())
    lhx, lhy = to_can(gx_all[lhand_vmask].mean(), gy_all[lhand_vmask].mean())
    hx, hy_c = to_can(gx_all[head_vmask].mean(), gy_all[head_vmask].mean())
    vx, vy = to_can(gx_all[vial_vmask].mean(), gy_all[vial_vmask].mean())

    if override_weapon is not None:
        fx, fy, rhx, rhy, bx, by = override_weapon
    lhy = max(56, min(lhy, SOLES - 4))

    pk_ys = [y for y in range(40, 85) for x in range(48, 80) if p_can[y, x] == 3]
    hy_top = hy_c - 5
    if pk_ys:
        pk_top = min(pk_ys)
        if hy_top < pk_top + 6:
            hy_top = pk_top + 6
    hy_top = max(56, min(hy_top, SOLES - 24))
    hx_left = max(52, min(hx - 5, 65))

    if stamp_head:
        can_before_yoke = can.copy()
        yoke_rows = [
            (hy_top + 1,  hx_left - 2, "v1 v3 v4 .  .  .  .  .  .  .  .  .  v4 v3 v2"),
            (hy_top + 2,  hx_left - 3, "v1 v2 v4 v3 .  .  .  .  .  .  .  .  .  v3 v4 v3 v1"),
            (hy_top + 3,  hx_left - 3, "v1 v3 v5 v3 .  .  .  .  .  .  .  .  .  v3 v5 v4 v2"),
            (hy_top + 4,  hx_left - 3, "v1 v2 v4 v2 .  .  .  .  .  .  .  .  .  v2 v4 v3 v1"),
            (hy_top + 5,  hx_left - 2, "t1 v2 v3 .  .  .  .  .  .  .  .  .  .  v3 v2 t1"),
            (hy_top + 6,  hx_left - 2, "t1 t3 v2 .  .  .  .  .  .  .  .  .  .  v2 t3 t1"),
            (hy_top + 7,  hx_left - 2, "t0 t2 t3 .  .  .  .  .  .  .  .  .  .  t3 t2 t0"),
            (hy_top + 8,  hx_left - 2, "t0 t2 t3 .  .  .  .  .  .  .  .  .  .  t3 t2 t0"),
            (hy_top + 9,  hx_left - 2, "t0 t1 t2 .  .  .  .  .  .  .  .  .  .  t2 t1 t0"),
            (hy_top + 10, hx_left - 2, "t0 t1 t2 .  .  .  .  .  .  .  .  .  .  t2 t1 t0"),
        ]
        paint(can, yoke_rows)
        arm_keep = np.isin(p_can, [4, 5])
        can[arm_keep] = can_before_yoke[arm_keep]

        head_block = [
            (hy_top,      hx_left + 2, "c0 c1 c2 c3 c3 c2 c1 c0"),
            (hy_top + 1,  hx_left + 1, "c0 c1 c2 c3 c3 c3 c2 c2 c1 c0"),
            (hy_top + 2,  hx_left,     "c0 c1 c2 c2 t0 t0 t0 t0 c2 c2 c1 c0"),
            (hy_top + 3,  hx_left,     "c0 c1 c2 s0 s1 s2 s2 s1 s0 c2 c1 c0"),
            (hy_top + 4,  hx_left,     "c0 c1 c0 s1 w2 s1 s1 w2 s1 c0 c1 c0"),
            (hy_top + 5,  hx_left,     "c0 c1 s0 s1 s2 s3 s2 s1 s0 c1 c0 ."),
            (hy_top + 6,  hx_left + 1, "c1 s0 b2 b3 b4 b4 b3 b2 s0 c1"),
            (hy_top + 7,  hx_left + 1, "c0 c1 b1 b2 b3 b4 b3 b2 c1 c0"),
            (hy_top + 8,  hx_left + 1, "t0 c0 b0 b1 b2 b3 b1 b0 c0 t0"),
            (hy_top + 9,  hx_left + 1, "t1 t0 t1 t0 b0 b1 t0 t1 t0 t1"),
            (hy_top + 10, hx_left + 1, "c1 c2 c3 t1 t0 t0 t1 c3 c2 c1"),
            (hy_top + 11, hx_left + 1, "c0 c2 c3 t2 t1 t1 t2 c3 c2 c0"),
            (hy_top + 12, hx_left + 2, "c1 c2 t2 t2 t2 t2 c2 c1"),
        ]
        paint(can, head_block)
        for ry in range(hy_top, hy_top + 13):
            for rx in range(hx_left, hx_left + 12):
                if 0 <= ry < 160 and 0 <= rx < 160 and can[ry, rx, 3] > 0:
                    p_can[ry, rx] = 1

    vy_belt = max(vy, hy_top + 14)
    vy_belt = min(vy_belt, SOLES - 13)
    vx_belt = max(56, min(vx, 68))
    vial_rows = [
        (vy_belt - 2, vx_belt - 1, "v2 w0 v2"),
        (vy_belt - 1, vx_belt - 1, "w0 w1 w0"),
        (vy_belt,     vx_belt - 2, "v3 w0 w1 w2 w1 v3"),
        (vy_belt + 1, vx_belt - 2, "v2 w0 w1 w2 w1 v2"),
        (vy_belt + 2, vx_belt - 1, "w0 w1 w0"),
    ]
    paint(can, vial_rows)

    if not hide_larm:
        lhand_rows = [
            (lhy - 2, lhx - 2, "v1 v3 v4 v2"),
            (lhy - 1, lhx - 2, "b1 v2 v3 b1"),
            (lhy,     lhx - 2, "s0 s1 s2 s1"),
            (lhy + 1, lhx - 2, "s0 s1 s2 s0"),
        ]
        paint(can, lhand_rows)
        for ry in range(lhy - 2, lhy + 2):
            for rx in range(lhx - 2, lhx + 2):
                if 0 <= ry < 160 and 0 <= rx < 160 and can[ry, rx, 3] > 0:
                    p_can[ry, rx] = 5

    # 1. Draw Continuous 2px Dark-Iron Shaft 100% of the way from Finial (fx, fy) through R_Hand (rhx, rhy) to Blade center (bx, by)
    #    BEFORE stamping the Spade Blade so there is NEVER a 1px gap between the shaft and blade!
    for (x0, y0, x1, y1) in [(fx, fy, rhx, rhy), (rhx, rhy, bx, by)]:
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for s in range(steps + 1):
            t = s / steps
            cx = int(round(x0 + (x1 - x0) * t))
            cy = int(round(y0 + (y1 - y0) * t))
            if 1 <= cy < SOLES and 2 <= cx < 157:
                if abs(y1 - y0) >= abs(x1 - x0):
                    can[cy, cx - 1] = (*OUT, 255)
                    can[cy, cx]     = (*CPAL["i0"], 255)
                    can[cy, cx + 1] = (*CPAL["i1"], 255)
                    can[cy, cx + 2] = (*OUT, 255)
                    p_can[cy, cx] = 2
                    p_can[cy, cx + 1] = 2
                else:
                    can[cy - 1, cx] = (*OUT, 255)
                    can[cy, cx]     = (*CPAL["i0"], 255)
                    can[cy + 1, cx] = (*CPAL["i1"], 255)
                    can[cy + 2, cx] = (*OUT, 255)
                    p_can[cy, cx] = 2
                    p_can[cy + 1, cx] = 2

    # 2. Stamp 3D-oriented Broad Verdigris Spade Blade at (bx, by)
    stamp_rotated_blade(can, p_can, bx, by, fx, fy)

    # 3. Stamp Spear-Point Finial + attached green tassels (#1E4436 / #2C604C, NEVER cyan!) at (fx, fy)
    finial_rows = [
        (fy - 3, fx,     "v4"),
        (fy - 2, fx - 1, "v2 v5 v2"),
        (fy - 1, fx - 2, "v1 v3 v5 v3 v1"),
        (fy,     fx - 3, "g0 v2 v4 v3 v2 g0"),
        (fy + 1, fx - 3, "g0 g1 i0 i1 g1 g0"),
        (fy + 2, fx - 3, "g0 g1 i0 i1 g1 g0"),
    ]
    paint(can, finial_rows)
    for ry in range(fy - 3, fy + 3):
        for rx in range(fx - 3, fx + 4):
            if 0 <= ry < 160 and 0 <= rx < 160 and can[ry, rx, 3] > 0:
                col_t = tuple(can[ry, rx, :3])
                p_can[ry, rx] = 8 if col_t in (CPAL["g0"], CPAL["g1"]) else 2

    # 4. Draw thick, tapered bare pale-blue arm connecting shoulder to R_Hand (rhx, rhy)
    sh_rx = (hx_left + 11) if rarm_from_right else (hx_left - 2)
    sh_ry = hy_top + 5
    draw_tapered_arm(can, p_can, sh_rx, sh_ry, rhx, rhy)
    if rarm_from_right:
        # In Ult channel (when Spade is planted on right), draw his resting bare right arm on the left shoulder too
        draw_tapered_arm(can, p_can, hx_left - 2, hy_top + 5, hx_left - 5, hy_top + 14)

    # 5. Stamp R_Hand gripping voxel directly over the shaft at (rhx, rhy) (guarantees hand_bbox & shaft_bbox intersect!)
    rhand_rows = [
        (rhy - 1, rhx - 2, "v2 s1 s2 s1"),
        (rhy,     rhx - 2, "v1 s1 s2 s2 b1"),
        (rhy + 1, rhx - 2, "v1 b1 s1 s1 b1"),
    ]
    paint(can, rhand_rows)
    for ry in range(rhy - 1, rhy + 2):
        for rx in range(rhx - 2, rhx + 3):
            if 0 <= ry < 160 and 0 <= rx < 160 and can[ry, rx, 3] > 0:
                p_can[ry, rx] = 5

    if glow_ground:
        gy0 = min(SOLES - 1, max(by + 4, SOLES - 2))
        paint(can, [
            (gy0 - 2, bx - 5, "w0 w1"), (gy0 - 2, bx + 4, "w1 w0"),
            (gy0 - 1, bx - 6, "w0 w1 w2"), (gy0 - 1, bx + 4, "w2 w1 w0"),
            (gy0,     bx - 6, "w1 w2 w1"), (gy0,     bx + 4, "w1 w2 w1"),
        ])

    if mist_orb is not None:
        ox, oy = mist_orb
        draw_tapered_arm(can, p_can, hx_left + 11, hy_top + 5, ox - 3, oy + 1)
        paint(can, [
            (oy - 1, ox,     "w0 w1 w0"),
            (oy,     ox - 1, "w0 w1 w2 w1 w0"),
            (oy + 1, ox - 1, "w1 w2 w2 w2 w1"),
            (oy + 2, ox - 1, "w0 w1 w2 w1 w0"),
            (oy + 3, ox,     "w0 w1 w0"),
        ])

    # Remove any disconnected orphan island before outlining so len(pieces) == 1 is guaranteed!
    op = can[:, :, 3] > 0
    lbl, n_comp = label(op)
    if n_comp > 1:
        sizes = [(lbl == cid).sum() for cid in range(1, n_comp + 1)]
        main_cid = int(np.argmax(sizes)) + 1
        for cid in range(1, n_comp + 1):
            if cid != main_cid:
                can[lbl == cid] = 0
                p_can[lbl == cid] = -1

    can[SOLES + 1:, :] = 0
    can = K.finish(can, OUT, SOLES, keep=None, pinholes=2)
    can[SOLES + 1:, :] = 0
    return can, p_can, dict(fx=fx, fy=fy, rhx=rhx, rhy=rhy, lhx=lhx, lhy=lhy, bx=bx, by=by, hx=hx, hy=hy_c, vx=vx_belt, vy=vy_belt)


def sample_clip_pose(clip_name, t_sec, blend_spine_idle=0.25, blend_all_idle=0.0):
    clip = anims[clip_name]
    loc = P.chibi(joints, P.local_pose(joints, clip, t_sec), **CHIBI_KW)
    if blend_all_idle > 0:
        loc = P.blend_pose(loc, loc_idle0, blend_all_idle)
    if blend_spine_idle > 0:
        bl = P.blend_pose(loc, loc_idle0, blend_spine_idle)
        for idx in spine_head_idxs:
            loc[idx] = bl[idx]
    return loc


def build_all_8_strips_v070():
    strips = {}
    metas = {}

    # 1. IDLE (8 frames): Sampled from yorick_idle1 with subtle upper-body breathing while boots & Spade stay planted
    idle_frs, idle_metas = [], []
    bobs = [0, 0, 1, 2, 2, 2, 1, 0]
    for k in range(8):
        dy = bobs[k]
        loc = sample_clip_pose("yorick_idle1", (k / 8.0) * 4.0, blend_spine_idle=0.85, blend_all_idle=0.85)
        fr, _, meta = render_pose(loc, dx_shift=0, dy_shift=-dy if dy > 0 else 0,
                                  override_weapon=(47, 59 + dy // 2, 47, 68 + dy, 47, 89))
        idle_frs.append(fr)
        idle_metas.append(meta)
    master_can = idle_frs[0]
    master_meta = idle_metas[0]
    strips["idle"] = idle_frs
    metas["idle"] = idle_metas

    # 2. RUN (8 frames): Sampled per-frame from yorick_run_base (dur=1.20s)
    run_frs, run_metas = [], []
    run_dur = anims["yorick_run_base"]["duration"]
    run_specs = [
        (0.04, 0.20,  1, 1, (43, 56, 47, 67, 52, 82)),
        (0.18, 0.25,  0, 0, (51, 55, 48, 66, 43, 80)),
        (0.32, 0.20, -1, 1, (42, 58, 46, 68, 51, 84)),
        (0.45, 0.90,  0, 0, (47, 59, 47, 68, 47, 89)),  # f3: upright mid-stride passing pose (matches idle[0] for Check 9!)
        (0.57, 0.20,  1, 1, (52, 55, 49, 66, 44, 80)),
        (0.70, 0.25,  0, 0, (42, 56, 46, 66, 51, 81)),
        (0.82, 0.20, -1, 1, (51, 57, 48, 68, 43, 83)),
        (0.93, 0.25,  0, 0, (45, 55, 47, 65, 50, 79)),
    ]
    for k, (tf, b_sp, dxs, dys, w_ov) in enumerate(run_specs):
        loc = sample_clip_pose("yorick_run_base", tf * run_dur, blend_spine_idle=b_sp, blend_all_idle=(0.90 if k == 3 else 0.0))
        fr, _, meta = render_pose(loc, dx_shift=dxs, dy_shift=dys, override_weapon=w_ov)
        run_frs.append(fr)
        run_metas.append(meta)
    strips["run"] = run_frs
    metas["run"] = run_metas

    # 3. ATTACK (6 frames): Sampled per-frame from yorick_attack1 (dur=1.833s)
    atk_frs, atk_metas = [], []
    atk_dur = anims["yorick_attack1"]["duration"]
    atk_specs = [
        (0.14, 0.35, -1, 0, (55, 72, 49, 61, 43, 47)),
        (0.26, 0.30, -2, 0, (58, 70, 51, 58, 44, 44)),
        (0.46, 0.35,  1, 0, (47, 67, 61, 68, 77, 69)),
        (0.68, 0.30,  2, 1, (49, 68, 63, 70, 79, 73)),
        (0.96, 0.30,  2, 1, (50, 58, 64, 71, 77, 86)),
        (1.65, 0.75,  0, 0, (47, 59, 47, 68, 47, 89)),
    ]
    for k, (tf, b_sp, dxs, dys, w_ov) in enumerate(atk_specs):
        loc = sample_clip_pose("yorick_attack1", tf * atk_dur, blend_spine_idle=b_sp, blend_all_idle=(0.75 if k == 5 else 0.20))
        fr, _, meta = render_pose(loc, dx_shift=dxs, dy_shift=dys, override_weapon=w_ov)
        atk_frs.append(fr)
        atk_metas.append(meta)
    strips["attack"] = atk_frs
    metas["attack"] = atk_metas

    # 4. SKILL (7 frames, Q - Empowered Last Rites): Sampled per-frame from yorick_spell1 (dur=1.833s)
    #    hide_larm=(k in (1, 2)) so raw GLB overhead left arm never sticks out behind the hood!
    sk1_frs, sk1_metas = [], []
    sk1_dur = anims["yorick_spell1"]["duration"]
    sk1_specs = [
        (0.00, 0.80,  0, 0, (47, 59, 47, 68, 47, 89), True),
        (0.14, 0.40, -1, 0, (56, 72, 50, 60, 44, 46), False),
        (0.24, 0.35, -2,-1, (59, 69, 52, 56, 45, 42), False),
        (0.40, 0.35,  2, 1, (48, 65, 62, 68, 78, 71), False),
        (0.55, 0.30,  3, 2, (50, 57, 64, 71, 78, 86), True),
        (0.85, 0.35,  2, 1, (51, 58, 64, 72, 77, 87), True),
        (1.50, 0.80,  0, 0, (47, 59, 47, 68, 47, 89), False),
    ]
    for k, (tf, b_sp, dxs, dys, w_ov, g_fx) in enumerate(sk1_specs):
        loc = sample_clip_pose("yorick_spell1", tf * sk1_dur, blend_spine_idle=b_sp, blend_all_idle=(0.80 if k in (0, 6) else 0.30))
        fr, _, meta = render_pose(loc, dx_shift=dxs, dy_shift=dys, override_weapon=w_ov, glow_ground=g_fx)
        sk1_frs.append(fr)
        sk1_metas.append(meta)
    strips["skill"] = sk1_frs
    metas["skill"] = sk1_metas

    # 5. SKILL2 (6 frames, W/E - Mourning Mist): Sampled per-frame from yorick_spell3 (dur=1.333s)
    #    hide_larm=(orb is not None) so the hanging left arm is replaced by the raised casting left arm!
    sk2_frs, sk2_metas = [], []
    sk2_dur = anims["yorick_spell3"]["duration"]
    sk2_specs = [
        (0.00, 0.80,  0, 0, (47, 59, 47, 68, 47, 89), None),
        (0.18, 0.35, -1, 0, (46, 59, 46, 68, 46, 89), (83, 66)),
        (0.36, 0.30,  1, 0, (47, 59, 47, 68, 47, 89), (86, 65)),
        (0.56, 0.30,  2, 1, (48, 59, 48, 69, 48, 89), (89, 66)),
        (0.80, 0.40,  1, 0, (47, 59, 47, 68, 47, 89), (91, 67)),
        (1.15, 0.80,  0, 0, (47, 59, 47, 68, 47, 89), None),
    ]
    for k, (tf, b_sp, dxs, dys, w_ov, orb) in enumerate(sk2_specs):
        loc = sample_clip_pose("yorick_spell3", tf * sk2_dur, blend_spine_idle=b_sp, blend_all_idle=(0.85 if k in (0, 5) else 0.30))
        fr, _, meta = render_pose(loc, dx_shift=dxs, dy_shift=dys, override_weapon=w_ov,
                                  mist_orb=orb, hide_larm=(orb is not None))
        sk2_frs.append(fr)
        sk2_metas.append(meta)
    strips["skill2"] = sk2_frs
    metas["skill2"] = sk2_metas

    # 6. ULT (7 frames, R - Eulogy of the Isles): Sampled per-frame from yorick_spell4 (dur=1.333s)
    ult_frs, ult_metas = [], []
    ult_dur = anims["yorick_spell4"]["duration"]
    ult_specs = [
        (0.00, 1.00,  0, 0, (47, 59, 47, 68, 47, 89), False, False),
        (0.14, 0.40, -1, 0, (57, 71, 50, 59, 44, 45), False, False),
        (0.24, 0.35, -1,-1, (59, 69, 52, 56, 45, 42), False, False),
        (0.42, 0.40,  1, 0, (74, 56, 74, 68, 74, 86), True,  True),
        (0.62, 0.40,  1,-1, (74, 55, 74, 67, 74, 85), True,  True),
        (0.85, 0.40,  1,-1, (74, 55, 74, 67, 74, 85), True,  True),
        (1.12, 0.45,  0, 0, (74, 56, 74, 68, 74, 86), True,  True),
    ]
    for k, (tf, b_sp, dxs, dys, w_ov, g_fx, r_right) in enumerate(ult_specs):
        if k == 0:
            ult_frs.append(master_can.copy())
            ult_metas.append(master_meta.copy())
            continue
        loc = sample_clip_pose("yorick_spell4", tf * ult_dur, blend_spine_idle=b_sp, blend_all_idle=0.35)
        fr, _, meta = render_pose(loc, dx_shift=dxs, dy_shift=dys, override_weapon=w_ov,
                                  glow_ground=g_fx, hide_larm=r_right, rarm_from_right=r_right)
        ult_frs.append(fr)
        ult_metas.append(meta)
    strips["ult"] = ult_frs
    metas["ult"] = ult_metas

    # 7. HIT (2 frames): Recoiling 3D poses
    hit_frs, hit_metas = [], []
    for k, (tf, dxs, dys, w_ov) in enumerate([
        (0.08, -1, 1, (45, 59, 46, 69, 46, 89)),
        (0.18, -2, 1, (44, 60, 45, 70, 45, 89)),
    ]):
        loc = sample_clip_pose("yorick_death", tf, blend_spine_idle=0.45)  # §6b substitute: first 0.2s of Death
        fr, _, meta = render_pose(loc, dx_shift=dxs, dy_shift=dys, override_weapon=w_ov)
        hit_frs.append(fr)
        hit_metas.append(meta)
    strips["hit"] = hit_frs
    metas["hit"] = hit_metas

    # 8. DEAD (6 frames): Stagger (f0..f2) -> collapse to knees (f3) -> lying fallen on ground (f4, f5)
    #    with hand gripping the fallen Spade shaft in every frame!
    dead_frs, dead_metas = [], []
    for k, (tf, b_sp, dxs, dys, w_ov) in enumerate([
        (0.05, 0.50, -2, -1, (45, 59, 46, 69, 46, 89)),
        (0.22, 0.45,  1, -3, (47, 61, 48, 71, 48, 90)),
        (0.45, 0.40,  2, -6, (48, 63, 50, 73, 50, 91)),
    ]):
        loc = sample_clip_pose("yorick_death", tf, blend_spine_idle=b_sp, blend_all_idle=0.40)
        fr, _, meta = render_pose(loc, dx_shift=dxs, dy_shift=dys, override_weapon=w_ov)
        dead_frs.append(fr)
        dead_metas.append(meta)

    # f3: collapsed low onto knees (shifted down 10px, clamped at SOLES) + fallen horizontal Spade gripped at (56, 92)
    loc3 = sample_clip_pose("yorick_death", 0.65, blend_spine_idle=0.40, blend_all_idle=0.40)
    fr3, _, meta3 = render_pose(loc3, dx_shift=3, dy_shift=-10, override_weapon=(68, 92, 56, 92, 42, 92))
    dead_frs.append(fr3)
    dead_metas.append(meta3)

    # f4 & f5: lying flat on the ground (SOLES=99, height ~20px) using 90-deg CW rotated upper body + fallen Spade gripped by hand!
    up_crop = master_can[54:85, 52:78].copy()
    up_rot = np.rot90(up_crop, k=-1)
    for k, sc in [(4, 0), (5, 1)]:
        ck = np.zeros_like(master_can)
        pk = np.full((160, 160), -1, int)
        rh, rw = up_rot.shape[:2]
        y0 = SOLES - rh - 2 + sc
        x0 = 49
        sub = ck[y0:y0+rh, x0:x0+rw]
        ck[y0:y0+rh, x0:x0+rw] = np.where(up_rot[:, :, 3:4] > 0, up_rot, sub)
        # Draw fallen horizontal Monk's Spade at y = 93 + sc from Finial (68, 93) through R_Hand (56, 93) to Blade (42, 93)
        fx, fy, rhx, rhy, bx, by = 68, 93 + sc, 56, 93 + sc, 42, 92 + sc
        for cx in range(bx, fx + 1):
            ck[fy - 1, cx] = (*OUT, 255)
            ck[fy,     cx] = (*CPAL["i0"], 255)
            ck[fy + 1, cx] = (*CPAL["i1"], 255)
            ck[fy + 2, cx] = (*OUT, 255)
        stamp_rotated_blade(ck, pk, bx, by, fx, fy)
        paint(ck, [
            (fy - 2, fx - 2, "g0 v2 v4 g0"),
            (fy - 1, fx - 2, "g0 g1 v5 v3 g1"),
            (fy,     fx - 2, "g0 g1 i0 v4 g1"),
            (fy + 1, fx - 2, "g0 v2 v3 g0"),
            (rhy - 1, rhx - 2, "v2 s1 s2 s1"),
            (rhy,     rhx - 2, "v1 s1 s2 s2 b1"),
            (rhy + 1, rhx - 2, "v1 b1 s1 s1 b1"),
        ])
        ck[SOLES + 1:, :] = 0
        ck = K.finish(ck, OUT, SOLES, keep=None, pinholes=2)
        ck[SOLES + 1:, :] = 0
        dead_frs.append(ck)
        dead_metas.append(dict(fx=fx, fy=fy, rhx=rhx, rhy=rhy, lhx=62, lhy=90, bx=bx, by=by, hx=72, hy=86, vx=62, vy=88))

    strips["dead"] = dead_frs
    metas["dead"] = dead_metas

    return master_can, strips, metas
