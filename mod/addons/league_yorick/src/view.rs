//! Display-world animation hook for Yorick's summoned Mist Walker Ghouls and
//! The Maiden of the Mist on Teamfight Manager 2 0.6.3 (Windows).

use std::sync::Mutex;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Role {
    Champion,
    Ghoul,
    Maiden,
}

#[derive(Clone, Copy, Debug)]
struct RoleEntry {
    is_view: bool,
    match_id: u64,
    set_index: u64,
    key: u64,
    role: Role,
}

static ROLES: Mutex<Vec<RoleEntry>> = Mutex::new(Vec::new());
static LATEST_VIEW: Mutex<Option<(u64, u64)>> = Mutex::new(None);

/// Record the latest on-screen match `(match_id, set_index)`.
pub fn note_view(match_id: u64, set_index: u64) {
    if let Ok(mut l) = LATEST_VIEW.lock() {
        *l = Some((match_id, set_index));
    }
}

/// Record whether `key` (`entity_id`) is Yorick the Champion, a Mist Walker Ghoul,
/// or The Maiden of the Mist. Moves updated entries to the back (MRU) and never lets
/// background presim entries evict active client-view entries.
pub fn note_role(is_view: bool, match_id: u64, set_index: u64, key: u64, role: Role) {
    if let Ok(mut list) = ROLES.lock() {
        list.retain(|e| {
            !(e.is_view == is_view
                && e.match_id == match_id
                && e.set_index == set_index
                && e.key == key)
        });
        list.push(RoleEntry {
            is_view,
            match_id,
            set_index,
            key,
            role,
        });
        if list.len() > 1024 {
            // Evict non-view (presim) entries first so live match roles are never lost
            if let Some(pos) = list.iter().position(|e| !e.is_view) {
                list.remove(pos);
            } else {
                list.remove(0);
            }
        }
    }
}

/// Map a base animation tag (`idle`, `run`, `attack`, `hit`, `dead`, ...) to the
/// summon's prefixed animation tag (`ghoul_*` or `maiden_*`).
pub fn summon_anim_for(cur: &str, role: Role) -> Option<String> {
    let prefix = match role {
        Role::Champion => return None,
        Role::Ghoul => "ghoul_",
        Role::Maiden => "maiden_",
    };
    let base = cur
        .strip_prefix("ghoul_")
        .or_else(|| cur.strip_prefix("maiden_"))
        .unwrap_or(cur);
    let mapped_base = match base {
        "idle" => "idle",
        "run" => "run",
        "attack" | "skill" | "skill2" | "ult" => "attack",
        "hit" => "hit",
        "dead" => "dead",
        _ => "idle",
    };
    let want = format!("{prefix}{mapped_base}");
    (want != cur).then_some(want)
}

#[cfg(target_os = "windows")]
mod imp {
    use std::ffi::c_void;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Mutex, Once};

    use super::super::wlog;
    use super::{summon_anim_for, Role, LATEST_VIEW, ROLES};

    const FN_RVA: usize = 0x2808e30;
    const SLOT_RVA: usize = 0x3c31358;
    const FN_HEAD: [u8; 24] = [
        0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x56, 0x57, 0x55, 0x53, 0x48, 0x81, 0xec,
        0x88, 0x00, 0x00, 0x00, 0x66, 0x0f, 0x7f, 0x7c, 0x24,
    ];
    const TABLE: usize = 0x138;
    const TABLE_MASK: usize = 0x140;
    const TABLE_ITEMS: usize = 0x150;
    const ELEM: usize = 0x1d0;
    const VALUE: usize = 8;
    const NAME_PTR: usize = 0x40;
    const NAME_LEN_AT: usize = 0x48;
    const ANIM_CAP: usize = 0x68;
    const ANIM_PTR: usize = 0x70;
    const ANIM_LEN: usize = 0x78;
    const BUFFS_PTR: usize = 0xb8;
    const BUFFS_LEN: usize = 0xc0;
    const BUFF_SIZE: usize = 0x28;

    const NAME: &[u8] = b"league_yorick";
    const BUFF_GHOUL: &[u8] = b"league_yorick_unit_ghoul";
    const BUFF_MAIDEN: &[u8] = b"league_yorick_unit_maiden";

    #[derive(Default)]
    struct SeenRoles {
        seen: Vec<(usize, u64, Role)>,
    }

    impl SeenRoles {
        fn resolve(&mut self, world: usize, key: u64, detected: Option<Role>) -> Role {
            if let Some(r) = detected {
                if let Some(entry) = self.seen.iter_mut().find(|e| e.0 == world && e.1 == key) {
                    entry.2 = r;
                } else {
                    self.seen.push((world, key, r));
                    if self.seen.len() > 256 {
                        self.seen.remove(0);
                    }
                }
                return r;
            }
            if let Some(entry) = self.seen.iter().find(|e| e.0 == world && e.1 == key) {
                return entry.2;
            }
            // Never default an unknown entity key to Role::Ghoul: Yorick himself can have
            // entity ID >= 12 (e.g. #20 when towers/structures occupy 0..19).
            Role::Champion
        }
    }

    struct Stats {
        calls: u64,
        entities: u64,
        writes: u64,
        last: Option<std::time::Instant>,
    }

    static SEEN: Mutex<SeenRoles> = Mutex::new(SeenRoles { seen: Vec::new() });
    static STATS: Mutex<Stats> = Mutex::new(Stats {
        calls: 0,
        entities: 0,
        writes: 0,
        last: None,
    });
    static PREV: AtomicUsize = AtomicUsize::new(0);
    static HOOK: Once = Once::new();

    #[link(name = "kernel32")]
    extern "system" {
        fn ReadProcessMemory(
            h: isize,
            base: *const c_void,
            buf: *mut c_void,
            size: usize,
            read: *mut usize,
        ) -> i32;
        fn GetCurrentProcess() -> isize;
        fn GetModuleHandleW(name: *const u16) -> usize;
        fn VirtualProtect(addr: *mut c_void, size: usize, new: u32, old: *mut u32) -> i32;
        fn GetProcessHeap() -> isize;
        fn HeapAlloc(heap: isize, flags: u32, bytes: usize) -> *mut u8;
        fn HeapFree(heap: isize, flags: u32, mem: *mut u8) -> i32;
    }

    fn read(addr: usize, len: usize) -> Option<Vec<u8>> {
        let mut buf = vec![0u8; len];
        let mut got = 0usize;
        let ok = unsafe {
            ReadProcessMemory(
                GetCurrentProcess(),
                addr as *const c_void,
                buf.as_mut_ptr() as *mut c_void,
                len,
                &mut got,
            )
        };
        (ok != 0 && got == len).then_some(buf)
    }

    type Update = unsafe extern "system" fn(usize, usize, usize, f32);

    unsafe extern "system" fn update(this: usize, a2: usize, world: usize, dt: f32) {
        let prev = PREV.load(Ordering::Acquire);
        if prev != 0 {
            let f: Update = std::mem::transmute(prev);
            f(this, a2, world, dt);
        }
        let _ = std::panic::catch_unwind(|| unsafe { dress_yorick_summons(world) });
    }

    unsafe fn q(addr: usize) -> usize {
        std::ptr::read_unaligned(addr as *const usize)
    }

    fn plausible(p: usize) -> bool {
        (0x10000..0x7fff_ffff_ffff).contains(&p)
    }

    fn lookup_role_by_key(key: u64, buffs: &[&[u8]]) -> Option<Role> {
        for &b in buffs {
            if b == BUFF_MAIDEN {
                return Some(Role::Maiden);
            }
            if b == BUFF_GHOUL {
                return Some(Role::Ghoul);
            }
        }
        let view = LATEST_VIEW.lock().ok().and_then(|l| *l);
        let roles = ROLES.lock().ok()?;
        if let Some((m, s)) = view {
            if let Some(entry) = roles
                .iter()
                .rev()
                .find(|e| e.is_view && e.match_id == m && e.set_index == s && e.key == key)
            {
                return Some(entry.role);
            }
        }
        if let Some(entry) = roles.iter().rev().find(|e| e.is_view && e.key == key) {
            return Some(entry.role);
        }
        roles.iter().rev().find(|e| e.key == key).map(|e| e.role)
    }

    unsafe fn dress_yorick_summons(world: usize) {
        if !plausible(world) {
            return;
        }
        let ctrl = q(world + TABLE);
        let mask = q(world + TABLE_MASK);
        let items = q(world + TABLE_ITEMS);
        if items == 0
            || !plausible(ctrl)
            || mask >= 1 << 16
            || (mask + 1) & mask != 0
            || items > mask + 1
        {
            return;
        }
        let Ok(mut seen) = SEEN.try_lock() else { return };
        let Ok(mut st) = STATS.try_lock() else { return };
        st.calls += 1;
        let now = std::time::Instant::now();
        if st.last.is_none_or(|t| now.duration_since(t).as_secs_f64() >= 10.0) {
            if st.last.is_some() && st.entities > 0 {
                wlog(&format!(
                    "[view] display handler: {} calls, Yorick/summon entities seen {}, {} anim swaps in 10s",
                    st.calls, st.entities, st.writes
                ));
            }
            st.last = Some(now);
            st.calls = 0;
            st.entities = 0;
            st.writes = 0;
        }

        for i in 0..=mask {
            if *(ctrl as *const u8).add(i) & 0x80 != 0 {
                continue;
            }
            let elem = ctrl - (i + 1) * ELEM;
            let v = elem + VALUE;
            if q(v + NAME_LEN_AT) != NAME.len() {
                continue;
            }
            let ptr = q(v + NAME_PTR);
            if !plausible(ptr)
                || std::slice::from_raw_parts(ptr as *const u8, NAME.len()) != NAME
            {
                continue;
            }
            let (bp, bn) = (q(v + BUFFS_PTR), q(v + BUFFS_LEN));
            let buffs: Vec<&[u8]> = if plausible(bp) && bn <= 256 {
                (0..bn)
                    .filter_map(|k| {
                        let b = bp + k * BUFF_SIZE;
                        let (p, n) = (q(b + 8), q(b + 0x10));
                        (plausible(p) && n <= 64)
                            .then(|| std::slice::from_raw_parts(p as *const u8, n))
                    })
                    .collect()
            } else {
                Vec::new()
            };
            st.entities += 1;
            let key = q(elem) as u64;
            let detected = lookup_role_by_key(key, &buffs);
            let role = seen.resolve(world, key, detected);
            if role == Role::Champion {
                continue;
            }
            let (acap, aptr, alen) = (q(v + ANIM_CAP), q(v + ANIM_PTR), q(v + ANIM_LEN));
            if !(1..=32).contains(&alen) || acap < alen || !plausible(aptr) {
                continue;
            }
            let Ok(cur) = std::str::from_utf8(std::slice::from_raw_parts(aptr as *const u8, alen))
            else {
                continue;
            };
            let Some(want) = summon_anim_for(cur, role) else {
                continue;
            };
            let heap = GetProcessHeap();
            let fresh = HeapAlloc(heap, 0, want.len());
            if fresh.is_null() {
                continue;
            }
            std::ptr::copy_nonoverlapping(want.as_ptr(), fresh, want.len());
            std::ptr::write_unaligned((v + ANIM_CAP) as *mut usize, want.len());
            std::ptr::write_unaligned((v + ANIM_PTR) as *mut usize, fresh as usize);
            std::ptr::write_unaligned((v + ANIM_LEN) as *mut usize, want.len());
            if acap > 0 {
                HeapFree(heap, 0, aptr as *mut u8);
            }
            st.writes += 1;
        }
    }

    fn install() -> Result<(usize, usize), String> {
        let module = unsafe { GetModuleHandleW(std::ptr::null()) };
        if module == 0 {
            return Err("no module handle".into());
        }
        let func = module + FN_RVA;
        if read(func, FN_HEAD.len()).as_deref() != Some(&FN_HEAD[..]) {
            return Err("not game 0.6.3 (display handler bytes differ)".into());
        }
        let slot = module + SLOT_RVA;
        let cur = read(slot, 8)
            .map(|b| usize::from_le_bytes(b.try_into().unwrap()))
            .ok_or("display slot is unreadable")?;
        if !plausible(cur) {
            return Err(format!("display slot holds {cur:#x}"));
        }
        PREV.store(cur, Ordering::Release);
        let mut old = 0u32;
        if unsafe { VirtualProtect(slot as *mut c_void, 8, 0x04, &mut old) } == 0 {
            return Err("VirtualProtect failed".into());
        }
        unsafe { std::ptr::write_volatile(slot as *mut usize, update as Update as usize) };
        let mut tmp = 0u32;
        unsafe { VirtualProtect(slot as *mut c_void, 8, old, &mut tmp) };
        Ok((slot, cur))
    }

    pub fn install_once() {
        HOOK.call_once(|| {
            let module = unsafe { GetModuleHandleW(std::ptr::null()) };
            match install() {
                Ok((slot, prev)) => wlog(&format!(
                    "summon display hook active: slot {slot:#x} now calls league_yorick, then {} ({prev:#x})",
                    if prev == module + FN_RVA {
                        "the game's own handler"
                    } else {
                        "another mod's handler (chained)"
                    }
                )),
                Err(e) => wlog(&format!("summon display hook inactive ({e})")),
            }
        });
    }
}

#[cfg(not(target_os = "windows"))]
mod imp {
    pub fn install_once() {}
}

pub use imp::install_once;
