# Release notes — v0.7.0 (initial public release)
---

**Yorick for TFM2-League-Heroes — first public release.** A pixel-art Yorick champion mod:
18 sprite strips (Yorick idle/run/attack/skill/skill2/ult/hit/dead plus Waters-of-Life maiden
and ghoul summons), built by per-frame sampling of the publicly viewable Yorick 3D model's
skeletal animation clips, quantized to a 31-colour palette at 42×45 px.

## Highlights

- **Real motion.** Per-frame GLB clip sampling (`idle1`, `run_base`, `attack1`, `spell1`,
  `spell3`, `spell4`, `death`): consecutive run-frame upper-silhouette IoU max 66.8%
  (mean 57.4%) — torso lean, shoulder counter-rotation, arm and spade swing per stride.
- **Prop attachment.** 100/100 frames a single connected component; the hand grips the spade
  shaft in every frame; green finial tassels, no orphan pixels.
- **Palette discipline.** 31 colours; 0 gold pixels; 0.00% non-cyan pixels above luminance 150
  under both BT.601 and BT.709.
- **One look across all strips.** idle vs run 92.0%, idle vs ult 100.0% top-60 silhouette IoU.
- **Verified.** Every number measured directly off the exported sheet + `.fanim` by the
  sheet-level oracle in `tools/rig_yorick_v070.py`, and independently re-measured in a
  separate verification sandbox.

## Contents

- `league_yorick_mod.zip` (attached below) — installable mod: dll/so, champion data, sprite
  sheets, `.fanim` animation files, icons, effects, addon Rust source
- Repository tree: `mod/` (same, unzipped), `tools/` (builder + verification oracle),
  `preview/` (renders), `NOTICE.md` (fan-content statement), `LICENSE` (MIT)

## Credits & legal

TFM2 engine and mod API (c) BoDUD (MIT). League of Legends and Yorick are Riot Games IP;
this is non-commercial fan content — see `NOTICE.md`.

---
