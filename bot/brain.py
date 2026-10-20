"""AI partner brain: pure logic, no game access. Input: world state. Output: controller command.

States:  FOLLOW   stay 3-8 m behind Player 1 (walk)
         CATCHUP  more than 15 m away -> sprint toward Player 1
         UNSTUCK  trying to move but position barely changes -> jump / side-step
         REVIVE   Player 1 is downed -> run to them and hold interact
         IDLE     close enough, nothing to do (also used while paused)
Units: game units (~cm). Angles: radians, yaw measured in the world X/Y plane.
"""
import math
from collections import deque
from dataclasses import dataclass, field, replace
import time


@dataclass
class World:
    p1: tuple                 # Player 1 position (x, y, z)
    p2: tuple                 # Player 2 (bot) position
    cam_yaw: float            # Player 2 camera yaw (direction the camera looks, radians)
    p1_downed: bool = False
    p1_jumps: tuple = ()      # co-op: spots where P1 jumped while moving (x, y, z, t) -> jump at the same place
    enemy: tuple = None       # nearest live hostile NPC position (x, y, z) or None
    enemy_human: bool = False # aim higher at humans (chest) than at animals (body)
    enemy_hp: int = None      # target's current HP (to notice shots that don't land)
    enemy_vel: tuple = None   # target velocity (cm/s, x, y) from memory -> aim ahead of moving animals
    crosshair_red: bool = None  # from the screen: game crosshair is red = on a target (None = unknown)
    line_blocked: bool = None # from the screen: foliage / trunk right in front of the crosshair
    open_side: float = None   # from the screen: +1 = right side looks more open, -1 = left
    enemy_height: float = None  # aim this high above the target's feet (per animal type), cm
    hungry: bool = False      # from the screen: hunger icon is not green
    food_screen: tuple = None # from the screen: berry cluster at (x, y), 0..1 of the screen width/height
    danger: tuple = ()        # positions of live humans nearby (the path planner keeps away from them)
    collect: tuple = None     # nearest collectable (type 9169: box / wood / ...) position, chosen by run_bot
    arrows: int = None        # estimated arrows left (run_bot counts shots; None = unknown)
    shots_since_craft: int = 0 # arrows fired since the last craft hold
    under_attack: bool = False      # Lara is being hurt right now: fight the attacker, drop everything else
    need_resources: bool = False # out of arrows + crafting failed: collect wood/feathers before hunting again
    traps: tuple = ()         # known trap parts nearby (crypt) -> careful mode: walk only on P1's footsteps
    npcs: tuple = ()          # positions of animals/humans nearby (their walking paints the walk map)
    cam_pitch: float = None   # camera pitch (radians, + = up) if known from memory
    cam_pos: tuple = None     # camera position (x, y, z) from memory: aim FROM here (over-the-shoulder offset)
    loot: tuple = None        # hunt mode: where the last kill lies (walk there, hold X)
    t: float = 0.0            # time in seconds


@dataclass
class Command:
    lx: float = 0.0           # left stick -1..1 (right = +)
    ly: float = 0.0           # left stick -1..1 (forward/away from camera = +)
    sprint: bool = False      # hold sprint (L3 on Xbox layout used by ROTTR)
    jump: bool = False        # tap A
    interact: bool = False    # hold X (revive / interact)
    drop: bool = False        # tap B (let go of a ledge / drop down)
    mash: bool = False        # mash X fast (~16/s, done by the pad thread, independent of the bot's tick rate)
    rx: float = 0.0           # right stick (camera): + = turn right
    ry: float = 0.0
    aim: bool = False         # hold LT
    fire: bool = False        # hold RT (bow: hold = draw, release = shoot; guns fire on press)
    state: str = "IDLE"
    info: str = ""


def stick_towards(p_from, p_to, cam_yaw):
    """World direction -> stick values relative to the camera.
    The stick's 'up' = the direction the camera looks (cam_yaw); 'right' = 90 deg clockwise from that."""
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    want = math.atan2(dy, dx)                 # world angle toward the target
    rel = want - cam_yaw                      # angle relative to camera forward
    # forward component = cos(rel), right component = -sin(rel) (counter-clockwise positive world angles)
    return -math.sin(rel), math.cos(rel)


def dist2d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


@dataclass
class Brain:
    follow_min: float = 300.0     # 3 m: closer than this -> stand still
    follow_max: float = 800.0     # 8 m: walk to keep within this
    catchup: float = 1500.0       # 15 m: sprint
    revive_range: float = 120.0   # 1.2 m: close enough to revive
    stuck_time: float = 1.2       # seconds without progress while moving -> stuck
    _last_pos: tuple = None
    _last_progress_t: float = 0.0
    _unstuck_until: float = 0.0
    _unstuck_side: float = 1.0
    _unstuck_kind: str = ""       # CLIMB / DROP / SIDE
    _attempts: int = 0            # failed unstuck attempts on the current crumb
    _attempt_goal: tuple = None
    _unstuck_z: float = 0.0
    _unstuck_start: float = 0.0
    climb_dz: float = 80.0        # next crumb this much higher -> try to climb (jump forward)
    give_up: int = 6              # after this many failed attempts skip ahead on the trail
    crumb_spacing: float = 100.0  # drop a breadcrumb every 1 m P1 walks
    crumb_reached: float = 120.0  # bot within 1.2 m of a crumb -> go to the next one
    teleport: float = 1000.0      # P1 jumped >10 m in one tick (respawn/fast travel) -> forget the trail
    slot_back: float = 180.0      # follow spot: 1.8 m behind P1 ... (user: stand close, e.g. both in the
    slot_side: float = 90.0       # ... and 0.9 m to P1's right     extraction circle; was 3.5 m / 2 m)
    slot_start: float = 170.0     # start walking to the slot when this far from it
    slot_stop: float = 70.0       # ... and stop when this close
    personal_space: float = 70.0  # closer than 0.7 m to P1 -> step away to the slot
    coop: bool = False            # co-op partner: hunt / collect only near P1, follow first
    coop_radius: float = 1000.0   # co-op: pickups / loot only within 10 m of P1, and stay within 10 m
    _to_slot: bool = False
    _p1_prev: tuple = None
    _p1_heading: float = None
    engage: float = 2000.0        # shoot enemies within 20 m of the bot
    leash: float = 2500.0         # ... but only while P1 is within 25 m (otherwise keep following)
    aim_ok: float = math.radians(2.5)  # fire when the camera points this close to the enemy
    stick_dz: float = 0.45        # game ignores right stick below ~this -> always push at least this much
    turn_gain: float = 6.0        # right stick per radian of aim error (2.5 was slow: 0.75-2 s to get on target)
    turn_damp: float = 0.12       # brake: stick per (rad/s) of error change, stops overshoot at the higher gain
    preturn: float = math.radians(12)  # further off than this: turn WITHOUT LT first (camera 180 deg/s vs 78)
    arrow_speed: float = 4500.0   # cm/s, for leading moving targets (tune from clips)
    max_lead: float = 500.0       # never lead more than 5 m
    predraw: float = math.radians(6)   # start drawing when this close to on-target (release waits for red)
    min_draw: float = 0.6         # full draw before a release (0.25 s: clips showed Lara still nocking -> weak misses)
    nock_time: float = 0.7        # after a shot: let the nock animation finish before drawing again
    _draw_since: float = None
    _release_until: float = 0.0
    point_blank: float = 600.0    # animal closer than 6 m: shoot without waiting for a red crosshair (defence)
    draw_time: float = 1.6        # no screen info: hold RT this long, then release (also crafts when empty)
    red_shot: float = 0.3         # screen says crosshair is red: quick 0.3 s shot (0.5 s: deer moved its head, missed)
    _red_until: float = 0.0
    side_walk: float = 3.0        # pickup: max time to walk round to a side (else skip that side)
    side_mash: float = 4.0        # mash X this long at a side before trying the next one
    first_side_mash: float = 6.0  # ... and this long at the side we walked up to (usually the right one)
    _mash_t: float = None
    side_dist: float = 110.0      # stand this far from the pickup's centre
    _mash: bool = False
    _climbing: bool = False
    _ground_try: tuple = None
    _climb_start: tuple = None
    _noclimb_for: tuple = None
    _side_k: int = None
    _side_t: float = None
    _side_base: float = 0.0
    craft_at: int = 2             # estimated arrows <= this and no animal within 30 m -> craft (hold RT)
    craft_hold: float = 2.0
    _craft_until: float = 0.0
    craft_done: bool = False      # set when a craft hold finished; run_bot adds arrows and clears it
    no_red_approach: float = 4.0  # in combat this long without a red crosshair -> stop aiming, walk a path closer
    no_red_give_up: float = 25.0  # ... this long in total without a red crosshair -> give up this target
    _no_red_since: float = None
    _prev_err: tuple = None
    _approach_until: float = 0.0
    give_up: bool = False         # set when the target can't be seen; run_bot bans it and picks another
    _blocked_since: float = None
    _blocked_tries: int = 0
    _closer_until: float = 0.0
    release_time: float = 0.35
    hunt: bool = False            # hunt mode: go after animals on its own (ignores P1 / home)
    mode: str = ""                # "follow" | "hunt" | "collect" | "auto" | "guard" (F1-F5); "" = from `hunt`
    hunt_first: float = 2500.0    # not hungry + animal within 25 m: hunt it before optional pickups
    craft_every: int = 5          # craft (hold RT) after this many shots since the last craft (user)
    stalk_slow: float = 3500.0    # animals closer than 35 m: walk slowly (half stick, quieter)
    stalk_speed: float = 0.5
    spooked_speed: float = 150.0  # animal moving away faster than 1.5 m/s = spooked
    spooked_wait: float = 6.0     # ... freeze up to this long, then carry on
    _spooked_since: float = None
    _target_lock: object = None
    hunt_shoot: float = 2500.0    # animals: shoot from 25 m (deer bolt when Lara gets closer)
    wander: tuple = None          # hunt mode: walk here when no animal is in range
    nav: object = None            # navmap.NavMap: learned walk map + A* (None = steer straight at goals)
    _path: list = field(default_factory=list)
    _path_goal: tuple = None
    _path_t: float = -99.0
    _replan: bool = False
    _probe: tuple = None          # contact sensing window: (t0, pos0, heading, expected_cm)
    _free_speed: float = 300.0    # learned walking speed when nothing is in the way (cm/s)
    _move_heading: float = None   # world direction the last stick command pushed toward
    _move_mag: float = 0.0
    _replans_failed: int = 0
    _jump_spots: list = field(default_factory=list)
    _jumps_seen: set = field(default_factory=set)
    _last_copy_jump: float = 0.0
    max_replans: int = 5          # stuck -> block the cell ahead + re-plan this many times before escape moves
    avoid_r: float = 300.0        # early sidestep: NPC within 3 m ...
    avoid_cone: float = math.radians(45)   # ... and within +-45 deg of where we walk (game: FUN_14053efd0)
    loot_hold: float = 3.0        # hold X this long at a body (skinning)
    _looted: list = field(default_factory=list)
    _loot_since: float = None
    pitch_gain: float = 2.5       # right stick Y per radian of pitch error (+ = look up)
    eye_height: float = 150.0     # camera ~1.5 m above Lara's feet
    target_height: float = 110.0  # humans: aim at the chest (~1.1 m above the feet)
    animal_height: float = 45.0   # animals (boar, deer, wolf): body centre ~0.45 m up
    keep_drawing: float = math.radians(6)   # once drawing, keep drawing while within this
    miss_limit: int = 3           # this many released shots without HP change -> reposition
    reposition_time: float = 1.6  # strafe sideways this long to get a clear line past trees
    _shots: int = 0
    _hp_at_first_shot: int = None
    _was_drawing: bool = False
    _repos_until: float = 0.0
    _repos_side: float = 1.0
    _aim_since: float = None
    _fire_phase_t: float = 0.0
    state: str = "IDLE"
    history: list = field(default_factory=list)
    trail: deque = field(default_factory=lambda: deque(maxlen=400))

    def _update_trail(self, p1):
        if self.trail and dist2d(self.trail[-1], p1) > self.teleport:
            self.trail.clear()
        if not self.trail or dist2d(self.trail[-1], p1) >= self.crumb_spacing:
            self.trail.append(p1)

    def _target(self, p2, p1):
        """Walk along P1's path instead of straight at P1 (P1's path goes around walls/cliffs).
        Skip to the crumb nearest the bot, then aim at the first crumb not yet reached."""
        if not self.trail:
            return p1
        # nearest crumb, height difference beyond 3 m counts double: in a crypt the deep crumbs lie right under the entrance
        # in 2D -> skipping to them made the bot walk out of the cave (live 2026-10-20)
        best = min(range(len(self.trail)), key=lambda i: math.hypot(dist2d(self.trail[i], p2),
                                                                    2 * max(0.0, abs(self.trail[i][2] - p2[2]) - 300)))
        for _ in range(best):
            self.trail.popleft()
        while self.trail and dist2d(self.trail[0], p2) < self.crumb_reached and                 abs(self.trail[0][2] - p2[2]) < 150:     # a crumb on another level is not reached
            self.trail.popleft()
        return self.trail[0] if self.trail else p1

    def _coop_leash(self, w, d):
        """co-op: P1 comes first. Pickups / loot only near P1, enemies only near P1 (or attacking us).
        None = nothing worth doing here -> follow P1."""
        near_p1 = lambda q: q is not None and dist2d(q, w.p1) < self.coop_radius
        collect = w.collect if near_p1(w.collect) else None
        loot = w.loot if near_p1(w.loot) else None
        enemy = w.enemy if (w.enemy is not None and (w.under_attack or dist2d(w.enemy, w.p1) < self.leash
                                                     or dist2d(w.enemy, w.p2) < self.point_blank)) else None
        if w.under_attack and w.enemy is not None:
            return w
        if d > self.coop_radius and not (enemy is not None and dist2d(enemy, w.p2) < self.point_blank):
            return None                              # too far from P1: catch up first
        if collect is None and loot is None and enemy is None:
            return None
        self.wander = None
        return replace(w, collect=collect, loot=loot, enemy=enemy, food_screen=None)

    def _careful(self, w):
        return any(dist2d(t, w.p2) < 1500 and abs(t[2] - w.p2[2]) < 600 for t in w.traps)

    def _start_unstuck(self, w, goal):
        if self.nav is not None:
            ahead = self._path[0] if self._path else goal
            heading = math.atan2(ahead[1] - w.p2[1], ahead[0] - w.p2[0])
            self.nav.blocked_ahead(w.p2, heading)
            self._replan = True
        if self._attempt_goal is None or dist2d(self._attempt_goal, goal) > 50:
            self._attempt_goal, self._attempts = goal, 0
        self._attempts += 1
        if self._attempts > self.give_up and len(self.trail) > 3:
            for _ in range(3):                      # this crumb is unreachable: skip ahead on P1's path
                self.trail.popleft()
            self._attempt_goal, self._attempts = None, 0
        if self._careful(w):
            if self.trail:
                self.trail.popleft()               # next footstep of P1 instead of stepping sideways
            self._unstuck_kind, self._unstuck_z = "WAIT", w.p2[2]
            self._unstuck_start, self._unstuck_until = w.t, w.t + 0.5
            self._last_progress_t = w.t
            return
        dz = goal[2] - w.p2[2]
        n = self._attempts
        if w.collect is not None and dist2d(w.collect, w.p2) < 300:
            dz, n = 0.0, max(n, 2)                  # next to a pickup: never jump (climbs crates, grapples trees)
        if dz > self.climb_dz and n <= 2:
            kind, dur = "CLIMB", 1.0                # higher: walk into it + jump = grab and climb
        elif dz < -self.climb_dz and n <= 2:
            kind, dur = "DROP", 0.8                 # lower: let go / drop, never jump (jump re-grabs the ledge)
        else:                                       # same level: never jump (NPCs only jump on real links;
                                                    # live: most jumps were blind vaults at trucks / walls)
            kind, dur = "SIDE", 0.6 + 0.3 * n       # longer side-steps each try
            # screen says which side is more open: go there (else alternate)
            self._unstuck_side = w.open_side if w.open_side else -self._unstuck_side
        self._unstuck_kind, self._unstuck_z = kind, w.p2[2]
        self._unstuck_start, self._unstuck_until = w.t, w.t + dur
        self._last_progress_t = w.t

    def _unstuck_cmd(self, w, goal, d):
        lx, ly = stick_towards(w.p2, goal, w.cam_yaw)
        early = w.t - self._unstuck_start < 0.15
        info = f"{self._unstuck_kind} try {self._attempts} d={d / 100:.1f} m"
        self.state = "UNSTUCK"
        if self._unstuck_kind == "CLIMB":
            return Command(lx=lx, ly=ly, jump=early or (w.t - self._unstuck_start) % 0.5 < 0.05,
                           state="UNSTUCK", info=info)
        if self._unstuck_kind == "WAIT":
            return Command(state="UNSTUCK", info=info + " (traps: no side-steps)")
        if self._unstuck_kind == "DROP":
            return Command(lx=lx, ly=ly, drop=early, state="UNSTUCK", info=info)
        # SIDE: no jump; jump only if the crumb is higher (then climbing is wanted)
        jump = early and goal[2] - w.p2[2] > self.climb_dz
        return Command(lx=ly * self._unstuck_side, ly=-lx * self._unstuck_side, jump=jump,
                       state="UNSTUCK", info=info)

    def step(self, w: World) -> Command:
        cmd = self._step(w)
        cmd = self._sidestep(w, cmd)
        cmd = self._copy_jumps(w, cmd)
        if cmd.lx or cmd.ly:                        # remember where this command pushes Lara (world angle)
            self._move_heading = w.cam_yaw + math.atan2(-cmd.lx, cmd.ly)
            self._move_mag = min(1.0, math.hypot(cmd.lx, cmd.ly)) if not cmd.sprint else 0.0   # sprint: no
                                                    # contact sensing (would inflate the learned walking speed)
        else:
            self._move_heading, self._move_mag = None, 0.0
        return cmd

    def _copy_jumps(self, w, cmd):
        """imitation: P1 jumped here (gap, spike pit, ledge) -> when the bot reaches the same spot on P1's path,
        moving the same way, it jumps too. Each spot is used once and forgotten after 3 min."""
        for j in w.p1_jumps:
            if j[3] not in self._jumps_seen:
                self._jumps_seen.add(j[3])
                self._jump_spots.append(j)
        now = w.t
        self._jump_spots = [j for j in self._jump_spots if abs(j[2] - w.p2[2]) < 400 and
                            (time.time() - j[3] < 180 if j[3] > 1e9 else True)]
        if not (cmd.lx or cmd.ly) or self.state not in ("FOLLOW", "CATCHUP"):
            return cmd
        for j in list(self._jump_spots):
            if dist2d(j, w.p2) < 90:
                cmd.jump = True
                cmd.info += " | jump (P1 jumped here)"
                self._jump_spots.remove(j)
                self._last_copy_jump = now
                break
        return cmd

    def _sidestep(self, w, cmd):
        """game rule for NPC crowds: another body within 3 m inside +-45 deg ahead -> steer away BEFORE contact
        (the nearer and the more head-on, the harder). The target we walk to is not an obstacle."""
        if not (cmd.lx or cmd.ly) or cmd.aim or cmd.mash or self.state in ("UNSTUCK", "PICKUP", "COMBAT", "DANGER"):
            return cmd
        h = w.cam_yaw + math.atan2(-cmd.lx, cmd.ly)
        goals = [g for g in (w.enemy, w.collect, w.loot) if g is not None]
        turn = 0.0
        for q in w.npcs:
            dq = dist2d(q, w.p2)
            if dq > self.avoid_r or dq < 1.0 or abs(q[2] - w.p2[2]) > 200 or any(dist2d(q, g) < 100 for g in goals):
                continue
            a = math.atan2(q[1] - w.p2[1], q[0] - w.p2[0]) - h
            a = math.atan2(math.sin(a), math.cos(a))
            if abs(a) > self.avoid_cone:
                continue
            push = (self.avoid_cone - abs(a)) * (1.0 - dq / self.avoid_r) * 2.0
            turn += -push if a >= 0 else push          # body on the left -> turn right, and vice versa
        if not turn:
            return cmd
        turn = max(-math.radians(70), min(math.radians(70), turn))
        m = math.hypot(cmd.lx, cmd.ly)
        phi = math.atan2(-cmd.lx, cmd.ly) + turn
        cmd.lx, cmd.ly = -m * math.sin(phi), m * math.cos(phi)
        cmd.info += f" | sidestep {math.degrees(turn):+.0f} deg"
        return cmd

    def _step(self, w: World) -> Command:
        d = dist2d(w.p1, w.p2)
        if self.nav is not None:
            self.nav.walked(w.p2)
            for q in w.npcs:
                if abs(q[2] - w.p2[2]) < 1500:          # same general area (not far below a cliff)
                    self.nav.npc_walked(q)
            self._contact(w)
        self._update_trail(w.p1)
        goal = self._target(w.p2, w.p1)
        if self.hunt:
            loot = self._loot_goal(w)
            goal = (w.enemy if w.enemy_human else None) or loot or                 (w.collect if w.hungry else None) or w.enemy or w.collect or self.wander or w.p2

        # stuck detection: we were trying to move but didn't get anywhere
        moving_wanted = self.state in ("FOLLOW", "CATCHUP", "REVIVE", "HUNT", "LOOT", "FORAGE", "COLLECT")
        if self._last_pos is None or dist2d(self._last_pos, w.p2) > 20.0 or not moving_wanted:
            if self._last_pos is not None and dist2d(self._last_pos, w.p2) > 150:
                self._replans_failed = 0
            # progress made, or not trying to move: restart the "no progress" timer
            self._last_pos, self._last_progress_t = w.p2, w.t
        elif moving_wanted and w.t - self._last_progress_t > self.stuck_time and w.t >= self._unstuck_until:
            if self.nav is not None and self._replans_failed < self.max_replans and                     (abs(goal[2] - w.p2[2]) < self.climb_dz if len(goal) > 2 else True):
                ahead = self._path[0] if self._path else goal          # mapped bot: block + replan first
                self.nav.blocked_ahead(w.p2, math.atan2(ahead[1] - w.p2[1], ahead[0] - w.p2[0]))
                self._replan = True
                self._replans_failed += 1
                self._last_progress_t = w.t
            else:
                self._replans_failed = 0
                self._start_unstuck(w, goal)

        if w.t < self._unstuck_until:
            dz = goal[2] - w.p2[2]
            if self._unstuck_kind == "SIDE" and dz < -self.climb_dz and w.p2[2] > self._unstuck_z + 100:
                self._unstuck_until = w.t          # side-step climbed back UP although we must go down: abort
            else:
                return self._unstuck_cmd(w, goal, d)

        if w.p1_downed:
            if d > self.revive_range:
                lx, ly = stick_towards(w.p2, goal, w.cam_yaw)
                self.state = "REVIVE"
                return Command(lx=lx, ly=ly, sprint=True, state="REVIVE", info=f"to P1 {d / 100:.1f} m")
            self.state = "REVIVE"
            return Command(interact=True, state="REVIVE", info="reviving")

        if self.hunt:
            if not self.coop:
                return self._hunt(w, goal)
            wl = self._coop_leash(w, d)
            if wl is not None:
                return self._hunt(wl, wl.collect or wl.enemy or wl.loot or goal)
            goal = self._target(w.p2, w.p1)         # nothing to do near P1: follow (falls through)
        if w.enemy is not None and dist2d(w.enemy, w.p2) < self.engage and d < self.leash:
            return self._combat(w)
        self._aim_since = None

        if self._careful(w):
            # crypt with traps: never leave P1's trail (slot / shortcuts could step on a plate), no sprint,
            # stand still on the trail when close; P1 walked it safely, so the trail is the safe path
            if d > 350 and dist2d(goal, w.p2) > 60:
                lx, ly = stick_towards(w.p2, goal, w.cam_yaw)
                self.state = "FOLLOW"
                return Command(lx=lx * 0.6, ly=ly * 0.6, state="FOLLOW", info=f"careful (traps) d={d / 100:.1f} m")
            self.state = "IDLE"
            return Command(state="IDLE", info=f"careful (traps), waiting d={d / 100:.1f} m")
        if d > self.catchup:
            lx, ly = stick_towards(w.p2, goal, w.cam_yaw)
            self.state = "CATCHUP"
            return Command(lx=lx, ly=ly, sprint=True, state="CATCHUP", info=f"d={d / 100:.1f} m")
        if d > self.follow_max:
            lx, ly = stick_towards(w.p2, goal, w.cam_yaw)
            self.state = "FOLLOW"
            return Command(lx=lx, ly=ly, state="FOLLOW", info=f"d={d / 100:.1f} m")
        # in range: go to a "slot" behind and to the side of P1 so the bot doesn't block P1's way
        slot = self._slot(w.p1)
        ds = dist2d(w.p2, slot)
        too_close = d < self.personal_space
        if too_close or ds > self.slot_start or (self._to_slot and ds > self.slot_stop):
            self._to_slot = True
            lx, ly = stick_towards(w.p2, slot, w.cam_yaw)
            k = max(0.35, min(0.7, ds / 300.0))     # gentle: slow down near the slot, no overshoot / bumping
            self.state = "FOLLOW"
            return Command(lx=lx * k, ly=ly * k, state="FOLLOW",
                           info=f"slot {ds / 100:.1f} m" + (" (too close)" if too_close else ""))
        self._to_slot = False
        self.state = "IDLE"
        return Command(state="IDLE", info=f"d={d / 100:.1f} m")

    def _loot_goal(self, w):
        if w.loot is None or any(dist2d(w.loot, q) < 150 for q in self._looted):
            return None
        return w.loot

    def _contact(self, w):
        """compare real movement with the commanded one over 0.4 s windows: much less progress than expected
        toward the steering direction = something solid ahead -> block it on the map and replan"""
        if self._move_heading is None or self._move_mag < 0.4 or self.state not in ("HUNT", "LOOT", "COLLECT", "FOLLOW", "CATCHUP"):
            self._probe = None
            return
        h = self._move_heading
        if self._probe is None or abs(math.atan2(math.sin(h - self._probe[2]), math.cos(h - self._probe[2]))) > 0.5:
            self._probe = (w.t, w.p2, h)
            return
        t0, p0, h0 = self._probe
        if w.t - t0 < 0.4:
            return
        dx, dy = w.p2[0] - p0[0], w.p2[1] - p0[1]
        along = dx * math.cos(h0) + dy * math.sin(h0)
        expected = self._free_speed * self._move_mag * (w.t - t0)
        if along > 0.8 * expected:                       # moving freely: learn the real speed (game differs)
            self._free_speed += 0.2 * (along / ((w.t - t0) * self._move_mag) - self._free_speed)
        elif along < 0.35 * expected:
            self.nav.blocked_ahead(w.p2, h0)
            self._replan = True
        self._probe = (w.t, w.p2, h)

    def _route(self, w, goal):
        """next point to walk to on the way to `goal` (A* over the learned map); goal itself if no map/path"""
        if self.nav is None or goal is None:
            return goal
        moved = self._path_goal is None or dist2d(self._path_goal, goal) > 300
        if self._replan or moved or not self._path or w.t - self._path_t > 15.0:   # no timer flip-flopping
            path = self.nav.plan(w.p2, goal, danger=w.danger or ())
            self._replan, self._path_goal, self._path_t = False, goal, w.t
            self._path = path or []
        while len(self._path) > 1 and dist2d(self._path[0], w.p2) < 120:
            self._path.pop(0)
        if not self._path:
            return goal
        x, y = self._path[0]
        return (x, y, goal[2] if len(goal) > 2 else 0.0)

    def _forage(self, w):
        """walk to berries seen on screen; tap X when they are at Lara's feet (eats them)"""
        fx, fy = w.food_screen
        ang = (fx - 0.5) * math.radians(75)         # horizontal field of view ~75 deg; + = to the right
        self.state = "FORAGE"
        tap = (w.t % 0.6) < 0.15
        if fy > 0.72 and abs(fx - 0.5) < 0.18:      # bush right in front of Lara: eat
            return Command(lx=0.0, ly=0.2, interact=tap, state="FORAGE", info="eating berries (X)")
        k = 0.55 if fy > 0.6 else 0.9
        return Command(lx=math.sin(ang) * k, ly=math.cos(ang) * k, interact=fy > 0.65 and tap,
                       state="FORAGE", info=f"to berries ({fx:.2f}, {fy:.2f})")

    def _collect(self, w):
        """walk to the pickup; then try it from each side: stand ~1.1 m away, push gently toward it and tap X
        (crates open from ONE side only and need repeated taps). Never climb onto it; step off if on top.
        run_bot clears w.collect once the object vanishes (= taken)."""
        c = w.collect
        dc = dist2d(c, w.p2)
        raised = c[2] - w.p2[2] > 90
        if dc > (600 if raised else 300):           # raised pickups (truck crate): start the routine from 6 m
            self._side_k, self._side_t = None, None
            lx, ly = stick_towards(w.p2, self._route(w, c), w.cam_yaw)
            self.state = "COLLECT"
            return Command(lx=lx, ly=ly, sprint=dc > 2500, state="COLLECT",
                           info=f"to pickup {dc / 100:.1f} m at {c[0]:.0f}/{c[1]:.0f}/{c[2]:.0f}")
        up = c[2] - w.p2[2]
        self._climbing = up > (60 if self._climbing else 90)   # hysteresis: 0.8 m flicker reset the mash timer
        if self._climbing:
            # GROUND FIRST: most raised pickups (dark wood on trunks, herbs on rocks) are taken from the ground -
            # mash X at the base for 4 s before trying to climb (live: Lara jumped / grappled at every tree)
            if self._ground_try is None or self._ground_try[0] != c:
                self._ground_try = (c, w.t + 4.0)
            if w.t < self._ground_try[1]:
                self._climbing = False
        if self._climbing and self._noclimb_for == c:
            self._climbing = False                  # already found: jumping does not get us up to this one
        if self._climbing:
            if self._climb_start is None or self._climb_start[2] != c:
                self._climb_start = (w.t, w.p2[2], c)
            elif w.t - self._climb_start[0] > 2.5 and w.p2[2] - self._climb_start[1] < 50:
                # 2.5 s of jumping did not lift Lara: it's wood on a tree trunk / a wall, not a ledge to climb.
                # Stand at its base and mash X from the ground (live: Lara kept jumping at trees).
                self._noclimb_for, self._climbing, self._climb_start = c, False, None
        if self._climbing:
            # pickup is up on something (truck bed, ledge): walk at it and jump to climb up, then work the sides
            lx, ly = stick_towards(w.p2, c, w.cam_yaw)
            self.state = "COLLECT"
            return Command(lx=lx, ly=ly, jump=(w.t % 1.0) < 0.1, state="COLLECT",
                           info=f"pickup is {(c[2] - w.p2[2]) / 100:.1f} m up - climbing")
        if w.p2[2] - c[2] > 60 and dc < 150:
            # standing ON the crate: walk off it, away from its centre
            away = (w.p2[0] + (w.p2[0] - c[0]) * 3 + 1, w.p2[1] + (w.p2[1] - c[1]) * 3, w.p2[2])
            lx, ly = stick_towards(w.p2, away, w.cam_yaw)
            self.state = "PICKUP"
            return Command(lx=lx, ly=ly, state="PICKUP", info="on top of the pickup - stepping off")
        if self._side_k is None:                   # first arrival: start on the side we came from
            self._side_k, self._side_t, self._mash_t = 0, w.t, None
            self._side_base = math.atan2(w.p2[1] - c[1], w.p2[0] - c[0])
        a = self._side_base + self._side_k * math.pi / 2
        spot = (c[0] + math.cos(a) * self.side_dist, c[1] + math.sin(a) * self.side_dist, c[2])
        need = self.first_side_mash if self._side_k == 0 else self.side_mash
        at_spot = dist2d(spot, w.p2) <= 45 or (self._side_k == 0 and dc < 180)
        done = (self._mash_t is not None and w.t - self._mash_t > need) or                (self._mash_t is None and w.t - self._side_t > self.side_walk)     # side not reachable: skip it
        if done:                                   # next side (90 deg further round)
            self._side_k, self._side_t, self._mash_t = (self._side_k + 1) % 4, w.t, None
            return Command(state="PICKUP", info="pickup: next side")
        self.state = "PICKUP"                      # (not a stuck-detection state)
        if not at_spot and self._mash_t is None:   # walk round to that side (stay out of the crate's footprint)
            lx, ly = stick_towards(w.p2, spot, w.cam_yaw)
            return Command(lx=lx * 0.6, ly=ly * 0.6, state="PICKUP", info=f"pickup: to side {self._side_k + 1}/4")
        if self._mash_t is None:
            self._mash_t = w.t
        lx, ly = stick_towards(w.p2, c, w.cam_yaw)  # face it: tiny push toward the pickup + mash X
        return Command(lx=lx * 0.25, ly=ly * 0.25, mash=True, state="PICKUP",
                       info=f"collecting (mashing X) side {self._side_k + 1}/4, {w.t - self._mash_t:.1f}/{need:.0f} s")

    MODES = ("follow", "hunt", "collect", "auto", "guard")

    def set_mode(self, mode):
        """switch behaviour live (F1-F5): follow = old follow/revive brain; the others use the hunt brain with
        parts switched off"""
        self.mode = mode
        self.hunt = mode != "follow"
        self._target_lock = None
        self._path, self._path_goal = [], None

    def _hunt(self, w, goal):
        mode = self.mode or "auto"
        if w.under_attack and w.enemy is not None and w.arrows != 0:
            # DANGER: something is hurting Lara -> straight to combat (no pickups / loot / stalking / waiting)
            self.state = "DANGER"
            c = self._combat(replace(w, enemy_human=True))
            c.state = "DANGER"
            c.info = "DANGER: " + c.info
            return c
        if w.need_resources and not w.enemy_human and mode != "guard":
            # no arrows and nothing to craft them from: shooting is pointless -> collect (any mode)
            if w.enemy is not None and dist2d(w.enemy, w.p2) > self.point_blank:
                w = replace(w, enemy=None)
            if w.collect is not None:
                return self._hunt_inner(replace(w, hungry=True), goal)
        if mode == "hunt":                          # animals only: no pickups / berries (bodies still looted)
            w = replace(w, collect=None, food_screen=None)
        elif mode == "collect" and w.enemy is not None and not w.enemy_human                 and dist2d(w.enemy, w.p2) > self.point_blank:
            w = replace(w, enemy=None)              # pickups only - but still defend against close animals
        elif mode == "guard":
            # hold position: shoot whatever comes within range, never walk off
            if w.enemy is not None and dist2d(w.enemy, w.p2) < self.engage:
                return self._combat(w)
            self._aim_since = None
            self.state = "GUARD"
            return Command(state="GUARD", info="guarding this spot")
        return self._hunt_inner(w, goal)

    def _hunt_inner(self, w, goal):
        # low on arrows and nothing close to shoot at: craft (hold LT + RT, test mode: needs wood in the inventory)
        far = w.enemy is None or dist2d(w.enemy, w.p2) > 3000
        low = (w.arrows is not None and w.arrows <= self.craft_at) or w.shots_since_craft >= self.craft_every
        empty = w.arrows == 0 and not w.need_resources
        close = w.enemy is not None and dist2d(w.enemy, w.p2) < self.point_blank
        if w.t < self._craft_until or (low and (far or empty) and not w.enemy_human and not close):
            if self._craft_until == 0.0 or w.t >= self._craft_until + 0.5:
                if w.t >= self._craft_until:
                    self._craft_until = w.t + self.craft_hold
            if w.t < self._craft_until:
                self.state = "CRAFT"
                return Command(aim=False, fire=True, state="CRAFT",       # crafting = hold RT only (user)
                               info=f"crafting arrows (hold RT) {self._craft_until - w.t:.1f} s")
            self.craft_done = True
            self._craft_until = 0.0
        if not w.enemy_human and self._loot_goal(w) is None:
            # an animal within 25 m comes FIRST, hungry or not: it IS the food (live: hunger stayed yellow for
            # 8 min while the bot collected wood and ignored two deer at 24-25 m)
            animal_near = w.enemy is not None and dist2d(w.enemy, w.p2) < self.hunt_first
            if w.hungry and w.collect is not None and not animal_near:
                return self._collect(w)
            if w.hungry and w.food_screen is not None and not animal_near:
                return self._forage(w)
            if w.collect is not None and not animal_near and (w.enemy is None or dist2d(w.collect, w.p2) < 2000):
                return self._collect(w)                         # not hungry: only pickups close by
        if self._loot_goal(w) is not None and not w.enemy_human:
            w = replace(w, enemy=None)              # collect the body first, then hunt the next one
        if w.enemy is not None:
            self._loot_since = None
            de = dist2d(w.enemy, w.p2)
            if w.t < self._approach_until and de > 1200 and not w.enemy_human:
                # no line of sight from here (trees / rocks): walk around them on the planned path, no aiming
                lx, ly = stick_towards(w.p2, self._route(w, w.enemy), w.cam_yaw)
                self.state = "HUNT"
                return Command(lx=lx, ly=ly, state="HUNT", info=f"no line of sight - going around, {de / 100:.0f} m")
            limit = self.engage if w.enemy_human else self.hunt_shoot      # humans: shoot from 20 m
            if self.state == "COMBAT":
                limit += 700                        # hysteresis: keep shooting until 7 m past the start range
            if de < limit:
                return self._combat(w)
            self._aim_since = None
            if not w.enemy_human:
                # QUIET STALKING (live: sprinting at > 30 m made every deer/boar run off -> 0 shots in 5 min)
                away = 0.0
                if w.enemy_vel:
                    ux, uy = (w.enemy[0] - w.p2[0]) / max(de, 1.0), (w.enemy[1] - w.p2[1]) / max(de, 1.0)
                    away = w.enemy_vel[0] * ux + w.enemy_vel[1] * uy        # + = moving away from Lara (cm/s)
                if away > self.spooked_speed:
                    if self._spooked_since is None:
                        self._spooked_since = w.t
                    if w.t - self._spooked_since < self.spooked_wait:   # spooked: freeze, let it settle down
                        self.state = "STALK"
                        return Command(state="STALK", info=f"animal spooked ({away / 100:.1f} m/s away) - "
                                                          f"waiting, {de / 100:.0f} m")
                else:
                    self._spooked_since = None
                lx, ly = stick_towards(w.p2, self._route(w, w.enemy), w.cam_yaw)
                k = self.stalk_speed if de < self.stalk_slow else 1.0
                self.state = "HUNT"
                return Command(lx=lx * k, ly=ly * k, state="HUNT",
                               info=f"stalking quietly, {de / 100:.0f} m" + (" (slow)" if k < 1 else ""))
            lx, ly = stick_towards(w.p2, self._route(w, w.enemy), w.cam_yaw)
            self.state = "HUNT"
            return Command(lx=lx, ly=ly, sprint=de > 3000, state="HUNT",
                           info=f"stalking, {de / 100:.0f} m" + (f", path {len(self._path)} pts" if self.nav else ""))
        self._aim_since = None
        loot = self._loot_goal(w)
        if loot is not None:
            dl = dist2d(loot, w.p2)
            if dl > 100:
                self._loot_since = None
                lx, ly = stick_towards(w.p2, self._route(w, loot), w.cam_yaw)
                self.state = "LOOT"
                return Command(lx=lx, ly=ly, state="LOOT", info=f"to body {dl / 100:.1f} m")
            if self._loot_since is None:
                self._loot_since = w.t
            if w.t - self._loot_since > self.loot_hold:
                self._looted.append(loot)
                self._loot_since = None
            self.state = "IDLE"                     # standing still: don't trigger stuck detection
            return Command(interact=(w.t % 0.6) < 0.2, state="LOOT", info="looting / eating (X)")
        if self.wander is not None and dist2d(self.wander, w.p2) > 400:
            lx, ly = stick_towards(w.p2, self._route(w, self.wander), w.cam_yaw)
            self.state = "HUNT"
            return Command(lx=lx, ly=ly, state="HUNT", info=f"wandering to hunting area, "
                                                             f"{dist2d(self.wander, w.p2) / 100:.0f} m")
        self.state = "IDLE"
        return Command(state="IDLE", info="searching for animals")

    def _dz(self, v, err):
        """stick value with dead-zone compensation: 0 when on target (< 0.5 deg), else at least stick_dz"""
        if abs(err) < math.radians(0.5):
            return 0.0
        mag = self.stick_dz + (1 - self.stick_dz) * min(1.0, abs(v))
        return math.copysign(mag, v)

    def _combat(self, w):
        """Turn fast (no LT) until close, then hold LT, fine-aim with the right stick (ahead of moving targets),
        shoot on a red crosshair (or point blank)."""
        de = dist2d(w.enemy, w.p2)
        ex, ey, ez = w.enemy[0], w.enemy[1], w.enemy[2]
        if w.enemy_vel:
            lead_t = de / self.arrow_speed + 0.05                   # arrow flight + release latency (pre-drawn)
            lx_, ly_ = w.enemy_vel[0] * lead_t, w.enemy_vel[1] * lead_t
            k = min(1.0, self.max_lead / max(1.0, math.hypot(lx_, ly_)))
            ex, ey = ex + lx_ * k, ey + ly_ * k
        # aim from the CAMERA, not from Lara: the camera sits ~0.5 m right of her shoulder, aiming from Lara put
        # the target under the crosshair's left arm (user, clip 2026-10-20)
        ox, oy = (w.cam_pos[0], w.cam_pos[1]) if w.cam_pos else (w.p2[0], w.p2[1])
        want = math.atan2(ey - oy, ex - ox)
        err = math.atan2(math.sin(want - w.cam_yaw), math.cos(want - w.cam_yaw))   # + = enemy is to the left
        derr = 0.0
        if self._prev_err is not None and w.t > self._prev_err[1]:
            derr = (err - self._prev_err[0]) / (w.t - self._prev_err[1])
        self._prev_err = (err, w.t)
        rx = self._dz(-(err * self.turn_gain + derr * self.turn_damp), err)
        ry, perr = 0.0, 0.0
        if w.cam_pitch is not None:
            th = self.target_height if w.enemy_human else (w.enemy_height or self.animal_height)
            eye_z = w.cam_pos[2] if w.cam_pos else w.p2[2] + self.eye_height
            dh = math.hypot(ex - ox, ey - oy)
            want_p = math.atan2((ez + th) - eye_z, max(dh, 1.0))
            perr = want_p - w.cam_pitch
            ry = self._dz(perr * self.pitch_gain, perr)
        if abs(err) > self.preturn and de > self.point_blank and w.t >= self._repos_until:
            # far off target: whip the camera round without aiming (180 deg/s instead of 78 deg/s)
            self._aim_since = None
            self.state = "COMBAT"
            return Command(rx=math.copysign(1.0, rx) if rx else 0.0, ry=ry, state="COMBAT",
                           info=f"turning to target {math.degrees(err):+.0f} deg, {de / 100:.1f} m")
        if self._aim_since is None:
            self._aim_since, self._fire_phase_t = w.t, w.t
        if w.t < self._closer_until:                # branches in the way: walk closer (no aiming while walking)
            lx, ly = stick_towards(w.p2, self._route(w, w.enemy), w.cam_yaw)
            self.state = "COMBAT"
            return Command(lx=lx, ly=ly, state="COMBAT", info=f"moving closer for a clear line, {de / 100:.1f} m")
        if w.t < self._repos_until:                 # blocked shots: strafe sideways while still aiming
            sx, sy = stick_towards(w.p2, w.enemy, w.cam_yaw)
            self.state = "COMBAT"
            return Command(lx=sy * self._repos_side, ly=-sx * self._repos_side, rx=rx, aim=True,
                           state="COMBAT", info=f"repositioning (shots blocked), enemy {de / 100:.1f} m")
        fire = False
        if w.crosshair_red is not None and self.hunt and not w.enemy_human:
            if w.crosshair_red or de < self.point_blank:
                self._no_red_since = None
            else:
                self._no_red_since = self._no_red_since or w.t
                since = w.t - self._no_red_since
                if since > self.no_red_give_up:
                    self.give_up, self._no_red_since = True, None
                elif since > self.no_red_approach and w.t >= self._approach_until + 2.0:
                    self._approach_until = w.t + 5.0       # 5 s of walking around, then try aiming again
        if w.crosshair_red is not None:
            # screen-validated shooting (live 2026-10-19: the only hits came from firing on a red crosshair)
            if w.line_blocked and w.t >= self._red_until:
                # branches / trunk right in front: red only means a target is BEHIND them -> step out first
                if self._blocked_since is None:
                    self._blocked_since = w.t
                elif w.t - self._blocked_since > 0.5:
                    self._blocked_since = None
                    self._blocked_tries += 1
                    if self._blocked_tries >= 3 and de > 800:
                        # side-steps did not clear the branches: walk 3 m closer instead (new angle, fewer branches)
                        self._closer_until, self._blocked_tries = w.t + 1.2, 0
                    else:
                        self._repos_until = w.t + self.reposition_time
                        self._repos_side = w.open_side if w.open_side else -self._repos_side
                return self._combat_out(w, rx, ry, False, de, err, perr)
            self._blocked_since = None
            self._blocked_tries = 0
            # PRE-DRAW: bow drawn (RT held) as soon as we are roughly on target, released on the FIRST red frame
            # (live: drawing only after red + 0.3 s hold was too slow - the animal moved)
            close = de < self.point_blank and not w.enemy_human and abs(err) < math.radians(8)
            on = abs(err) < self.predraw and abs(perr) < self.predraw
            keep = self._draw_since is not None and abs(err) < 2 * self.predraw
            if w.t < self._release_until:
                fire = False                                  # just shot: short pause, then draw again
            elif on or keep or close:
                if self._draw_since is None:
                    self._draw_since = w.t
                fire = True
                if w.t - self._draw_since >= self.min_draw and (w.crosshair_red or close):
                    fire = False                              # release = shoot
                    self._draw_since, self._release_until = None, w.t + self.nock_time
            else:
                fire, self._draw_since = False, None
            return self._combat_out(w, rx, ry, fire, de, err, perr)
        drawing = (w.t - self._fire_phase_t) % (self.draw_time + self.release_time) < self.draw_time             and w.t - self._fire_phase_t > 0.05
        lim = self.keep_drawing if drawing else self.aim_ok
        if abs(err) < lim and abs(perr) < lim and w.t - self._aim_since > 0.4:
            ph = (w.t - self._fire_phase_t) % (self.draw_time + self.release_time)
            fire = ph < self.draw_time
        else:
            self._fire_phase_t = w.t                # restart the draw once we're back on target
        return self._combat_out(w, rx, ry, fire, de, err, perr)

    def _combat_out(self, w, rx, ry, fire, de, err, perr):
        if w.arrows == 0:
            fire = False                            # empty quiver: never "shoot" (live: endless empty shots)
        # count released shots; if the target's HP never changes, something (tree, rock) blocks the line
        if self._was_drawing and not fire:
            self._shots += 1
            if self._hp_at_first_shot is None:
                self._hp_at_first_shot = w.enemy_hp
            elif w.enemy_hp is not None and w.enemy_hp != self._hp_at_first_shot:
                self._shots, self._hp_at_first_shot = 0, w.enemy_hp     # a hit: keep shooting from here
            if self._shots >= self.miss_limit:
                self._shots, self._hp_at_first_shot = 0, None
                self._repos_until = w.t + self.reposition_time
                self._repos_side = -self._repos_side
        self._was_drawing = fire
        self.state = "COMBAT"
        return Command(rx=rx, ry=ry, aim=True, fire=fire, state="COMBAT",
                       info=f"enemy {de / 100:.1f} m, aim err {math.degrees(err):+.0f}/{math.degrees(perr):+.0f} deg")

    def _slot(self, p1):
        """Spot behind P1 (slot_back) and to P1's right (slot_side), using P1's walking direction."""
        if self._p1_prev is not None and dist2d(self._p1_prev, p1) > 30.0:
            a = math.atan2(p1[1] - self._p1_prev[1], p1[0] - self._p1_prev[0])
            if self._p1_heading is None:
                self._p1_heading = a
            else:
                self._p1_heading += 0.3 * math.atan2(math.sin(a - self._p1_heading), math.cos(a - self._p1_heading))
            self._p1_prev = p1
        elif self._p1_prev is None:
            self._p1_prev = p1
        if self._p1_heading is None:
            return p1[0], p1[1], p1[2]
        h = self._p1_heading
        bx, by = -math.cos(h) * self.slot_back, -math.sin(h) * self.slot_back
        rx, ry = math.sin(h) * self.slot_side, -math.cos(h) * self.slot_side     # P1's right
        return p1[0] + bx + rx, p1[1] + by + ry, p1[2]

