"""Offline test of brain.py with a fake world (no game, no pad). Checks steering math and state changes.
    venv\\Scripts\\python sim_test.py
"""
import math

from brain import Brain, World, stick_towards


def approx(a, b, eps=1e-6):
    return abs(a - b) < eps


# 1) steering: target straight ahead of the camera -> stick forward
lx, ly = stick_towards((0, 0, 0), (100, 0, 0), cam_yaw=0.0)
assert approx(lx, 0) and approx(ly, 1), (lx, ly)
# camera faces +Y (90 deg left of +X); target at +X is to the camera's RIGHT -> stick right
lx, ly = stick_towards((0, 0, 0), (100, 0, 0), cam_yaw=math.pi / 2)
assert approx(lx, 1) and approx(ly, 0), (lx, ly)
# target behind the camera -> stick back
lx, ly = stick_towards((0, 0, 0), (-100, 0, 0), cam_yaw=0.0)
assert approx(lx, 0) and approx(ly, -1), (lx, ly)
print("steering math OK")

# 2) simulated follow: P1 walks away, bot follows using its commands
b = Brain()
p1, p2, yaw = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.3   # camera slightly rotated, must not matter
states = []
dt = 0.05
for i in range(int(20 / dt)):
    t = i * dt
    if t < 10:
        p1[0] += 250 * dt                            # P1 walks 2.5 m/s along +X for 10 s
    cmd = b.step(World(tuple(p1), tuple(p2), yaw, t=t))
    speed = 600 if cmd.sprint else 300               # bot: 6 m/s sprint, 3 m/s walk
    # convert stick back to world motion (inverse of stick_towards)
    ang = yaw + math.atan2(-cmd.lx, cmd.ly) if (cmd.lx or cmd.ly) else None
    if ang is not None:
        mag = min(1.0, math.hypot(cmd.lx, cmd.ly))
        p2[0] += math.cos(ang) * speed * mag * dt
        p2[1] += math.sin(ang) * speed * mag * dt
    states.append(cmd.state)
d_end = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
print(f"after 20 s: P1 at {p1[0] / 100:.1f} m, bot {d_end / 100:.1f} m behind; states used: {sorted(set(states))}")
assert d_end < 8.5 * 100, "bot fell too far behind"
assert "FOLLOW" in states and "IDLE" in states
assert "UNSTUCK" not in states, "false stuck detection during normal following"

# 3) stuck: bot doesn't move at all although it wants to -> UNSTUCK appears
b = Brain()
seen = set()
for i in range(100):
    cmd = b.step(World((1000.0, 0, 0), (0.0, 0, 0), 0.0, t=i * 0.05))
    seen.add(cmd.state)
assert "UNSTUCK" in seen, seen
print("stuck detection OK:", sorted(seen))

# 4) revive: P1 downed far away -> run, then interact when close
b = Brain()
c = b.step(World((500.0, 0, 0), (0.0, 0, 0), 0.0, p1_downed=True, t=0))
assert c.state == "REVIVE" and c.sprint
c = b.step(World((50.0, 0, 0), (0.0, 0, 0), 0.0, p1_downed=True, t=0.1))
assert c.interact
print("revive OK")

# 5) breadcrumbs: P1 walks around a corner past a wall; the straight line is blocked, the trail is not
def blocked(p):
    return 150 < p[0] < 1100 and -500 < p[1] < 1150          # wall block inside the corner
b = Brain()
p1, p2 = [0.0, 0.0, 0.0], [0.0, -400.0, 0.0]
dt, unstuck = 0.05, 0
for i in range(int(30 / dt)):
    t = i * dt
    if p1[1] < 1500:
        p1[1] += 250 * dt                                     # north 15 m ...
    elif p1[0] < 2500:
        p1[0] += 250 * dt                                     # ... then east 25 m
    cmd = b.step(World(tuple(p1), tuple(p2), 0.0, t=t))
    unstuck += cmd.state == "UNSTUCK"
    if t > 10 and (cmd.lx or cmd.ly):                         # bot waits 10 s: now the wall is between them
        ang = math.atan2(-cmd.lx, cmd.ly)
        sp = (600 if cmd.sprint else 300) * min(1.0, math.hypot(cmd.lx, cmd.ly))
        nxt = (p2[0] + math.cos(ang) * sp * dt, p2[1] + math.sin(ang) * sp * dt)
        if not blocked(nxt):
            p2[0], p2[1] = nxt
d_end = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
print(f"corner: bot {d_end / 100:.1f} m from P1, UNSTUCK ticks {unstuck}, trail left {len(b.trail)}")
assert d_end < 8.5 * 100

# 6) teleport (respawn) clears the trail
b = Brain()
for i in range(20):
    b.step(World((i * 100.0, 0, 0), (0.0, 0, 0), 0.0, t=i * 0.1))
b.step(World((50000.0, 0, 0), (0.0, 0, 0), 0.0, t=3))
assert len(b.trail) == 1
print("breadcrumbs OK")
# 7) climbable block: crossing x 300..400 only works right after a jump; crumbs on top are higher
def run_climb():
    b = Brain()
    p2, last_jump, t_reach = [0.0, 0.0, 0.0], -9.0, None
    trail = [(x * 100.0, 0.0, 250.0 if 300 <= x * 100 <= 400 else 0.0) for x in range(0, 13)]
    for i in range(int(20 / 0.05)):
        t = i * 0.05
        p1 = trail[min(len(trail) - 1, i)]
        cmd = b.step(World(p1, tuple(p2), 0.0, t=t))
        if cmd.jump:
            last_jump = t
        if cmd.lx or cmd.ly:
            ang = math.atan2(-cmd.lx, cmd.ly)
            nx = p2[0] + math.cos(ang) * 300 * min(1, math.hypot(cmd.lx, cmd.ly)) * 0.05
            ny = p2[1] + math.sin(ang) * 300 * min(1, math.hypot(cmd.lx, cmd.ly)) * 0.05
            on_block = 300 <= nx <= 400
            if on_block and p2[2] < 200 and t - last_jump > 0.3:
                continue                                       # wall: need a jump to get up
            p2 = [nx, ny, 250.0 if on_block else 0.0]
        if p2[0] > 400 and t_reach is None:
            t_reach = t
    return t_reach
tr = run_climb()
print(f"climb: over the block after {tr} s")
assert tr is not None and tr < 6, tr

# 8) ledge drop: bot on a ledge (z 270, y<0), P1 went down (z 130, y>0) and on past a rock that needs side-steps.
#    A jump near the ledge foot re-grabs and climbs back up (the in-game loop). Bot must never climb back.
b = Brain()
p2, climbs_back = [0.0, -300.0, 270.0], 0
trail = [(0.0, -300.0 + k * 100.0, 270.0 if -300 + k * 100 < 0 else 130.0) for k in range(0, 25)]
jump_t = -9
for i in range(int(40 / 0.05)):
    t = i * 0.05
    p1 = trail[min(len(trail) - 1, i // 2)]
    cmd = b.step(World(p1, tuple(p2), 0.0, t=t))
    if cmd.jump:
        jump_t = t
    if cmd.lx or cmd.ly:
        ang = math.atan2(-cmd.lx, cmd.ly)
        nx = p2[0] + math.cos(ang) * 300 * min(1, math.hypot(cmd.lx, cmd.ly)) * 0.05
        ny = p2[1] + math.sin(ang) * 300 * min(1, math.hypot(cmd.lx, cmd.ly)) * 0.05
        if -150 < nx < 150 and 600 < ny < 700:
            continue                                           # rock at the bottom: needs side-steps
        if p2[2] < 200 and ny < 0:
            if t - jump_t < 0.3:
                p2 = [nx, -50.0, 270.0]; climbs_back += 1       # jumped at the ledge foot: back on top
            continue
        p2 = [nx, ny, 270.0 if ny < 0 else 130.0]
d_end = math.dist(p2[:2], trail[-1][:2])
print(f"ledge: climbed back {climbs_back}x, end {d_end / 100:.1f} m from P1")
assert climbs_back == 0 and d_end < 8.5 * 100
# 9) side slot: P1 walks +X then stops -> bot settles ~3.5 m behind and ~2 m to P1's right (right of +X = -Y)
def sim(b, p1_path, p2, secs, yaw=0.7):
    p2 = list(p2)
    for i in range(int(secs / 0.05)):
        t = i * 0.05
        p1 = p1_path(t)
        cmd = b.step(World(p1, tuple(p2), yaw, t=t))
        if cmd.lx or cmd.ly:
            ang = yaw + math.atan2(-cmd.lx, cmd.ly)
            sp = (600 if cmd.sprint else 300) * min(1.0, math.hypot(cmd.lx, cmd.ly))
            p2[0] += math.cos(ang) * sp * 0.05
            p2[1] += math.sin(ang) * sp * 0.05
    return p2, cmd
b = Brain()
p2, cmd = sim(b, lambda t: (min(t, 8) * 250.0, 0.0, 0.0), (-500.0, 0.0, 0.0), 16)
print(f"slot: bot at ({(p2[0] - 2000) / 100:.1f}, {p2[1] / 100:.1f}) m from P1, state {cmd.state}")
assert -3.0 < (p2[0] - 2000) / 100 < -1.0 and -2.0 < p2[1] / 100 < 0.0 and cmd.state == "IDLE"

# 10) personal space: bot starts right on top of a standing P1 (who walked +X before) -> backs off >1.5 m
b = Brain()
for i in range(10):
    b.step(World((i * 50.0, 0, 0), (i * 50.0 - 400, -200.0, 0.0), 0.0, t=i * 0.05))
p2, cmd = sim(b, lambda t: (450.0, 0.0, 0.0), (450.0, 20.0, 0.0), 6)
d = math.hypot(p2[0] - 450, p2[1])
print(f"personal space: {d / 100:.1f} m from P1")
assert d > 60      # backs off to the (closer, 2026-10-20) follow slot
print("follow polish OK")
# 11) combat: target 12 m away, 90 deg to the left. Realistic camera: 180 deg/s at full stick without LT,
#     78 deg/s with LT, stick dead zone 0.25, 1 tick of input lag. Must be on target and drawing fast.
def aim_time(brain_kw, start_err_deg, red_window_deg=2.0, secs=4.0):
    b = Brain(**brain_kw)
    yaw = math.radians(90 - start_err_deg)          # target at +90 deg (straight +Y)
    lag, on_at, fired_at = 0.0, None, None
    for i in range(int(secs / 0.03)):
        t = i * 0.03
        err = math.degrees(math.atan2(math.sin(math.pi / 2 - yaw), math.cos(math.pi / 2 - yaw)))
        cmd = b.step(World((300.0, -200.0, 0), (0.0, 0.0, 0.0), yaw, enemy=(0.0, 1200.0, 0.0), cam_pitch=-0.09,
                           crosshair_red=abs(err) < red_window_deg, t=t))
        eff = max(0.0, (abs(lag) - 0.25) / 0.75) * (1 if lag > 0 else -1)
        yaw -= eff * math.radians(78 if cmd.aim else 180) * 0.03
        lag = cmd.rx
        if on_at is None and abs(err) < 2.0 and cmd.aim:
            on_at = t
        if fired_at is None and cmd.fire:
            fired_at = t
    return on_at, fired_at, err
for start in (16, 36, 90):
    on, fired, err = aim_time({}, start)
    print(f"combat: {start:3d} deg off -> on target (LT held) after {on:.2f} s, first shot at {fired:.2f} s, "
          f"final error {err:+.1f} deg")
    assert on is not None and on < 0.25 + start / 120 and fired is not None and abs(err) < 2.5
# leading: deer 15 m ahead running sideways at 6 m/s -> aim ahead of it (to its direction of travel)
b = Brain()
c = b.step(World((300.0, -200.0, 0), (0.0, 0.0, 0.0), math.pi / 2, enemy=(0.0, 1500.0, 0.0), enemy_vel=(600.0, 0.0),
                 cam_pitch=-0.09, crosshair_red=False, t=0))
assert c.rx > 0.3, c        # deer runs to +X = to the camera's right -> camera must turn right of the deer
print("lead OK (aims ahead of a running deer)")
# point blank: wolf 4 m in front, crosshair not red (too close) -> shoots anyway within ~0.6 s
b = Brain(hunt=True)
fired = [b.step(World((0, 0, 0), (0.0, 0.0, 0.0), math.pi / 2, enemy=(0.0, 400.0, 0.0), cam_pitch=-0.2,
                      crosshair_red=False, line_blocked=False, t=i * 0.03)).fire for i in range(25)]
assert any(fired), "no point-blank shot"
print("point-blank defence OK (first shot after", round(fired.index(True) * 0.03, 2), "s)")
b = Brain()
yaw = math.pi / 2
# P1 downed beats combat; far-away P1 beats combat (bot keeps up instead)
assert b.step(World((300.0, 0, 0), (0, 0, 0), yaw, p1_downed=True, enemy=(0, 1200.0, 0), t=5)).state == "REVIVE"
assert Brain().step(World((4000.0, 0, 0), (0, 0, 0), 0.0, enemy=(0, 1200.0, 0), t=0)).state == "CATCHUP"
# pitch: camera looking down 30 deg at a target on the same level -> stick up until level, then fire
b = Brain()
pitch, fired = math.radians(-30), False
for i in range(int(3 / 0.05)):
    c = b.step(World((300.0, -200.0, 0), (0.0, 0.0, 0.0), math.pi / 2, enemy=(0.0, 1200.0, 0.0), cam_pitch=pitch,
                     t=i * 0.05))
    pitch += max(0.0, (abs(c.ry) - 0.25) / 0.75) * (1 if c.ry > 0 else -1) * 1.36 * 0.05
    fired |= c.fire
print(f"pitch: ended at {math.degrees(pitch):+.1f} deg (want about -5.0, animal body), fired {fired}")
assert abs(math.degrees(pitch) + 5.0) < 2.5 and fired
print("combat OK")

# 11b) blocked shots: target HP never changes -> after 3 shots the bot strafes sideways
b = Brain()
yaw, saw_repos = math.pi / 2, False
for i in range(int(12 / 0.05)):
    c = b.step(World((300.0, -200.0, 0), (0.0, 0.0, 0.0), yaw, enemy=(0.0, 1200.0, 0.0), enemy_hp=2500,
                     cam_pitch=math.radians(-5), t=i * 0.05))
    saw_repos |= "repositioning" in c.info and bool(c.lx or c.ly)
print("blocked shots -> reposition:", saw_repos)
assert saw_repos

# 11c) screen-validated: no fire while the crosshair isn't red, quick 0.5 s shot when it is
b = Brain()
fires = []
for i in range(40):
    red = i >= 20
    c = b.step(World((300.0, -200.0, 0), (0.0, 0.0, 0.0), math.pi / 2, enemy=(0.0, 1200.0, 0.0), cam_pitch=-0.087,
                     crosshair_red=red, t=i * 0.05))
    fires.append(c.fire)
releases = [i for i in range(1, len(fires)) if fires[i - 1] and not fires[i]]   # shot = RT released
assert releases and releases[0] == 20, (releases, fires)        # pre-drawn, released on the FIRST red tick
print("red crosshair gating OK")

# 11d) screen says branches block the line: never fire, step toward the open side instead
b = Brain(); fired = False; repos = 0
for i in range(60):
    c = b.step(World((300.0, -200.0, 0), (0.0, 0.0, 0.0), math.pi / 2, enemy=(0.0, 1200.0, 0.0), cam_pitch=-0.087,
                     crosshair_red=True, line_blocked=True, open_side=-1.0, t=i * 0.05))
    fired |= c.fire
    repos += "repositioning" in c.info
assert not fired and repos
print("blocked line -> no fire, reposition OK")

# 11e) hungry + berries on screen: walk toward them (right side -> stick right), tap X when at Lara's feet
b = Brain(hunt=True)
c = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, hungry=True, food_screen=(0.8, 0.5), t=0))
assert c.state == "FORAGE" and c.lx > 0.3 and c.ly > 0, c
xs = [b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, hungry=True, food_screen=(0.5, 0.85), t=i * 0.05)).interact
      for i in range(24)]
assert any(xs) and not all(xs)
assert Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, hungry=False, food_screen=(0.5, 0.85),
                                   t=0)).state != "FORAGE"
print("forage OK")

# 11f) collectables: hungry -> pickup before the animal; walk there, then tap X; not hungry + far -> hunt first
b = Brain(hunt=True)
c = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, hungry=True, collect=(0.0, 1000.0, 0.0),
                 enemy=(0.0, 3000.0, 0.0), t=0))
assert c.state == "COLLECT" and c.lx < -0.5, c        # camera faces +X, pickup at +Y = left
# at the crate: walks to a side spot ~1.1 m out, faces it, taps X; then tries the other sides (4 in ~10 s)
b = Brain(hunt=True)
p = [0.0, 850.0, 0.0]
sides, taps = set(), 0
for i in range(int(28 / 0.05)):
    c = b.step(World((0, 0, 0), tuple(p), 0.0, hungry=True, collect=(0.0, 1000.0, 0.0), t=i * 0.05))
    if c.lx or c.ly:
        ang = math.atan2(-c.lx, c.ly)
        nxt = (p[0] + math.cos(ang) * 300 * min(1, math.hypot(c.lx, c.ly)) * 0.05,
               p[1] + math.sin(ang) * 300 * min(1, math.hypot(c.lx, c.ly)) * 0.05)
        if math.hypot(nxt[0], nxt[1] - 1000.0) > 60:        # crate footprint 0.6 m: can't walk into it
            p[0], p[1] = nxt
    taps += c.mash
    m_ = __import__("re").search(r"side (\d)/4", c.info)
    if m_:
        sides.add(m_.group(1))
assert taps >= 6 / 0.05 and sides >= {"1", "2", "3", "4"}, (taps, sides)   # >= 6 s of mashing, all sides
# standing ON the crate (0.9 m above it): steps off first, no X
c = Brain(hunt=True).step(World((0, 0, 0), (20.0, 1000.0, 90.0), 0.0, hungry=True, collect=(0.0, 1000.0, 0.0), t=0))
assert "stepping off" in c.info and not c.interact, c
# crate up on a truck bed (1.3 m higher): first mash X from the ground (4 s), then walk at it and jump to climb
b = Brain(hunt=True)
seq = [b.step(World((0, 0, 0), (0.0, 800.0, 0.0), 0.0, hungry=True, collect=(0.0, 1000.0, 130.0), t=i * 0.05))
       for i in range(int(5 / 0.05))]
assert not any(c_.jump for c_ in seq[:70]) and any("climbing" in c_.info and c_.jump for c_ in seq[78:]),     [c_.info for c_ in seq[75:90]]
# tree wood 1.5 m up the trunk: jumping does not lift Lara -> after ~2.5 s stop jumping and mash X from the ground
b = Brain(hunt=True)
infos = [b.step(World((0, 0, 0), (0.0, 880.0, 0.0), 0.0, hungry=True, collect=(0.0, 1000.0, 150.0), t=i * 0.05))
         for i in range(int(9 / 0.05))]
assert any(c_.mash for c_ in infos[:70]) and not any(c_.jump for c_ in infos[:70]), "ground mash first"
assert not any(c_.jump for c_ in infos[150:]), [c_.info for c_ in infos[140:150]]   # gave up climbing
print("tree wood: stops jumping, mashes from the ground OK")
c = Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, hungry=False, collect=(0.0, 3000.0, 0.0),
                                enemy=(0.0, 2000.0, 0.0), cam_pitch=0.0, t=0))
assert c.state in ("COMBAT", "HUNT"), c
print("collect OK")

# 11g) low on arrows, nothing near: hold LT+RT ~2 s (craft), then report craft_done; animal close -> no crafting
b = Brain(hunt=True)
held = [b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, arrows=1, t=i * 0.05)) for i in range(50)]
assert held[0].state == "CRAFT" and held[0].fire and not held[0].aim and b.craft_done, held[0]
assert Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, arrows=1, enemy=(0.0, 1500.0, 0.0),
                                   cam_pitch=0.0, t=0)).state != "CRAFT"
# 5 shots since the last craft (arrows unknown / plenty) -> craft too
c = Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, arrows=12, shots_since_craft=5, t=0))
assert c.state == "CRAFT" and c.fire and not c.aim, c
# not hungry, deer 20 m away and a pickup 5 m away -> hunt the deer first
c = Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, hungry=False, collect=(500.0, 0.0, 0.0),
                                enemy=(2000.0, 0.0, 0.0), cam_pitch=0.0, t=0))
assert c.state in ("COMBAT", "HUNT", "STALK"), c
# HUNGRY, deer 20 m away and a pickup 5 m away -> still hunt the deer first (it is food)
c = Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, hungry=True, collect=(500.0, 0.0, 0.0),
                                enemy=(2000.0, 0.0, 0.0), cam_pitch=0.0, t=0))
assert c.state in ("COMBAT", "HUNT", "STALK"), c
print("craft OK")

# 11h) target behind rocks: aimed but never red -> after ~4 s walk toward it (no aim), after 25 s give up
b = Brain(hunt=True)
moved_at = gave_up_at = None
for i in range(int(30 / 0.05)):
    t = i * 0.05
    c = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), math.pi / 2, enemy=(0.0, 2000.0, 0.0), cam_pitch=-0.05,
                     crosshair_red=False, line_blocked=False, t=t))
    if moved_at is None and "going around" in c.info and c.ly > 0.5 and not c.aim:
        moved_at = t
    if b.give_up and gave_up_at is None:
        gave_up_at = t
print(f"no line of sight: walks around after {moved_at} s, gives up after {gave_up_at} s")
assert moved_at is not None and moved_at < 6 and gave_up_at is not None and gave_up_at < 30

# 11i) quiet stalking: never sprint at animals, slow inside 35 m, freeze while the animal runs away
b = Brain(hunt=True)
far = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, enemy=(5000.0, 0.0, 0.0), t=0))
assert far.state == "HUNT" and not far.sprint and far.ly > 0.9, far
near = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, enemy=(3000.0, 0.0, 0.0), t=0.1))
assert not near.sprint and 0.3 < near.ly < 0.6, near
spook = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, enemy=(3000.0, 0.0, 0.0), enemy_vel=(400.0, 0.0), t=0.2))
assert spook.state == "STALK" and not (spook.lx or spook.ly), spook
later = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, enemy=(3000.0, 0.0, 0.0), enemy_vel=(400.0, 0.0), t=7.0))
assert later.state == "HUNT", later
print("quiet stalking OK")

# 11j) F1-F5 modes
def mode_step(m, **kw):
    b = Brain(hunt=True); b.set_mode(m)
    return b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, cam_pitch=0.0, t=0, **kw))
both = dict(collect=(500.0, 0.0, 0.0), enemy=(2000.0, 0.0, 0.0))
assert mode_step("hunt", **both).state in ("COMBAT", "HUNT", "STALK")                 # animal, not the pickup
assert mode_step("collect", **both).state in ("COLLECT", "PICKUP")                     # pickup, ignores the deer
assert mode_step("collect", collect=(500.0, 0.0, 0.0), enemy=(400.0, 0.0, 0.0)).state == "COMBAT"   # self-defence
assert mode_step("guard", collect=(500.0, 0.0, 0.0)).state == "GUARD"                 # never walks off
assert mode_step("guard", enemy=(1500.0, 0.0, 0.0)).state == "COMBAT"                 # shoots what comes close
f = mode_step("follow", **both)
assert f.state not in ("COLLECT", "PICKUP", "HUNT"), f                                  # follow brain only
# out of arrows + crafting failed: even in HUNT mode go collect resources, ignore the deer
c = mode_step("hunt", need_resources=True, **both)
assert c.state in ("COLLECT", "PICKUP"), c
# quiver EMPTY with a deer at 20 m: craft now (not keep firing); never fire with 0 arrows
c = mode_step("auto", arrows=0, enemy=(2000.0, 0.0, 0.0))
assert c.state == "CRAFT", c
b0 = Brain(hunt=True)
fired = [b0.step(World((0, 0, 0), (0.0, 0.0, 0.0), math.pi / 2, enemy=(0.0, 1200.0, 0.0), cam_pitch=-0.087,
                       crosshair_red=True, arrows=0, need_resources=False, t=i * 0.05)).fire for i in range(5)]
# DANGER: being hurt by a wolf 8 m away while a pickup is closer -> fight the wolf, not collect
c = mode_step("auto", under_attack=True, collect=(300.0, 0.0, 0.0), enemy=(800.0, 0.0, 0.0))
assert c.state == "DANGER", c
print("modes OK (follow / hunt / collect / auto / guard)")

# 12) hunt: deer 40 m away -> walk to ~15 m, aim + shoot; deer dies -> walk to body, hold X, then search
b = Brain(hunt=True)
p2, yaw, deer, states, seen_interact = [0.0, 0.0, 0.0], 0.0, (4000.0, 0.0, 0.0), [], False
for i in range(int(25 / 0.05)):
    t = i * 0.05
    alive = t < 12
    c = b.step(World((0, 0, 0), tuple(p2), yaw, enemy=deer if alive else None, loot=None if alive else deer,
                     cam_pitch=0.0, t=t))
    states.append(c.state)
    seen_interact |= c.interact
    eff = max(0.0, (abs(c.rx) - 0.25) / 0.75) * (1 if c.rx > 0 else -1)
    yaw -= eff * 1.36 * 0.05
    if c.lx or c.ly:
        ang = yaw + math.atan2(-c.lx, c.ly)
        sp = (600 if c.sprint else 300) * min(1.0, math.hypot(c.lx, c.ly))
        p2[0] += math.cos(ang) * sp * 0.05
        p2[1] += math.sin(ang) * sp * 0.05
print(f"hunt: states {sorted(set(states))}, ended {math.dist(p2[:2], deer[:2]) / 100:.1f} m from body, last {states[-1]}")
assert {"HUNT", "COMBAT", "LOOT"} <= set(states) and seen_interact and states[-1] == "IDLE"
print("ALL TESTS PASSED")
# --- NPC-style navigation rules (NPC_NAV_RE.md) ---
from brain import Command
# early sidestep: wolf 2 m straight ahead on the way to a pickup 20 m away -> stick turns away
b = Brain(hunt=True)
c = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, collect=(2000.0, 0.0, 0.0), npcs=((200.0, 30.0, 0.0),), t=0))
plain = Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, collect=(2000.0, 0.0, 0.0), t=0))
assert "sidestep" in c.info and abs(c.lx - plain.lx) > 0.2, (c, plain)
# the target itself is not avoided
c = Brain(hunt=True).step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, collect=(250.0, 0.0, 0.0), npcs=((250.0, 0.0, 0.0),), t=0))
assert "sidestep" not in c.info, c
# jump only with a reason: stuck on flat ground -> never jumps
b = Brain(hunt=True)
jumps = 0
for i in range(400):
    c = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, collect=(3000.0, 0.0, 0.0), t=i * 0.05))
    jumps += c.jump
assert jumps == 0 and b._attempts >= 2, (jumps, b._attempts)
# stuck below a ledge (goal 2 m higher) -> climbing jump is allowed
b = Brain(hunt=True)
jumps = 0
for i in range(200):
    c = b.step(World((0, 0, 0), (0.0, 0.0, 0.0), 0.0, collect=(3000.0, 0.0, 200.0), t=i * 0.05))
    jumps += c.jump
assert jumps > 0
print("NPC nav rules OK (sidestep, no blind jumps)")

# careful mode: trap 5 m away -> follows the trail at 0.6 stick, never sprints, no side-steps when stuck
b = Brain()
trap = ((500.0, 500.0, 250.0),)
for i in range(40):
    c = b.step(World((1500.0 + i * 10, 0, 0), (0.0, 0.0, 0.0), 0.0, traps=trap, t=i * 0.1))
assert not c.sprint and "careful" in c.info and math.hypot(c.lx, c.ly) <= 0.61, c
seq = [b.step(World((3000.0, 0, 0), (0.0, 0.0, 0.0), 0.0, traps=trap, t=10 + i * 0.1)) for i in range(60)]
assert not any(c_.jump for c_ in seq) and all("SIDE" not in c_.info for c_ in seq)
print("careful trap mode OK")

# co-op leash: pickup 20 m from P1 is ignored (follow); pickup next to P1 is collected
b = Brain(hunt=True, coop=True); b.set_mode("auto")
c = b.step(World((1500.0, 0, 0), (0.0, 0.0, 0.0), 0.0, collect=(-2000.0, 0.0, 0.0), t=0))
assert c.state in ("CATCHUP", "FOLLOW") and c.ly != 0 or c.lx != 0, c
b = Brain(hunt=True, coop=True); b.set_mode("auto")
c = b.step(World((500.0, 0, 0), (0.0, 0.0, 0.0), 0.0, collect=(600.0, 100.0, 0.0), t=0))
assert c.state in ("COLLECT", "PICKUP"), c
print("co-op leash OK")
