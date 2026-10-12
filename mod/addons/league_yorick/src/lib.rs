//! Native Teamfight Manager 2 Add-on for Yorick (`league_yorick` v0.4.2)
//!
//! Spawns actual autonomous and controllable combat units on the battlefield:
//!   1. **Mist Walker Ghouls** (up to 4 active per Yorick, 8s duration):
//!      - Raised from nearby enemy deaths (`alive -> dead` transition within grave range),
//!      - Raised when Yorick casts **Q (Last Rites)** from Level 1 (`cooldowns()` + `BUFF_Q_CAST`),
//!      - Raised when Yorick casts **E (Mourning Mist)** (`cooldowns()` + `BUFF_W_CAST`),
//!      - Raised in a pair when Yorick casts **R (Eulogy of the Isles)**, and periodically
//!        raised by The Maiden of the Mist during combat while she is alive.
//!   2. **The Maiden of the Mist** (1 active per Yorick, 20s duration):
//!      - Summoned as an independent ranged combat unit (`maiden_*` animations)
//!        when Yorick casts **R (Eulogy of the Isles)** at Level 5+ (`cooldowns()` + `BUFF_R_CAST`
//!        + Level 5 combat fail-safe),
//!      - Accompanies Yorick, attacks nearby enemies from range, raises Mist Walkers in combat
//!        and from nearby enemy deaths, and empowers Yorick's attacks against her target
//!        with bonus 3% Max HP magic damage and healing.
//!   3. **Mourning Mist (E) Command / Leap Control**:
//!      - When Yorick casts **Mourning Mist** and marks an enemy with
//!        `league_yorick_e_mark`, all active Mist Walkers and the Maiden immediately
//!        leap (`CcKindV1::ForceMove`) onto the marked target and gain a brief
//!        `+15%` attack-speed & movement-speed buff (`league_yorick_ghoul_frenzy`).

use std::collections::BTreeMap;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{LazyLock, Mutex};

use mod_api_stable::*;

mod view;

const MOD_ID: &str = "league_yorick";
const HERO_ID: &str = "league_yorick";

// Marker buffs placed on Yorick / targets by `.data_champion` skills or native code:
const BUFF_Q_CAST: &str = "league_yorick_q_cast";
const BUFF_W_CAST: &str = "league_yorick_w_cast";
const BUFF_W_WALL: &str = "league_yorick_w_wall";
const BUFF_E_MARK: &str = "league_yorick_e_mark";
const BUFF_R_CAST: &str = "league_yorick_r_cast";
const BUFF_R_LEGACY: &str = "league_yorick_r_maiden";
const BUFF_GHOUL_FRENZY: &str = "league_yorick_ghoul_frenzy";
const BUFF_MAIDEN_AURA: &str = "league_yorick_maiden_aura";

// Internal unit-identity buffs attached to spawned units (distinct from legacy view_buff names):
pub const BUFF_UNIT_GHOUL: &str = "league_yorick_unit_ghoul";
pub const BUFF_UNIT_MAIDEN: &str = "league_yorick_unit_maiden";

const MAX_GHOULS: usize = 4;
const GHOUL_DURATION_TICKS: u64 = 480; // 8 seconds at 60 ticks/sec
const MAIDEN_DURATION_TICKS: u64 = 1200; // 20 seconds at 60 ticks/sec

// Distances in TFM2 fixed world units (1000 units ~= 1px on arena):
const GRAVE_RADIUS: f64 = 105_000.0;
const LEAP_MAX_RANGE: f64 = 220_000.0;
const TETHER_MAX_RANGE: f64 = 140_000.0;
const LEAP_SPEED: f64 = 3_400.0;

static LOG_LOCK: Mutex<()> = Mutex::new(());
static PRESIM_LOGS: AtomicUsize = AtomicUsize::new(0);
const MAX_PRESIM_LOGS: usize = 80;

fn resolve_log_path() -> PathBuf {
    if let Some(appdata) = std::env::var_os("APPDATA") {
        let dir = PathBuf::from(appdata)
            .join("TeamSamoyed")
            .join("TeamfightManager2")
            .join("data");
        if dir.is_dir() {
            return dir.join("league_yorick.log");
        }
    }
    PathBuf::from("league_yorick.log")
}

static LOG_PATH: LazyLock<PathBuf> = LazyLock::new(resolve_log_path);

pub(crate) fn wlog(msg: &str) {
    let _g = LOG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&*LOG_PATH) {
        let _ = writeln!(f, "{msg}");
        let _ = f.flush();
    }
}

fn head(sim: &StableSim<'_>, id: usize) -> String {
    let o = sim.sim_origin().unwrap_or_default();
    let kind = match SimOriginKindV1::from_code(o.kind) {
        Some(SimOriginKindV1::ServerPresim) => "presim",
        Some(SimOriginKindV1::ClientMatchView) => "view",
        Some(SimOriginKindV1::ClientSpectate) => "spectate",
        Some(SimOriginKindV1::ClientReplay) => "replay",
        Some(SimOriginKindV1::Tool) => "tool",
        _ => "sim",
    };
    let m = |v: u64| {
        if v == SimOriginV1::NONE {
            "-".to_string()
        } else {
            v.to_string()
        }
    };
    let t = sim.tick();
    format!(
        "[{kind} m={} s={}] t={t} ({}:{:02}) #{id}",
        m(o.match_id),
        m(o.set_index),
        t / 3600,
        t / 60 % 60
    )
}

fn sim_log(sim: &StableSim<'_>, id: usize, msg: &str) {
    let o = sim.sim_origin().unwrap_or_default();
    if matches!(
        SimOriginKindV1::from_code(o.kind),
        Some(SimOriginKindV1::ServerPresim)
    ) {
        let prev = PRESIM_LOGS.fetch_add(1, Ordering::Relaxed);
        if prev >= MAX_PRESIM_LOGS {
            return;
        }
    }
    wlog(&format!("{} {msg}", head(sim, id)));
}

/// Returns `(is_client_view, is_presim, match_id, set_index)` for recording entity roles in `view.rs`.
fn sim_view_info(sim: &StableSim<'_>) -> (bool, bool, u64, u64) {
    match sim.sim_origin() {
        Some(o) => {
            let kind = SimOriginKindV1::from_code(o.kind);
            let is_view = matches!(
                kind,
                Some(
                    SimOriginKindV1::ClientMatchView
                        | SimOriginKindV1::ClientSpectate
                        | SimOriginKindV1::ClientReplay
                )
            );
            let is_presim = matches!(kind, Some(SimOriginKindV1::ServerPresim));
            (is_view, is_presim, o.match_id, o.set_index)
        }
        None => (false, false, 0, 0),
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct SimKey {
    origin_kind: u32,
    match_id: u64,
    set_index: u64,
    seed: u64,
    yorick_id: usize,
}

impl SimKey {
    fn of(sim: &StableSim<'_>, yorick_id: usize) -> Self {
        let o = sim.sim_origin().unwrap_or_default();
        Self {
            origin_kind: o.kind,
            match_id: o.match_id,
            set_index: o.set_index,
            seed: sim.seed(),
            yorick_id,
        }
    }
}

#[derive(Clone, Default)]
struct YorickState {
    last_tick: usize,
    first_tick: usize,
    started: bool,
    ghouls: Vec<usize>,
    maiden: Option<usize>,
    alive_seen: Vec<usize>,
    dead_seen: Vec<usize>,
    graves: usize,
    had_q: bool,
    had_w: bool,
    had_r: bool,
    prev_skill_cd: usize,
    prev_skill2_cd: usize,
    prev_ult_cd: usize,
    q_cd_handled: bool,
    w_cd_handled: bool,
    r_cd_handled: bool,
    lvl5_combat_ticks: usize,
    last_q_tick: usize,
    last_w_tick: usize,
    last_r_tick: usize,
    last_maiden_raise_tick: usize,
    last_leap_tick: usize,
    last_siphon_tick: usize,
}

// Global thread-safe BTreeMap keyed by SimKey.
// Never cleared wholesale; only old ServerPresim entries are pruned when > 128.
static MATCH_STATES: LazyLock<Mutex<BTreeMap<SimKey, YorickState>>> =
    LazyLock::new(|| Mutex::new(BTreeMap::new()));

fn dist(a: (u64, u64), b: (u64, u64)) -> f64 {
    let dx = a.0 as f64 - b.0 as f64;
    let dy = a.1 as f64 - b.1 as f64;
    (dx * dx + dy * dy).sqrt()
}

fn has_buff(e: &StableEntity<'_, '_>, buff_name: &str) -> bool {
    (0..e.buff_count())
        .filter_map(|i| e.buff_at(i))
        .any(|b| b.name() == buff_name)
}

fn is_summon_entity(e: &StableEntity<'_, '_>) -> bool {
    has_buff(e, BUFF_UNIT_GHOUL) || has_buff(e, BUFF_UNIT_MAIDEN)
}

/// Reads `(level, skill_cd, skill2_cd, ult_cd)` for the player controlling `yorick_id`.
fn read_yorick_player_info(sim: &StableSim<'_>, yorick_id: usize) -> (usize, usize, usize, usize) {
    let pcount = sim.player_count();
    for i in 0..pcount {
        let Some(p) = sim.player_at(i) else {
            continue;
        };
        if p.champion().map(|c| c.id()) == Some(yorick_id) {
            let lvl = p.level().max(1);
            let (_, s1, s2, ult) = p.cooldowns().unwrap_or((0, 0, 0, 0));
            return (lvl, s1, s2, ult);
        }
    }
    (1, 0, 0, 0)
}

fn force_leap(
    sim: &mut StableSim<'_>,
    unit_id: usize,
    from: (u64, u64),
    to: (u64, u64),
    speed: f64,
) {
    let dx = to.0 as f64 - from.0 as f64;
    let dy = to.1 as f64 - from.1 as f64;
    let d = (dx * dx + dy * dy).sqrt();
    if d < 12_000.0 {
        return;
    }
    let land_ratio = ((d - 10_000.0) / d).clamp(0.2, 1.0);
    let move_dx = dx * land_ratio;
    let move_dy = dy * land_ratio;
    let move_dist = (move_dx * move_dx + move_dy * move_dy).sqrt();
    let ticks = (move_dist / speed).ceil().clamp(4.0, 24.0);
    let mut cc = CcV1::of_kind(CcKindV1::ForceMove, ticks as u64);
    cc.dx = move_dx.round() as i64;
    cc.dy = move_dy.round() as i64;
    cc.speed = (move_dist / ticks).ceil() as u64;
    sim.apply_cc(unit_id, &cc);
}

/// Spawns a single Mist Walker Ghoul with distinct formation offsets around Yorick.
fn spawn_mist_walker(
    sim: &mut StableSim<'_>,
    yorick_id: usize,
    team: usize,
    pos: (u64, u64),
    yorick_stat: &StatV1,
    slot_idx: usize,
) -> Option<usize> {
    const OFFSETS: [(f64, f64); 4] = [
        (22_000.0, -18_000.0),
        (22_000.0, 18_000.0),
        (-20_000.0, 22_000.0),
        (28_000.0, 0.0),
    ];
    let (ox, oy) = OFFSETS[slot_idx % OFFSETS.len()];
    let sx = (pos.0 as f64 + ox).max(10_000.0) as u64;
    let sy = (pos.1 as f64 + oy).max(10_000.0) as u64;

    let ghoul_hp = 70 + (yorick_stat.hp.max(400) * 8) / 100;
    let ghoul_ad = 5 + (yorick_stat.attack.max(40) * 12) / 100;
    let stat = StatV1 {
        hp: ghoul_hp,
        attack: ghoul_ad,
        magic_power: 0,
        defence: (yorick_stat.defence / 3).max(10),
        magic_resistance: (yorick_stat.magic_resistance / 3).max(10),
        move_speed: yorick_stat.move_speed.clamp(1850, 2100),
        hp_regen: 0,
        stack: 0,
        crit_chance: 0,
    };
    let atk = UnitAttackV1 {
        attack_ratio: 100,
        attack: 0,
        range: 20_000,
        cooltime: 65,
        duration: 20,
        start_timing: 10,
        cancelable: false,
        attack_type: AttackTypeV1::BaseAttack.code(),
    };
    let id = sim.spawn_unit(
        HERO_ID,
        yorick_id,
        team,
        sx,
        sy,
        GHOUL_DURATION_TICKS,
        &stat,
        &atk,
    );
    if let Some(gid) = id {
        sim.add_buff(gid, &BuffV1::named(BUFF_UNIT_GHOUL));
        let (is_view, is_presim, m, s) = sim_view_info(sim);
        if !is_presim {
            view::note_role(is_view, m, s, gid as u64, view::Role::Ghoul);
        }
    }
    id
}

/// Spawns The Maiden of the Mist as an independent ranged unit (`maiden_*` animations).
fn spawn_maiden_of_the_mist(
    sim: &mut StableSim<'_>,
    yorick_id: usize,
    team: usize,
    pos: (u64, u64),
    yorick_stat: &StatV1,
) -> Option<usize> {
    let sx = pos.0.saturating_sub(28_000).max(10_000);
    let sy = pos.1.saturating_sub(14_000).max(10_000);

    let maiden_hp = 260 + (yorick_stat.hp.max(500) * 40) / 100;
    let maiden_atk = 18
        + (yorick_stat.attack.max(50) * 25) / 100
        + (yorick_stat.magic_power * 35) / 100;
    let stat = StatV1 {
        hp: maiden_hp,
        attack: maiden_atk,
        magic_power: yorick_stat.magic_power.max(30),
        defence: ((yorick_stat.defence * 55) / 100).max(20),
        magic_resistance: ((yorick_stat.magic_resistance * 55) / 100).max(20),
        move_speed: yorick_stat.move_speed.clamp(1850, 2050),
        hp_regen: 8,
        stack: 0,
        crit_chance: 0,
    };
    let atk = UnitAttackV1 {
        attack_ratio: 100,
        attack: 0,
        range: 52_000,
        cooltime: 55,
        duration: 22,
        start_timing: 11,
        cancelable: false,
        attack_type: AttackTypeV1::BaseAttack.code(),
    };
    let id = sim.spawn_unit(
        HERO_ID,
        yorick_id,
        team,
        sx,
        sy,
        MAIDEN_DURATION_TICKS,
        &stat,
        &atk,
    );
    if let Some(mid) = id {
        sim.add_buff(mid, &BuffV1::named(BUFF_UNIT_MAIDEN));
        let (is_view, is_presim, m, s) = sim_view_info(sim);
        if !is_presim {
            view::note_role(is_view, m, s, mid as u64, view::Role::Maiden);
        }
    }
    id
}

/// Core per-tick controller for a Yorick champion (`yorick_id`) in the match.
fn tick_yorick_summons(sim: &mut StableSim<'_>, yorick_id: usize) {
    let tick = sim.tick();
    let Some(yorick) = sim.get_entity(yorick_id) else {
        return;
    };
    if !yorick.is_champion() || is_summon_entity(&yorick) {
        return;
    }

    let (is_view, is_presim, m_id, s_id) = sim_view_info(sim);
    if is_view {
        view::note_view(m_id, s_id);
    }
    if !is_presim {
        // Always register Yorick as Role::Champion even when he is dead so his dead animation never swaps to ghoul_dead!
        view::note_role(is_view, m_id, s_id, yorick_id as u64, view::Role::Champion);
    }

    if !yorick.is_alive() {
        return;
    }

    let y_team = yorick.team();
    let y_pos = yorick.pos();
    let y_stat = yorick.stat();
    // Only check short-duration cast marker buffs (15 ticks) — NEVER long-duration buffs!
    let has_q = has_buff(&yorick, BUFF_Q_CAST);
    let has_w = has_buff(&yorick, BUFF_W_CAST);
    let has_r = has_buff(&yorick, BUFF_R_CAST) || has_buff(&yorick, BUFF_R_LEGACY);
    let (p_level, skill_cd, skill2_cd, ult_cd) = read_yorick_player_info(sim, yorick_id);

    let key = SimKey::of(sim, yorick_id);

    let mut state = {
        let Ok(mut map) = MATCH_STATES.lock() else {
            return;
        };
        if map.len() > 128 {
            // Only evict ServerPresim entries so active ClientMatchView states are never wiped!
            let presim_code = SimOriginKindV1::ServerPresim as u32;
            if let Some(&old_k) = map.keys().find(|k| k.origin_kind == presim_code) {
                map.remove(&old_k);
            }
        }
        map.get(&key).cloned().unwrap_or_default()
    };

    // Guard against duplicate calls on the exact same tick (e.g. MatchHook + Passive).
    if state.started && tick == state.last_tick {
        return;
    }
    if tick < state.last_tick || tick <= 1 {
        state = YorickState::default();
    }
    state.last_tick = tick;

    if !state.started {
        state.started = true;
        state.first_tick = tick;
        state.prev_skill_cd = skill_cd;
        state.prev_skill2_cd = skill2_cd;
        state.prev_ult_cd = ult_cd;
        if is_view {
            sim_log(
                sim,
                yorick_id,
                &format!("Yorick active in match (team={y_team}, lvl={p_level})"),
            );
        }
    }

    // 1. Reconcile active Mist Walkers & Maiden against live entities in the simulation
    state
        .ghouls
        .retain(|&gid| sim.get_entity(gid).map_or(false, |e| e.is_alive()));
    if let Some(mid) = state.maiden {
        if !sim.get_entity(mid).map_or(false, |e| e.is_alive()) {
            state.maiden = None;
        }
    }

    let total_entities = sim.entity_count();
    let mut nearest_enemy_dist = f64::MAX;
    for idx in 0..total_entities {
        let Some(e) = sim.entity_at(idx) else {
            continue;
        };
        if !e.is_alive() {
            continue;
        }
        let eid = e.id();
        if e.team() == y_team {
            if eid != yorick_id {
                if has_buff(&e, BUFF_UNIT_MAIDEN) {
                    if state.maiden.is_none() {
                        state.maiden = Some(eid);
                    }
                    if !is_presim {
                        view::note_role(is_view, m_id, s_id, eid as u64, view::Role::Maiden);
                    }
                } else if has_buff(&e, BUFF_UNIT_GHOUL) {
                    if !state.ghouls.contains(&eid) && state.ghouls.len() < MAX_GHOULS {
                        state.ghouls.push(eid);
                    }
                    if !is_presim {
                        view::note_role(is_view, m_id, s_id, eid as u64, view::Role::Ghoul);
                    }
                }
            }
        } else {
            let d = dist(e.pos(), y_pos);
            if d < nearest_enemy_dist {
                nearest_enemy_dist = d;
            }
        }
    }

    // 2. Passive (Shepherd of Souls):
    // Track enemies that are ALIVE near Yorick/Maiden after 45 ticks into the simulation.
    let elapsed = tick.saturating_sub(state.first_tick);
    if elapsed >= 45 {
        for idx in 0..total_entities {
            let Some(e) = sim.entity_at(idx) else {
                continue;
            };
            let eid = e.id();
            if eid == yorick_id || e.team() == y_team || is_summon_entity(&e) {
                continue;
            }
            let d_yorick = dist(e.pos(), y_pos);
            let d_maiden = state
                .maiden
                .and_then(|mid| sim.get_entity(mid))
                .map_or(f64::MAX, |m| dist(e.pos(), m.pos()));

            if e.is_alive() && e.hp().0 > 0 {
                if (d_yorick <= GRAVE_RADIUS * 1.4 || d_maiden <= GRAVE_RADIUS * 1.4)
                    && !state.alive_seen.contains(&eid)
                {
                    state.alive_seen.push(eid);
                    if state.alive_seen.len() > 128 {
                        state.alive_seen.remove(0);
                    }
                }
            } else if state.alive_seen.contains(&eid) && !state.dead_seen.contains(&eid) {
                state.alive_seen.retain(|&x| x != eid);
                state.dead_seen.push(eid);
                if state.dead_seen.len() > 128 {
                    state.dead_seen.remove(0);
                }
                if d_yorick <= GRAVE_RADIUS || d_maiden <= GRAVE_RADIUS {
                    if state.maiden.is_some() && state.ghouls.len() < MAX_GHOULS {
                        let slot = state.ghouls.len();
                        if let Some(gid) =
                            spawn_mist_walker(sim, yorick_id, y_team, y_pos, &y_stat, slot)
                        {
                            state.ghouls.push(gid);
                            sim_log(
                                sim,
                                yorick_id,
                                &format!(
                                    "Maiden raised Mist Walker ghoul #{gid} from fallen enemy #{eid} (active={})",
                                    state.ghouls.len()
                                ),
                            );
                        }
                    } else {
                        state.graves = (state.graves + 1).min(MAX_GHOULS);
                    }
                }
            }
        }
    }

    // If 3+ graves have accumulated from nearby enemy deaths, Shepherd of Souls awakens them!
    if state.graves >= 3 && state.ghouls.len() < MAX_GHOULS {
        let to_spawn = state.graves.min(MAX_GHOULS - state.ghouls.len());
        state.graves = state.graves.saturating_sub(to_spawn);
        for _ in 0..to_spawn {
            let slot = state.ghouls.len();
            if let Some(gid) = spawn_mist_walker(sim, yorick_id, y_team, y_pos, &y_stat, slot) {
                state.ghouls.push(gid);
                sim_log(
                    sim,
                    yorick_id,
                    &format!(
                        "Shepherd of Souls awakened Mist Walker ghoul #{gid} from graves (active={})",
                        state.ghouls.len()
                    ),
                );
            }
        }
    }

    // 3. Dual-Redundant Skill Cast Detection (Engine Player::cooldowns() + Short Cast Buffs):
    if skill_cd < 40 {
        state.q_cd_handled = false;
    }
    if skill2_cd < 40 {
        state.w_cd_handled = false;
    }
    if ult_cd < 100 {
        state.r_cd_handled = false;
    }

    let q_cd_jump = skill_cd > state.prev_skill_cd + 10 || (skill_cd >= 100 && !state.q_cd_handled);
    let w_cd_jump =
        skill2_cd > state.prev_skill2_cd + 10 || (skill2_cd >= 150 && !state.w_cd_handled);
    let r_cd_jump = ult_cd > state.prev_ult_cd + 10 || (ult_cd >= 600 && !state.r_cd_handled);

    state.prev_skill_cd = skill_cd;
    state.prev_skill2_cd = skill2_cd;
    state.prev_ult_cd = ult_cd;

    let q_buff_edge = has_q && !state.had_q;
    let w_buff_edge = has_w && !state.had_w;
    let r_buff_edge = has_r && !state.had_r;
    state.had_q = has_q;
    state.had_w = has_w;
    state.had_r = has_r;

    let q_edge = (q_cd_jump || q_buff_edge)
        && (state.last_q_tick == 0 || tick.saturating_sub(state.last_q_tick) >= 30);
    let w_edge = (w_cd_jump || w_buff_edge)
        && (state.last_w_tick == 0 || tick.saturating_sub(state.last_w_tick) >= 35);

    if q_edge || w_edge {
        if q_edge {
            state.last_q_tick = tick;
            state.q_cd_handled = true;
        }
        if w_edge {
            state.last_w_tick = tick;
            state.w_cd_handled = true;
        }
        let to_spawn = (1 + state.graves).min(MAX_GHOULS);
        state.graves = 0;
        for _ in 0..to_spawn {
            if state.ghouls.len() >= MAX_GHOULS {
                break;
            }
            let slot = state.ghouls.len();
            if let Some(gid) = spawn_mist_walker(sim, yorick_id, y_team, y_pos, &y_stat, slot) {
                state.ghouls.push(gid);
                sim_log(
                    sim,
                    yorick_id,
                    &format!(
                        "{} cast spawned Mist Walker ghoul #{gid} at ({:.1},{:.1}) (lvl={p_level}, active={})",
                        if q_edge { "Q Last Rites" } else { "E Mourning Mist" },
                        y_pos.0 as f64 / 1000.0,
                        y_pos.1 as f64 / 1000.0,
                        state.ghouls.len()
                    ),
                );
            }
        }
    }

    // 4. R (Eulogy of the Isles): Summon The Maiden of the Mist + 2 Mist Walkers
    // Track Level 5+ combat ticks as a fail-safe if AI delays targeted ult cast
    if p_level >= 5 && nearest_enemy_dist <= 70_000.0 {
        state.lvl5_combat_ticks += 1;
    } else {
        state.lvl5_combat_ticks = 0;
    }
    let r_failsafe = p_level >= 5
        && state.maiden.is_none()
        && state.lvl5_combat_ticks >= 45
        && (state.last_r_tick == 0 || tick.saturating_sub(state.last_r_tick) >= 1800);

    let r_edge = (r_cd_jump || r_buff_edge || r_failsafe)
        && (state.last_r_tick == 0 || tick.saturating_sub(state.last_r_tick) >= 180);

    if r_edge && state.maiden.is_none() {
        state.last_r_tick = tick;
        state.r_cd_handled = true;
        state.last_maiden_raise_tick = tick;
        if let Some(mid) = spawn_maiden_of_the_mist(sim, yorick_id, y_team, y_pos, &y_stat) {
            state.maiden = Some(mid);
            let mut aura = BuffV1::timed(BUFF_MAIDEN_AURA, MAIDEN_DURATION_TICKS as usize);
            aura.attack_speed_mult = 15;
            sim.add_buff(mid, &aura);
        }
        for _ in 0..2 {
            if state.ghouls.len() >= MAX_GHOULS {
                break;
            }
            let slot = state.ghouls.len();
            if let Some(gid) = spawn_mist_walker(sim, yorick_id, y_team, y_pos, &y_stat, slot) {
                state.ghouls.push(gid);
            }
        }
        sim_log(
            sim,
            yorick_id,
            &format!(
                "R Eulogy of the Isles spawned Maiden {:?} + ghouls {:?} (lvl={p_level})",
                state.maiden, state.ghouls
            ),
        );
    }

    // While The Maiden of the Mist is alive, she periodically raises a Mist Walker every 4s (240 ticks) in combat
    if let Some(mid) = state.maiden {
        if state.ghouls.len() < MAX_GHOULS
            && nearest_enemy_dist <= 95_000.0
            && tick.saturating_sub(state.last_maiden_raise_tick) >= 240
        {
            state.last_maiden_raise_tick = tick;
            let m_pos = sim.get_entity(mid).map_or(y_pos, |m| m.pos());
            let slot = state.ghouls.len();
            if let Some(gid) = spawn_mist_walker(sim, yorick_id, y_team, m_pos, &y_stat, slot) {
                state.ghouls.push(gid);
                sim_log(
                    sim,
                    yorick_id,
                    &format!(
                        "Maiden #{mid} summoned Mist Walker ghoul #{gid} in combat (active={})",
                        state.ghouls.len()
                    ),
                );
            }
        }
    }

    // 5. E (Mourning Mist): Command all Mist Walkers & Maiden to leap onto E-marked enemy
    let mut marked_target: Option<(usize, (u64, u64), usize)> = None;
    for idx in 0..total_entities {
        let Some(e) = sim.entity_at(idx) else {
            continue;
        };
        if !e.is_alive() || e.team() == y_team {
            continue;
        }
        if has_buff(&e, BUFF_E_MARK) || has_buff(&e, BUFF_W_WALL) {
            let d = dist(e.pos(), y_pos);
            if d <= LEAP_MAX_RANGE {
                let (_, max_hp) = e.hp();
                marked_target = Some((e.id(), e.pos(), max_hp));
                break;
            }
        }
    }

    if let Some((target_id, target_pos, target_max_hp)) = marked_target {
        if tick.saturating_sub(state.last_leap_tick) >= 50 {
            state.last_leap_tick = tick;
            if state.ghouls.is_empty() {
                if let Some(gid) = spawn_mist_walker(sim, yorick_id, y_team, y_pos, &y_stat, 0) {
                    state.ghouls.push(gid);
                }
            }
            if !state.ghouls.is_empty() || state.maiden.is_some() {
                sim_log(
                    sim,
                    yorick_id,
                    &format!(
                        "Mourning Mist hit #{target_id}: {} ghouls leaping {:?}, maiden={:?}",
                        state.ghouls.len(),
                        state.ghouls,
                        state.maiden
                    ),
                );
            }
            for &gid in &state.ghouls {
                if let Some(g) = sim.get_entity(gid) {
                    let g_pos = g.pos();
                    force_leap(sim, gid, g_pos, target_pos, LEAP_SPEED);
                    let mut frenzy = BuffV1::timed(BUFF_GHOUL_FRENZY, 120);
                    frenzy.attack_speed_mult = 15;
                    frenzy.move_speed_mult = 15;
                    sim.add_buff(gid, &frenzy);
                }
            }
            if let Some(mid) = state.maiden {
                if let Some(m) = sim.get_entity(mid) {
                    let m_pos = m.pos();
                    if dist(m_pos, target_pos) > 48_000.0 {
                        force_leap(sim, mid, m_pos, target_pos, LEAP_SPEED * 0.7);
                    }
                }
            }
        }

        // Eulogy of the Isles Soul Siphon: while Maiden is alive and enemy is marked,
        // deal 3% target Max HP bonus magic damage and heal Yorick every 1.5s (90 ticks).
        if state.maiden.is_some() && tick.saturating_sub(state.last_siphon_tick) >= 90 {
            state.last_siphon_tick = tick;
            let pct_dmg = ((target_max_hp * 3) / 100).clamp(18, 160);
            let _ = sim.deal_damage_typed(
                yorick_id,
                target_id,
                pct_dmg,
                DamageTypeV1::Ap,
                AttackTypeV1::Skill,
            );
            let heal_amt = (pct_dmg / 2).max(12);
            sim.heal(yorick_id, yorick_id, heal_amt);
        }
    } else if tick % 30 == 0 {
        // 6. Formation Tether: keep Mist Walkers and Maiden marching with Yorick
        for (slot, &gid) in state.ghouls.iter().enumerate() {
            if let Some(g) = sim.get_entity(gid) {
                let g_pos = g.pos();
                if dist(g_pos, y_pos) > TETHER_MAX_RANGE {
                    let angle = (slot as f64) * std::f64::consts::FRAC_PI_2;
                    let tx = (y_pos.0 as f64 + angle.cos() * 22_000.0).max(10_000.0) as u64;
                    let ty = (y_pos.1 as f64 + angle.sin() * 22_000.0).max(10_000.0) as u64;
                    force_leap(sim, gid, g_pos, (tx, ty), 2_400.0);
                }
            }
        }
        if let Some(mid) = state.maiden {
            if let Some(m) = sim.get_entity(mid) {
                let m_pos = m.pos();
                if dist(m_pos, y_pos) > TETHER_MAX_RANGE * 1.15 {
                    let tx = y_pos.0.saturating_sub(28_000).max(10_000);
                    let ty = y_pos.1.saturating_sub(14_000).max(10_000);
                    force_leap(sim, mid, m_pos, (tx, ty), 2_200.0);
                }
            }
        }
    }

    if let Ok(mut map) = MATCH_STATES.lock() {
        map.insert(key, state);
    }
}

/// Find all Yorick champions (including dead ones so view::note_role keeps their Champion role) in `sim`.
fn find_yoricks(sim: &StableSim<'_>) -> Vec<usize> {
    let mut ids = Vec::new();
    let champ_count = sim.champion_count();
    if champ_count > 0 {
        for i in 0..champ_count {
            let id = sim.champion_id_at(i);
            let Some(e) = sim.get_entity(id) else {
                continue;
            };
            if e.is_champion()
                && e.name().as_deref() == Some(HERO_ID)
                && !is_summon_entity(&e)
            {
                ids.push(id);
            }
        }
    } else {
        let count = sim.entity_count();
        for i in 0..count {
            let Some(e) = sim.entity_at(i) else {
                continue;
            };
            if e.is_champion()
                && e.name().as_deref() == Some(HERO_ID)
                && !is_summon_entity(&e)
            {
                ids.push(e.id());
            }
        }
    }
    ids
}

#[derive(Clone, Default)]
struct YorickMatchHook;

impl StableMatchHook for YorickMatchHook {
    fn on_match_start(&self, _sim: &mut StableSim<'_>) {}

    fn on_match_tick(&self, sim: &mut StableSim<'_>, _rng_seed: u64) {
        for yid in find_yoricks(sim) {
            tick_yorick_summons(sim, yid);
        }
    }
}

#[derive(Clone, Default)]
struct ShepherdPassive;

impl StablePassive for ShepherdPassive {
    fn clone_box(&self) -> Box<dyn StablePassive> {
        Box::new(self.clone())
    }

    fn on_update(
        &mut self,
        sim: &mut StableSim<'_>,
        _rng_seed: u64,
        _player_id: usize,
        entity_id: usize,
    ) {
        tick_yorick_summons(sim, entity_id);
    }
}

#[derive(Clone, Default)]
struct RaiseGhoulEffect;

impl StableEffectType for RaiseGhoulEffect {
    fn apply(
        &self,
        sim: &mut StableSim<'_>,
        _rng_seed: u64,
        caster_id: usize,
        _input: InputTargetV1,
    ) {
        sim.add_buff(caster_id, &BuffV1::timed(BUFF_Q_CAST, 15));
        tick_yorick_summons(sim, caster_id);
    }
}

#[derive(Clone, Default)]
struct SummonMaidenEffect;

impl StableEffectType for SummonMaidenEffect {
    fn apply(
        &self,
        sim: &mut StableSim<'_>,
        _rng_seed: u64,
        caster_id: usize,
        _input: InputTargetV1,
    ) {
        sim.add_buff(caster_id, &BuffV1::timed(BUFF_R_CAST, 15));
        tick_yorick_summons(sim, caster_id);
    }
}

fn init(host: &StableHost) -> StableMod {
    let _ = std::fs::rename(&*LOG_PATH, LOG_PATH.with_extension("prev.log"));
    let v = host.game_version();
    wlog(&format!(
        "=== {MOD_ID} v0.4.2 native addon loaded: game {}.{}.{} abi={} log={} ===",
        v.major,
        v.minor,
        v.patch,
        host.abi_level(),
        LOG_PATH.display()
    ));

    let mut module = StableMod::new(MOD_ID);
    module.set_match_hook(YorickMatchHook);
    module.add_native_passive(
        format!("{MOD_ID}:shepherd_of_souls"),
        ShepherdPassive,
    );
    module.add_native_effect(format!("{MOD_ID}:raise_ghoul"), RaiseGhoulEffect);
    module.add_native_effect(
        format!("{MOD_ID}:summon_maiden"),
        SummonMaidenEffect,
    );
    view::install_once();

    host.log(
        LogLevel::Info,
        "league_yorick v0.4.2 native addon loaded (Mist Walker Ghouls & Maiden of the Mist active).",
    );
    module
}

declare_stable_mod!(init);
