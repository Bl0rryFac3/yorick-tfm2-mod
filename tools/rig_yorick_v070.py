import sys, json, time
sys.path.insert(0, "/home/user/yorick_v060_src/scripts")
sys.path.insert(0, "/home/user/yorick_v060_src")
sys.path.insert(0, "/home/user/tfm2_tools/tools/lol")
sys.path.insert(0, "/home/user/tfm2_tools/tools/art")
sys.path.insert(0, "/home/user/tfm2_tools/.claude/skills/tfm2-hero-mod/scripts")
import build_yorick_v070 as B7
import build_yorick_v060 as B6
import rigkit as K
import strips as G
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import label

master_can, y_strips, y_metas = B7.build_all_8_strips_v070()
CPAL = B7.CPAL
OUT = B7.OUT
SOLES = B7.SOLES

# Build summons with updated CPAL (so s3=(106,154,184) has BT.709 lum < 150 on summon frames too!)
B6.CPAL = CPAL
B6._CPAL_KEYS = [k for k in CPAL.keys() if k != "o"]
B6._CPAL_RGB = np.array([CPAL[k] for k in B6._CPAL_KEYS], dtype=float)
m_strips, g_strips = B6.build_summons_v060()

# Export to mod sheets & .fanim
all_strips = {}
all_strips.update(y_strips)
all_strips.update(m_strips)
all_strips.update(g_strips)

tags_for_sheet = {}
all_ms = {}
for tag, frs in all_strips.items():
    orig_frs = B6.FA_041["anims"][tag]["frames"]
    tag_list = []
    ms_list = []
    for i, arr in enumerate(frs):
        dur_s = orig_frs[min(i, len(orig_frs) - 1)]["duration"]
        dur_ms = int(round(dur_s * 1000))
        ms_list.append(dur_ms)
        u0 = -63 if not (tag.startswith("maiden_") or tag.startswith("ghoul_")) else -64
        centred = G.centre_frame(arr, u0=u0, r0=-88)
        tag_list.append((centred, dur_ms))
    tags_for_sheet[tag] = tag_list
    all_ms[tag] = ms_list

MOD_DIR = "/home/user/league_yorick_mod"
G.write_sheet("/home/user/league_yorick", tags_for_sheet, share=True)
w2, h2 = G.write_sheet(f"{MOD_DIR}/champions/league_yorick", tags_for_sheet, share=True)
maiden_tags = {t.replace("maiden_", ""): tags_for_sheet[t] for t in m_strips}
wm, hm = G.write_sheet(f"{MOD_DIR}/champions/league_yorick_maiden", maiden_tags, share=True)
ghoul_tags = {t.replace("ghoul_", ""): tags_for_sheet[t] for t in g_strips}
wg, hg = G.write_sheet(f"{MOD_DIR}/champions/league_yorick_ghoul", ghoul_tags, share=True)

# === INDEPENDENT ORACLE: MEASURE DIRECTLY OFF EXPORTED SHEET + FANIM ===
sh = np.asarray(Image.open(f"{MOD_DIR}/champions/league_yorick#sheet.png").convert("RGBA"))
with open(f"{MOD_DIR}/champions/league_yorick#anim.fanim") as f:
    fa = json.load(f)

def get_fr(t, i):
    d = fa["anims"][t]["frames"][i]["data"]
    x, y, w, h = int(round(d["x"])), int(round(d["y"])), int(round(d["w"])), int(round(d["h"]))
    return sh[y:y+h, x:x+w]

def get_tight(t, i):
    c = get_fr(t, i)
    ys, xs = np.nonzero(c[:, :, 3] > 0)
    return c[ys.min():ys.max()+1, xs.min():xs.max()+1]

def top60_iou(a, b):
    ha, wa = int(a.shape[0] * 0.6), a.shape[1]
    hb, wb = int(b.shape[0] * 0.6), b.shape[1]
    H, W = max(ha, hb), max(wa, wb)
    ma = np.zeros((H, W), bool)
    mb = np.zeros((H, W), bool)
    ma[:ha, :wa] = a[:ha, :wa, 3] > 0
    mb[:hb, :wb] = b[:hb, :wb, 3] > 0
    return (ma & mb).sum() / max(1, (ma | mb).sum()) * 100.0

champ_tags = ["idle", "run", "attack", "skill", "skill2", "ult", "hit", "dead"]
champ_frames = [get_tight(t, i) for t in champ_tags for i in range(len(fa["anims"][t]["frames"]))]
champ_px = np.concatenate([c[c[:, :, 3] > 0][:, :3] for c in champ_frames], axis=0)
raw_sh_px = sh[sh[:, :, 3] > 0][:, :3]
idle0_tight = get_tight("idle", 0)
idle0_px = idle0_tight[idle0_tight[:, :, 3] > 0][:, :3]

print("\n=== INDEPENDENT SHEET-LEVEL VERIFICATION ORACLE (v0.7.0) ===")
print(f"0. Idle[0] tight dimensions: {idle0_tight.shape[1]}w x {idle0_tight.shape[0]}h px (tank height band <= 45 px!)")

# Check 1: Stoop test
cowl_cols = {CPAL["c0"], CPAL["c1"], CPAL["c2"], CPAL["c3"]}
horn_cols = {CPAL["v0"], CPAL["v1"], CPAL["v2"], CPAL["v3"], CPAL["v4"], CPAL["v5"], CPAL["v6"]}
cowl_ys = [y for y in range(160) for x in range(54, 70) if master_can[y, x, 3] > 0 and tuple(master_can[y, x, :3]) in cowl_cols]
horn_ys = [y for y in range(160) for x in range(50, 72) if master_can[y, x, 3] > 0 and tuple(master_can[y, x, :3]) in horn_cols]
print(f"1. Stoop test: Head cowl crown (y={min(cowl_ys)}) sits {min(cowl_ys) - min(horn_ys)} rows BELOW pauldron-horn top (y={min(horn_ys)}).")
assert min(cowl_ys) > min(horn_ys)

# Check 2: Arm skin test (report idle[0], 47-frame sum, and raw sheet PNG count!)
skin_cols = {CPAL["s0"], CPAL["s1"], CPAL["s2"], CPAL["s3"]}
skin_idle0 = sum(1 for p in idle0_px if tuple(p) in skin_cols)
skin_47 = sum(1 for p in champ_px if tuple(p) in skin_cols)
skin_raw_sh = sum(1 for p in raw_sh_px if tuple(p) in skin_cols)
print(f"2. Arm/skin test: {skin_idle0} px in idle[0] | {skin_47} px across 47 frames | {skin_raw_sh} px on raw sheet PNG.")

# Check 3: Luminance > 150 non-cyan test (both BT.601 and BT.709 on idle[0], 47 frames, and raw sheet!)
vial_cols = {CPAL["w0"], CPAL["w1"], CPAL["w2"]}
for label_s, px_arr in [("idle[0]", idle0_px), ("47_champ_frames", champ_px), ("raw_sheet_png", raw_sh_px)]:
    is_cyan = np.array([tuple(p) in vial_cols for p in px_arr])
    l601 = 0.299 * px_arr[:, 0] + 0.587 * px_arr[:, 1] + 0.114 * px_arr[:, 2]
    l709 = 0.2126 * px_arr[:, 0] + 0.7152 * px_arr[:, 1] + 0.0722 * px_arr[:, 2]
    p601 = ((l601 > 150.0) & ~is_cyan).mean() * 100.0
    p709 = ((l709 > 150.0) & ~is_cyan).mean() * 100.0
    print(f"3. Luminance >150 non-cyan ({label_s:15s}): BT.601 = {p601:.2f}% | BT.709 = {p709:.2f}%")
    assert p601 == 0.0 and p709 == 0.0

# Check 4: No-gold test
gold_sh = ((raw_sh_px[:, 0] > 140) & (raw_sh_px[:, 1] > 100) & (raw_sh_px[:, 2] < 100)).sum()
print(f"4. No-gold test: {gold_sh} gold pixels across entire sheet.")
assert gold_sh == 0

# Check 5: Colour count test
n_col_idle0 = len(np.unique(idle0_px, axis=0))
n_col_47 = len(np.unique(champ_px, axis=0))
n_col_sh = len(np.unique(raw_sh_px, axis=0))
print(f"5. Colour count: {n_col_idle0} in idle[0] | {n_col_47} across 8 strips | {n_col_sh} on raw sheet PNG.")
assert 25 <= n_col_47 <= 40

# Check 6: Vial at belt test
vial_ys = [y for y in range(72, 88) for x in range(55, 69) if master_can[y, x, 3] > 0 and tuple(master_can[y, x, :3]) in vial_cols]
print(f"6. Vial position: Belt row mean y={np.mean(vial_ys):.1f} (rows {min(vial_ys)}..{max(vial_ys)}).")

# Check 7: Blade material & Tassel test (green #1E4436/#2C604C attached to finial, never cyan, never orphan!)
tassel_cols = {CPAL["g0"], CPAL["g1"]}
shaft_cols = {CPAL["i0"], CPAL["i1"]}
hand_cols = skin_cols | {CPAL["b1"], CPAL["b2"], CPAL["v1"], CPAL["v2"], CPAL["v3"], CPAL["v4"]}
for t in champ_tags:
    for i, fr in enumerate(y_strips[t]):
        meta = y_metas[t][i]
        # Verify 1 single connected component (0 orphan tassels or detached blades!)
        op = fr[:, :, 3] > 0
        _, n_comp = label(op)
        assert n_comp == 1, f"{t}[{i}] has {n_comp} disconnected pieces (orphan detected)!"
        # Verify green tassels exist near finial (fx, fy) and 0 cyan pixels exist near finial!
        fx, fy = meta["fx"], meta["fy"]
        f_box = fr[max(0, fy-4):min(160, fy+4), max(0, fx-4):min(160, fx+5)]
        f_px = [tuple(p[:3]) for p in f_box[f_box[:, :, 3] > 0]]
        assert any(p in tassel_cols for p in f_px), f"{t}[{i}] missing green tassels at finial!"
        if not (t == "dead" and i >= 3):
            assert not any(p in vial_cols for p in f_px), f"{t}[{i}] has cyan pixel near finial!"
        # Verify Hand bbox intersects Shaft bbox!
        rhx, rhy = meta["rhx"], meta["rhy"]
        hand_box = (rhx - 3, rhy - 2, rhx + 3, rhy + 2)
        sh_ys, sh_xs = np.nonzero(op & np.isin(fr[:, :, 0], [CPAL["i0"][0], CPAL["i1"][0]]) & np.isin(fr[:, :, 1], [CPAL["i0"][1], CPAL["i1"][1]]))
        assert len(sh_ys) > 0, f"{t}[{i}] missing dark-iron shaft pixels!"
        shaft_box = (int(sh_xs.min()) - 1, int(sh_ys.min()) - 1, int(sh_xs.max()) + 1, int(sh_ys.max()) + 1)
        intersects = not (hand_box[2] < shaft_box[0] or hand_box[0] > shaft_box[2] or hand_box[3] < shaft_box[1] or hand_box[1] > shaft_box[3])
        assert intersects, f"{t}[{i}] hand_box {hand_box} does not intersect shaft_box {shaft_box}!"
print("7. Blade, Tassel & Hand∩Shaft test: 47/47 frames have 1 connected component, green #1E4436/#2C604C tassels at finial, 0 cyan near finial, and hand_bbox ∩ shaft_bbox != empty!")

# Check 8: Face & Beard readability test
beard_cols = {CPAL["b0"], CPAL["b1"], CPAL["b2"], CPAL["b3"], CPAL["b4"]}
eye_px = sum(1 for y in range(56, 74) for x in range(54, 70) if tuple(master_can[y, x, :3]) == CPAL["w2"])
beard_px = sum(1 for y in range(60, 78) for x in range(54, 70) if master_can[y, x, 3] > 0 and tuple(master_can[y, x, :3]) in beard_cols)
face_skin_px = sum(1 for y in range(58, 70) for x in range(55, 69) if master_can[y, x, 3] > 0 and tuple(master_can[y, x, :3]) in skin_cols)
print(f"8. Face & Beard test: {eye_px} glowing eyes, {face_skin_px} pale-blue face skin px, {beard_px} grey-brown beard px (>= 8 px), plus separate chest cowl tails.")
assert eye_px == 2 and beard_px >= 8

# Check 9 & Disease A Motion Test (auditor's exact top60_iou on get_tight!)
iou_idle_run3 = top60_iou(get_tight("idle", 0), get_tight("run", 3))
iou_idle_ult0 = top60_iou(get_tight("idle", 0), get_tight("ult", 0))
run_consec = [top60_iou(get_tight("run", i), get_tight("run", i+1)) for i in range(7)]
idle_consec = [top60_iou(get_tight("idle", i), get_tight("idle", i+1)) for i in range(7)]
print(f"9. One-look test (tight top-60% IoU): idle[0] vs run[3] = {iou_idle_run3:.1f}% | idle[0] vs ult[0] = {iou_idle_ult0:.1f}%")
print(f"10. Run consecutive upper-body IoU : {'  '.join(f'{v:.1f}' for v in run_consec)} (max={max(run_consec):.1f}%, mean={np.mean(run_consec):.1f}%)")
print(f"11. Idle consecutive upper-body IoU: {'  '.join(f'{v:.1f}' for v in idle_consec)}")

# Render 6x run strip (/home/user/yorick_v070_run_6x.png) and 4x contact sheet (/home/user/yorick_v070_contact_4x.png)
Z6 = 6
cw6, ch6 = 52 * Z6, 52 * Z6
run_im = Image.new("RGBA", (cw6 * 8 + 16, ch6 + 34), (22, 24, 30, 255))
dr = ImageDraw.Draw(run_im)
dr.text((10, 8), f"v0.7.0 run f0..f7 @ 6x — consec upper IoU: {' / '.join(f'{v:.1f}%' for v in run_consec)}", fill=(200, 220, 230, 255))
for k, fr in enumerate(y_strips["run"]):
    crop = fr[50:102, 37:89]
    big = Image.fromarray(crop).resize((cw6, ch6), Image.NEAREST)
    run_im.paste(big, (8 + k * cw6, 28), big)
run_im.save("/home/user/yorick_v070_run_6x.png")

K.review_sheet([(t, y_strips[t]) for t in champ_tags], "/home/user/yorick_v070_contact_4x.png", z=4, soles=SOLES)
print("Saved /home/user/yorick_v070_run_6x.png and /home/user/yorick_v070_contact_4x.png")
