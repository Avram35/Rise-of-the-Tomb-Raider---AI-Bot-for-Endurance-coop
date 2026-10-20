"""AI partner runner: reads the world, asks brain.py what to do, drives the virtual Xbox pad.

    venv\\Scripts\\python run_bot.py --demo     simulated world, pad stays idle (try F8 + the status window)
    venv\\Scripts\\python run_bot.py           real game (needs positions from phase 1 -> not available yet)

F8 = bot on/off (starts OFF). F6 (bot off) = tap A once, for the Nucleus "press a button" screen. Closing the status window quits and releases the pad.
Button mapping is in BUTTONS below (ROTTR Xbox layout; change if your in-game bindings differ).
"""
import ctypes
import ctypes.wintypes
import math
import threading
import os
import sys
import time
import tkinter as tk

import vgamepad as vg

from brain import Brain, Command, World

DEMO = "--demo" in sys.argv
EXPLORE = "--explore" in sys.argv
BOT_PID = None                         # co-op: process id of Player 2's game (set when the instances are found)
CAMPFIRE_TYPE = 2196                  # base camp fire (recorded run): X next to it opens the camp menu
TRAP_TYPES = {1556, 1557, 1300}       # ceiling spike trap parts (recorded run 2026-10-20); plates/pits: TODO
SOLO = "--solo" in sys.argv
COMBAT = "--no-combat" not in sys.argv   # --no-combat: follow/revive only
HUNT = "--hunt" in sys.argv              # solo: go after animals on its own, shoot, skin
AUTOSTART = "--autostart" in sys.argv    # bot ON right away (no F8 needed)
KEEP_FOCUS = "--keep-focus" in sys.argv  # unattended tests: click back into the game if it loses focus > 3 s
MINUTES = float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else None
HUMAN_TYPES = {7281, 7278}
MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maps", "endurance.json")
SCREEN = "--no-screen" not in sys.argv   # read the crosshair colour from the screen (game must be on the main monitor)


TYPE_HEIGHT = {7507: 75.0, 7509: 75.0, 7819: 45.0}   # aim height above the feet: deer chest (95 hit the head
                                                      # and missed when it moved, clip 2026-10-19) / boar body


CAP = None                                # capture.Capture (30 fps thread) when SCREEN is on


def game_rect():
    """screen rectangle (x0, y0, x1, y1) of the game's client area - works windowed and fullscreen"""
    if time.time() - getattr(game_rect, "_t", 0) < 2.0:
        return game_rect._r
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, None)
    found = []
    buf = ctypes.create_unicode_buffer(256)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        u.GetWindowTextW(h, buf, 256)
        if buf.value.startswith("Rise of the Tomb Raider") and u.IsWindowVisible(h):
            pid = ctypes.wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if BOT_PID is None or pid.value == BOT_PID:     # co-op: only the bot's (Player 2's) window
                found.append(h)
        return True
    u.EnumWindows(cb, 0)
    r = None
    if found:
        rc = ctypes.wintypes.RECT()
        u.GetClientRect(found[0], ctypes.byref(rc))
        pt = ctypes.wintypes.POINT(0, 0)
        u.ClientToScreen(found[0], ctypes.byref(pt))
        if rc.right > 200 and rc.bottom > 200:
            r = (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
    game_rect._r, game_rect._t = r, time.time()
    return r


def grab_frame():
    """newest frame of the GAME window as a PIL image (30 fps capture thread; ImageGrab as fallback),
    cropped to the game's client area so crosshair / HUD positions are right in windowed mode too"""
    from PIL import Image, ImageGrab
    im = None
    if CAP is not None:
        f, t = CAP.latest()
        if f is not None and time.time() - t < 0.5:
            im = Image.fromarray(f)
    if im is None:
        im = ImageGrab.grab()
    r = game_rect()
    if r and (r[2] - r[0], r[3] - r[1]) != im.size:
        box = (max(0, r[0]), max(0, r[1]), min(im.size[0], r[2]), min(im.size[1], r[3]))
        if box[2] > box[0] + 50 and box[3] > box[1] + 50:  # window off-screen / minimised (co-op): full frame
            im = im.crop(box)
    return im


def screen_view():
    """one screen grab -> (blocked, open_side). blocked: strong texture (branches, bark) in the 240x160 box around
    the crosshair (fog / snow / open ground are smooth; calibrated on shots/ crops, see return line).
    open_side: compare texture in the left vs right third of the middle band (less texture = more open)."""
    from PIL import ImageGrab
    import numpy as np
    im = grab_frame()
    screen_view.icons = survival_icons(im)
    screen_view.food = find_berries(im)
    w, h = im.size
    L = np.asarray(im.convert("L").resize((w // 4, h // 4))).astype(float)    # quarter size is enough
    h4, w4 = L.shape
    c = L[h4 // 2 - 20:h4 // 2 + 20, w4 // 2 - 30:w4 // 2 + 30]
    tex_c = np.abs(np.diff(c, axis=1)).mean() * 0.45     # scale quarter-res gradient to the full-res calibration
    band = L[int(h4 * 0.35):int(h4 * 0.75)]
    t_left = np.abs(np.diff(band[:, : w4 // 3], axis=1)).mean()
    t_right = np.abs(np.diff(band[:, 2 * w4 // 3:], axis=1)).mean()
    side = 1.0 if t_right < t_left * 0.85 else (-1.0 if t_left < t_right * 0.85 else None)
    return tex_c > 2.0, side, tex_c        # calibrated on shots/: blocked 2.9-5.1, clear 0.5-1.1


def survival_icons(im=None):
    """hunger / cold icon colours (top-left HUD, 1920x1080): 'green' | 'yellow' | 'red' | None (HUD hidden)"""
    from PIL import ImageGrab
    import numpy as np
    if im is None:
        im = grab_frame()
    w, h = im.size
    sx, sy = w / 1920, h / 1080
    out = []
    for x0, x1 in ((125, 200), (225, 297)):
        a = np.asarray(im.crop((int(x0 * sx), int(74 * sy), int(x1 * sx), int(132 * sy))).convert("RGB")).astype(int)
        sat = (a.max(axis=2) - a.min(axis=2)) > 60
        if sat.sum() < 150:
            out.append(None)
            continue
        r, g, b = a[sat].mean(axis=0)
        out.append("green" if g > 1.6 * r else ("red" if r > 1.6 * g else "yellow"))
    return tuple(out)


def find_berries(im):
    """densest cluster of berry-red pixels (muted red: ~RGB 95,50,57) in the lower 70% of the screen
    -> (x, y) as 0..1 fractions, or None"""
    import numpy as np
    small = im.convert("RGB").resize((im.size[0] // 4, im.size[1] // 4))
    a = np.asarray(small).astype(int)
    h, w, _ = a.shape
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = (r > 70) & (r - g > 32) & (r - b > 22) & (g < 90)
    m[: int(h * 0.3)] = False
    m[:, : int(w * 0.12)] = False
    m[:, int(w * 0.9):] = False
    m[int(h * 0.45):int(h * 0.55), int(w * 0.47):int(w * 0.53)] = False      # crosshair
    cell = 8
    gh, gw = h // cell, w // cell
    grid = m[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell).sum(axis=(1, 3))
    gy, gx = np.unravel_index(grid.argmax(), grid.shape)
    if grid[gy, gx] < 6:                       # fewer than 6 quarter-res pixels (~100 full-res): nothing
        return None
    return ((gx + 0.5) * cell / w, (gy + 0.5) * cell / h)


RED = {"n": 0, "t": 0.0}                   # latest crosshair red-pixel count from the 30 fps red thread


def _red_count(frame_rgb, rect):
    import numpy as np
    if rect:
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    else:
        cy, cx = frame_rgb.shape[0] // 2, frame_rgb.shape[1] // 2
    a = frame_rgb[cy - 22:cy + 22, cx - 22:cx + 22].astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return int(((r > 140) & (r > g + 60) & (r > b + 60)).sum())


def obstacle_thread(world, nav):
    """the game's own nav obstacles (bfx::BoxObstacle: crates, barrels, small rocks) -> solid walk-map cells.
    One memory scan finds a box (~5 s), links give the rest; refreshed every 60 s, rescan when the list breaks."""
    import navobstacles as no
    r = world.r2
    start = None
    while True:
        try:
            if start is None or not no.is_box(r, start):
                start = no.find_one_box(r)
            if start is not None:
                cells = {}
                for a in no.all_boxes(r, start):
                    b = no.read_box(r, a)
                    if b and b["size"][2] > 20:                 # ignore flat ones
                        for c in no.footprint_cells(b):
                            lo, hi = cells.get(c, (b["zmin"], b["zmax"]))
                            cells[c] = (min(lo, b["zmin"]), max(hi, b["zmax"]))
                nav.set_solid(cells)
                print(f"game obstacles: {len(cells)} solid cells from the nav boxes")
        except Exception as e:
            print("obstacle thread:", e)
        time.sleep(60)


def hud_arrows(im, save_fail=None):
    """real arrow count from the HUD counter (top-right circle, shown while aiming): OCR of the WHOLE game frame
    (reads far better than a tiny crop over snow), keep a 1-2 digit word inside the top-right zone"""
    import re
    import winocr
    w, h = im.size
    try:
        res = winocr.recognize_pil_sync(im.convert("L"), "en")
    except Exception:
        res = {}
    for line in res.get("lines", []):
        for wd in line.get("words", []):
            b = wd.get("bounding_rect", {})
            cx, cy = (b.get("x", 0) + b.get("width", 0) / 2) / w, (b.get("y", 0) + b.get("height", 0) / 2) / h
            if re.fullmatch(r"[0-9]{1,2}", wd.get("text", "")) and cx > 0.80 and cy < 0.25:
                return int(wd["text"])
    n = hud_counter_repeat(im)                     # fallback for single digits over busy texture
    if n is not None:
        return n
    if save_fail:
        im.save(save_fail)                         # debug: what the OCR saw
    return None


def hud_counter_repeat(im):
    """the counter digits alone (white pixels in a 70x44 box at the counter centre, x 0.911 / y 0.0926 of the game
    window), cleaned and TRIPLED side by side - Windows OCR ignores a lone digit but reads '999' (tested on 3 frames
    where the normal OCR failed: 9, 9, 8)"""
    import re
    import numpy as np
    import winocr
    from PIL import Image, ImageFilter
    w, h = im.size
    cx, cy = int(w * 0.911), int(h * 0.0926)
    a = np.asarray(im.crop((cx - 35, cy - 22, cx + 35, cy + 22)).convert("RGB")).astype(int)
    m = a.min(axis=2) > 180
    if m.sum() < 15:
        return None                                # no counter drawn
    ys, xs = np.nonzero(m)
    m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    g = Image.fromarray(np.where(m, 0, 255).astype(np.uint8))
    sc = max(1, int(60 / g.height))
    g = g.resize((g.width * sc, g.height * sc), Image.NEAREST).filter(ImageFilter.MaxFilter(3))
    gap = g.height // 2
    canvas = Image.new("L", (g.width * 3 + gap * 4, g.height + gap * 2), 255)
    for k in range(3):
        canvas.paste(g, (gap + k * (g.width + gap), gap))
    try:
        t = re.sub(r"[^0-9]", "", winocr.recognize_pil_sync(canvas, "en")["text"])
    except Exception:
        return None
    if t and len(t) % 3 == 0 and t[:len(t) // 3] * 3 == t:
        return int(t[:len(t) // 3])
    return None


CRYPT_WORDS = ("CRYPT", "CAVE", "TOMB", "RELIC", "DISCOVERED", "SARCOPH", "CODEX", "ARTIFACT")


def hud_text_thread(world):
    """every 2 s: OCR the game window. (1) pause menu open (RESUME + RESTART) -> Enter = resume.
    (2) bottom-left corner text (the game names a crypt / cave there when Lara is near one, per user) ->
    print it once, and on a crypt word save a screenshot + a census snapshot of everything within 60 m
    (crypt_finds/), so the crypt / artifact / trap object types can be worked out afterwards."""
    import json
    import gamekeys
    import census
    os.makedirs("crypt_finds", exist_ok=True)
    last, n_find = "", 0
    cr = None
    while True:
        time.sleep(2.0)
        try:
            words = gamekeys.screen_words()
            r = game_rect()
            if not r:
                continue
            ws = {w for w, _, _ in words}
            if "RESUME" in ws and ("RESTART" in ws or "OPTIONS" in ws) and getattr(world, "bot_on", True):
                print("pause menu open - resuming (Enter)")
                gamekeys.key("enter")
                continue
            w_, h_ = r[2] - r[0], r[3] - r[1]
            bl = " ".join(w for w, x, y in words if (x - r[0]) < 0.45 * w_ and (y - r[1]) > 0.60 * h_)
            if bl and bl != last:
                last = bl
                print(f"bottom-left text: {bl}")
                if any(k in bl for k in CRYPT_WORDS) and not any(k in bl for k in ("SCORE", "HUNTED", "SKIP")):
                    n_find += 1
                    cr = cr or census.reader()
                    lara = cr.vec4(cr.base + census.INSTANCE_RVA + 0x10)[:3]
                    tag = f"crypt_finds/find{n_find}_{int(time.time())}"
                    grab_frame().save(tag + ".png")
                    json.dump({"text": bl, "lara": list(lara), "objs": census.instances(cr, 6000)},
                              open(tag + ".json", "w"))
                    world.crypt_seen = (lara, bl, time.time())
                    print(f"CRYPT? '{bl}' at {lara[0]:.0f}/{lara[1]:.0f}/{lara[2]:.0f} -> saved {tag}")
        except Exception as ex:
            print(f"hud_text_thread: {ex}")


class _XGP(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class _XST(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _XGP)]


MENU_WORDS = ({"BASE", "CAMP"}, {"SKILLS", "INVENTORY"}, {"SKILLS", "WEAPONS"}, {"CRAFTED", "REQUIREMENTS"})


def coop_menu_thread(world):
    """co-op: every 2.5 s OCR the BOT's window; if it is stuck in the base camp / skills / craft menu (live: the bot
    opened the camp menu and stayed there while P1 died in the crypt) -> press B a few times to back out"""
    import gamekeys
    while True:
        time.sleep(2.5)
        if not getattr(world, "bot_on", False):
            continue
        try:
            ws = {w.strip(".,!:'") for w, _, _ in gamekeys.screen_words()}
        except Exception:
            continue
        if any(m <= ws for m in MENU_WORDS):
            print("bot is in the base camp menu - backing out (B)")
            world.menu_escape_until = time.time() + 2.0


def log_p2_modules(pid):
    """write Player 2's loaded modules (base..end) to p2_modules.txt: the game log's crash address can then be
    matched to the DLL that crashed (2026-10-20: access violation at 0xa38e3553, outside ROTTR.exe)"""
    try:
        import pymem.process
        pm = __import__("pymem").Pymem()
        pm.open_process_from_id(pid)
        with open("p2_modules.txt", "w") as f:
            f.write(f"{time.ctime()} pid {pid}\n")
            for m in pymem.process.enum_process_module(pm.process_handle):
                f.write(f"{m.lpBaseOfDll:#018x} {m.lpBaseOfDll + m.SizeOfImage:#018x} {m.name}\n")
        print("Player 2 module map saved (p2_modules.txt)")
    except Exception as ex:
        print(f"module map failed: {ex}")


def p1_watch_thread(world):
    """co-op: watch PLAYER 1 (the human) and learn from them. Reads the real controller (XInput slot 0; the
    virtual pad is slot 1) every 50 ms:
      - A pressed while moving -> a jump spot on P1's path (the bot jumps at the same place: gaps, spike pits)
      - everything -> p1_log.csv (position, HP, buttons, triggers, sticks) for later tuning"""
    try:
        xi = ctypes.windll.xinput1_4
    except OSError:
        xi = ctypes.windll.xinput9_1_0
    log = open("p1_log.csv", "a", buffering=1)
    log.write("t,x,y,z,hp,buttons,lt,rt,lx,ly,rx,ry\n")
    a_prev, last_log = False, 0.0
    names = {0x1000: "A", 0x2000: "B", 0x4000: "X", 0x8000: "Y", 0x0100: "LB", 0x0200: "RB", 0x0040: "L3",
             0x0080: "R3", 0x0010: "START", 0x0020: "BACK"}
    while True:
        time.sleep(0.05)
        st = _XST()
        if xi.XInputGetState(0, ctypes.byref(st)) != 0:
            continue
        g = st.Gamepad
        p1 = getattr(world, "_p1_last", None)
        a = bool(g.wButtons & 0x1000)
        moving = abs(g.sThumbLX) > 10000 or abs(g.sThumbLY) > 10000
        if a and not a_prev and moving and p1 is not None:
            world.p1_jump_spots.append((p1[0], p1[1], p1[2], time.time()))
            print(f"P1 jumped at {p1[0]:.0f}/{p1[1]:.0f}/{p1[2]:.0f} - the bot will jump there too")
        a_prev = a
        if time.time() - last_log > 0.2 and p1 is not None:
            last_log = time.time()
            hp = (getattr(world, "p1_health", None) or {}).get("health", "")
            btn = "+".join(n for b, n in names.items() if g.wButtons & b)
            log.write(f"{time.time():.2f},{p1[0]:.0f},{p1[1]:.0f},{p1[2]:.0f},{hp},{btn},{g.bLeftTrigger},"
                      f"{g.bRightTrigger},{g.sThumbLX / 32767:.2f},{g.sThumbLY / 32767:.2f},"
                      f"{g.sThumbRX / 32767:.2f},{g.sThumbRY / 32767:.2f}\n")


def arrows_thread(world):
    """while the bot aims, read the arrow counter about once a second (OCR ~50 ms, off the main loop)"""
    while True:
        if getattr(world, "aiming", False):
            try:
                nf = getattr(world, "ocr_fail", 0)
                n = hud_arrows(grab_frame(), save_fail=f"ocr_fail_{nf}.png" if nf < 4 else None)
                if n is not None and 0 <= n <= 99:
                    world._fail_run = 0
                    if n != world.arrows or time.time() - getattr(world, "_ocr_print", 0) > 15:
                        print(f"arrows (HUD): {n}" + ("" if n == world.arrows else f" (estimate was {world.arrows})"))
                        world._ocr_print = time.time()
                    if n <= 2 and getattr(world, "_crafts_since_read", 0) > 0:
                        # crafted but still (almost) no arrows: no wood/feathers -> collect resources first
                        world.need_resources_until, world._res_pickups = time.time() + 600, 0
                        print("out of arrows and crafting did not help - no crafting until 2 pickups are collected")
                    world._crafts_since_read = 0
                    world.arrows = n
                    world.arrows_seen = time.time()
                else:
                    world.ocr_fail = getattr(world, "ocr_fail", 0) + 1
                    world._fail_run = getattr(world, "_fail_run", 0) + 1
                    if world._fail_run == 5:
                        # the HUD shows NO number when the quiver is empty (seen 2026-10-20) -> 0 arrows
                        print("arrows (HUD): no counter while aiming 5x -> quiver EMPTY")
                        world.arrows = 0
                        if getattr(world, "_crafts_since_read", 0) > 0:     # already crafted: no materials
                            world.need_resources_until, world._res_pickups = time.time() + 600, 0
                            print("out of arrows and crafting did not help - no crafting until 2 pickups are collected")
                        else:
                            print("quiver empty -> crafting first")
                    if world.ocr_fail % 20 == 1:
                        print(f"arrows (HUD): not readable ({world.ocr_fail} failed reads so far)")
            except Exception:
                pass
            time.sleep(1.0)
        else:
            time.sleep(0.2)


def red_thread():
    """checks the crosshair on EVERY capture frame (~30 fps, ~1 ms each) so a red frame is seen within ~35 ms"""
    last_t = 0.0
    while True:
        if CAP is None:
            time.sleep(0.1)
            continue
        f, t = CAP.latest()
        if f is not None and t != last_t:
            last_t = t
            try:
                RED["n"], RED["t"] = _red_count(f, game_rect()), t
            except Exception:
                pass
        time.sleep(0.005)


def crosshair_red():
    """reddish pixels at the game's crosshair: from the red thread if fresh, else a direct grab"""
    import numpy as np
    if time.time() - RED["t"] < 0.15:
        return RED["n"]
    im = grab_frame()
    w, h = im.size
    crosshair_red.size = (w, h)
    a = np.asarray(im.crop((w // 2 - 22, h // 2 - 22, w // 2 + 22, h // 2 + 22)).convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return int(((r > 140) & (r > g + 60) & (r > b + 60)).sum())


TICK = 0.033                     # ~30 updates per second
VK_F8 = 0x77
VK_F7 = 0x76
VK_MODES = {0x70: "follow", 0x71: "hunt", 0x72: "collect", 0x73: "auto", 0x74: "guard"}   # F1-F5
VK_F6 = 0x75                     # F6 = tap A once on the virtual pad (Nucleus "press a button" screen)
BUTTONS = {
    "jump": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "interact": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "sprint": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    "drop": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,          # let go of a ledge
}


# ---------------- world sources ----------------
class DemoWorld:
    def set_home(self):
        pass

    """P1 walks a circle; the bot's 'position' follows the commands (same model as sim_test)."""
    def __init__(self):
        self.p2 = [0.0, -600.0, 0.0]
        self.yaw = 0.4
        self.t0 = time.time()

    def read(self):
        t = time.time() - self.t0
        p1 = (math.cos(t * 0.15) * 1500, math.sin(t * 0.15) * 1500, 0.0)
        return World(p1, tuple(self.p2), self.yaw, p1_downed=(20 < t % 60 < 25), t=t)

    def apply(self, cmd, dt):
        if cmd.lx or cmd.ly:
            ang = self.yaw + math.atan2(-cmd.lx, cmd.ly)
            sp = (600 if cmd.sprint else 300) * min(1.0, math.hypot(cmd.lx, cmd.ly))
            self.p2[0] += math.cos(ang) * sp * dt
            self.p2[1] += math.sin(ang) * sp * dt


LARA_POS_RVA = 0x24EF3E0   # local Lara world position (x, y, z floats) = ROTTR.exe + 0x24EF3D0 (object) + 0x10
                           # found via Ghidra (player manager chain -> object, AGInstanceGetPosNode reads +0x10),
                           # same RVA in every session of Steam build 9575745


def load_turn_rate():
    """Camera turn speed (rad/s) at full right stick while aiming (LT held). Measured by turn_test.py."""
    import json
    try:
        return float(json.load(open("cam_turn.json"))["rad_per_s"])
    except Exception:
        return 2.0                 # guess until turn_test.py has run


CAM_DIR_RVAS = (0x15CFB30, 0x2D3B1F0, 0x1A8C540)   # camera look direction (unit x, y, z), found by camscan.py;
                                                   # several copies, first valid one wins


def read_cam(r):
    """-> (yaw, pitch) radians from memory, or None"""
    for rva in CAM_DIR_RVAS:
        v = r.vec4(r.base + rva)
        if v and abs(v[0] * v[0] + v[1] * v[1] + v[2] * v[2] - 1.0) < 0.01:
            return math.atan2(v[1], v[0]), math.asin(max(-1.0, min(1.0, v[2])))
    return None


def read_cam_pos(r):
    """camera position (x, y, z) = 16 bytes after the camera direction (found 2026-10-20, all 3 copies)"""
    for rva in CAM_DIR_RVAS:
        v = r.vec4(r.base + rva + 0x10)
        if v and all(map(math.isfinite, v[:3])):
            return v[:3]
    return None


class CamYawEstimator:
    """Learns where the camera points from how Lara actually moves for a given stick input.
    World direction of movement = cam_yaw + atan2(-lx, ly)  ->  cam_yaw = moved_dir - atan2(-lx, ly)."""
    STEADY = 0.5               # only learn after the stick held the same direction this long (Lara turns slowly)
    STEADY_ANGLE = math.radians(15)

    def __init__(self):
        self.yaw = 0.0
        self.known = False
        self._prev = None
        self._stick_ang = None
        self._steady_since = 0.0

    def reset(self):
        self.known = False
        self._prev = None
        self._stick_ang = None

    turn_rate = None

    def update(self, pos, cmd, now):
        if cmd is not None and abs(cmd.rx) > 0.05 and self._prev_t is not None:
            # right stick turns the camera: predict it (right = clockwise = yaw goes down)
            if self.turn_rate is None:
                self.turn_rate = load_turn_rate()
            self.yaw -= cmd.rx * self.turn_rate * (now - self._prev_t)
            self._stick_ang = None             # camera moving: don't learn from walking this tick
        self._prev_t = now
        self._update_walk(pos, cmd, now)

    _prev_t = None

    def _update_walk(self, pos, cmd, now):
        if cmd is not None and math.hypot(cmd.lx, cmd.ly) > 0.5:
            ang = math.atan2(-cmd.lx, cmd.ly)
            if self._stick_ang is None or abs(math.atan2(math.sin(ang - self._stick_ang),
                                                         math.cos(ang - self._stick_ang))) > self.STEADY_ANGLE:
                self._stick_ang, self._steady_since = ang, now      # stick direction changed: wait again
            elif self._prev is not None and now - self._steady_since >= self.STEADY:
                dx, dy = pos[0] - self._prev[0], pos[1] - self._prev[1]
                if math.hypot(dx, dy) > 5.0:                       # moved at least 5 cm this tick
                    est = math.atan2(dy, dx) - ang
                    if not self.known:
                        self.yaw, self.known = est, True
                    else:                                          # smooth on the circle
                        diff = math.atan2(math.sin(est - self.yaw), math.cos(est - self.yaw))
                        self.yaw += 0.2 * diff
        else:
            self._stick_ang = None
        self._prev = pos


class GameWorld:
    """Real game. Normal mode: P1 = Nucleus Instance0, P2 (bot) = Instance1.
    Solo test (--solo, one game): the bot walks the single Lara back to the 'home' spot saved with F7."""
    def __init__(self, solo=False):
        import psutil
        from probe_players import Reader, norm_exe
        procs = [p for p in psutil.process_iter(["name", "exe"]) if (p.info["name"] or "").lower() == "rottr.exe"]
        self.solo = solo
        if solo:
            if len(procs) != 1:
                raise SystemExit(f"--solo needs exactly one ROTTR.exe running (found {len(procs)})")
            self.r1 = self.r2 = Reader(procs[0].pid)
            self.game_pid = procs[0].pid
        else:
            while True:     # pad already exists; wait until Nucleus has started both games
                procs = [p for p in psutil.process_iter(["name", "exe"])
                         if (p.info["name"] or "").lower() == "rottr.exe"]
                exes = " ".join(norm_exe(p.info["exe"]) for p in procs)   # + own launcher
                if "instance0" in exes and "instance1" in exes:
                    break
                print(f"waiting for both games (Nucleus or coop_launcher) ({len(procs)} ROTTR.exe running)... F6 = tap A")
                if ctypes.windll.user32.GetAsyncKeyState(VK_F6) & 0x8000 and PAD_EARLY:
                    PAD_EARLY[0].press_button(button=BUTTONS["jump"]); PAD_EARLY[0].update(); time.sleep(0.15)
                    PAD_EARLY[0].release_button(button=BUTTONS["jump"]); PAD_EARLY[0].update()
                time.sleep(0.5)
            print("both instances found; waiting 5 s for them to load")
            time.sleep(5)
            nm = lambda p: norm_exe(p.info["exe"])
            by = {("instance0" if "instance0" in nm(p) else "instance1" if "instance1" in nm(p) else "other"): p
                  for p in procs}
            self.r1, self.r2 = Reader(by["instance0"].pid), Reader(by["instance1"].pid)
            self.p2_pid = by["instance1"].pid
            global BOT_PID
            BOT_PID = self.p2_pid
        from health import Health
        from enemies import Enemies
        self.h1 = Health(self.r1)
        self.en = Enemies(self.r2)          # NPCs as seen by the bot's game
        self.enemy = None
        self._en_t = 0.0
        self.view = (None, None, 0.0)
        self.p1_health = None
        self.home = None
        self.p2_pid = getattr(self, "p2_pid", None)
        self.cam = CamYawEstimator()
        self.t0 = time.time()
        self.last_cmd = None

    def _pos(self, r):
        v = r.vec4(r.base + LARA_POS_RVA)
        return (v[0], v[1], v[2]) if v else (0.0, 0.0, 0.0)

    def set_home(self):
        self.home = self._pos(self.r2)

    def p2_focused(self):
        """True while P2's game window is the active window: you are in its menus (pause, etc.).
        The bot must not press anything then - A taps in the pause menu can click 'quit'."""
        if not self.p2_pid:
            return False
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(pid))
        return pid.value == self.p2_pid

    def _traps(self):
        """known crypt trap parts (ceiling spike trap 1556/1557/1300 from the user's recorded run) within 20 m,
        refreshed every 1.5 s in a thread (full instance scan ~50 ms)"""
        if time.time() - getattr(self, "_trap_t", 0) > 1.5 and not getattr(self, "_trap_busy", False):
            self._trap_t, self._trap_busy = time.time(), True

            def job():
                try:
                    import census
                    objs = census.instances(self.r2, 2000)
                    self._trap_list = tuple(tuple(o["pos"]) for o in objs if o["type"] in TRAP_TYPES)
                    self._fires = tuple(tuple(o["pos"]) for o in objs if o["type"] == CAMPFIRE_TYPE)
                    if self._trap_list and not getattr(self, "_trap_said", False):
                        print(f"TRAPS nearby ({len(self._trap_list)} parts) - careful mode: P1's footsteps only")
                    self._trap_said = bool(self._trap_list)
                except Exception:
                    pass
                self._trap_busy = False
            threading.Thread(target=job, daemon=True).start()
        return getattr(self, "_trap_list", ())

    def read(self):
        p2 = self._pos(self.r2)
        self._lara_z = p2[2]
        self._lara_xy = (p2[0], p2[1])
        self.cam.update(p2, self.last_cmd, time.time())
        cam = read_cam(self.r2)
        pitch = None
        if cam:                                  # real camera from memory beats the walking estimate
            self.cam.yaw, pitch = cam
            self.cam.known = True
        self.cam_from_memory = cam is not None
        p1 = (self.home or p2) if self.solo else self._pos(self.r1)
        self._p1_last = p1
        # downed = health <= 0 (verified live: health sits at -99, stage 5, while downed)
        self.p1_health = None if self.solo else self.h1.read()
        downed = bool(self.p1_health and self.p1_health["max"] > 0 and self.p1_health["health"] <= 0)
        if SCREEN and time.time() - getattr(self, "_view_t", 0) > 0.25:
            self._view_t = time.time()
            self.view = screen_view()
            icons = getattr(screen_view, "icons", None)
            if icons and "red" in icons[:2]:          # hunger OR cold red (no fire logic yet) -> restart
                self._red_since = getattr(self, "_red_since", None) or time.time()
                if self.hunt_on and time.time() - self._red_since > 4:
                    self.restart_wanted = True
            else:
                self._red_since = None
            if icons and icons != getattr(self, "_icons", None) and None not in icons:
                print(f"{time.time() - self.t0:6.1f}s hunger={icons[0]} cold={icons[1]}")
                self._icons = icons
        if time.time() - self._en_t > 0.25:            # full array scan ~4x per second
            self._en_t = time.time()
            if self.hunt_on:
                e = self._hunt_target(p2)
                # DANGER: Lara lost >= 10 HP since the last check -> nearest NPC within 12 m is the attacker
                hp = self.lara_hp() if self.solo else None
                if hp is not None:
                    if self._hp_prev is not None and hp <= self._hp_prev - 10:
                        near = [x for x in self.en.scan(1200.0) if (x["health"] or 0) > 0 and not x["dead"]]
                        if near:
                            self._danger_until = time.time() + 8
                            if self._target is None or self._target["slot"] != near[0]["slot"]:
                                print(f"DANGER: Lara hurt ({self._hp_prev} -> {hp}) - fighting type {near[0]['type']} "
                                      f"at {near[0]['dist'] / 100:.1f} m")
                            self._target, self._best = near[0], None
                            e = near[0]
                        else:
                            print(f"Lara hurt ({self._hp_prev} -> {hp}), no NPC within 12 m (fall / cold?)")
                    self._hp_prev = hp
                self._track_velocity(e)
                icon = (getattr(self, "_icons", None) or ["green"])[0]
                need_base = icon == "red" or time.time() < getattr(self, "need_resources_until", 0) or                     (getattr(self, "arrows", None) is not None and self.arrows <= 3)
                if EXPLORE and not need_base and icon != "yellow":
                    self.col, self.collect = None, None
                else:
                    self.collect = self._collect_target()
                if EXPLORE and self.collect is not None:
                    # explore: pickups only when needed (hungry, no arrows / materials) - crypts are the goal
                    icon = (getattr(self, "_icons", None) or ["green"])[0]
                    need = icon == "red" or time.time() < getattr(self, "need_resources_until", 0) or                         (getattr(self, "arrows", None) is not None and self.arrows <= 3) or                         (icon == "yellow" and self.col is not None and self.col["dist"] < 1500)
                    if not need:                        # (live: yellow hunger kept it collecting 3/4 of the round)
                        self.collect = None
            else:
                e = self.en.nearest_alive(max_dist=2500.0) if COMBAT else None
            self.enemy = e
        cpos = read_cam_pos(self.r2)
        if cpos and math.dist(cpos, p2) > 800:
            cpos = None                                  # sanity: camera must be near Lara
        return World(p1, p2, self.cam.yaw, p1_downed=downed, p1_jumps=tuple(self.p1_jump_spots), cam_pitch=pitch, cam_pos=cpos, enemy=self.enemy["pos"] if self.enemy else None, loot=self.loot,
                     enemy_human=bool(self.enemy and self.enemy["type"] in HUMAN_TYPES),
                     enemy_hp=self.enemy["health"] if self.enemy else None,
                     enemy_vel=self._vel if self.enemy else None,
                     crosshair_red=(crosshair_red() >= 6) if (self.enemy and SCREEN) else None,
                     line_blocked=self.view[0] if SCREEN else None, open_side=self.view[1] if SCREEN else None,
                     enemy_height=TYPE_HEIGHT.get(self.enemy["type"]) if self.enemy else None,
                     danger=getattr(self, "danger", ()),
                     collect=getattr(self, "collect", None),
                     npcs=getattr(self, "npcs", ()),
                     traps=self._traps(),
                     arrows=self.arrows if self.hunt_on else None,
                     shots_since_craft=getattr(self, "shots_since_craft", 0),
                     need_resources=time.time() < getattr(self, "need_resources_until", 0),
                     under_attack=time.time() < self._danger_until,
                     hungry=bool(getattr(self, "_icons", None) and self._icons[0] != "green"),
                     food_screen=getattr(screen_view, "food", None) if SCREEN else None,
                     t=time.time() - self.t0)

    loot = None
    _target = None
    _hp_prev = None
    p1_jump_spots = []           # co-op: where the human pressed A while moving (filled by p1_watch_thread)
    _danger_until = 0.0
    _banned = {}                 # slot -> time until which we ignore it (unreachable)
    _best = None                 # (closest distance so far, time it was reached) for the current target

    hunt_on = HUNT               # hunt-type mode active (F2-F5) -> scan animals / pickups, auto-restart
    arrows = 10                  # estimate: start of an expedition shows 10; -1 per shot, +5 per craft hold
    shots_since_craft = 0
    col = None                   # current collectable target (dict from Enemies.collectables)
    _col_banned = {}
    _col_best = None
    _col_near_since = None

    _vel = None
    _vel_prev = None

    def _track_velocity(self, e):
        """target velocity (cm/s) from its memory positions, smoothed; reset when the target changes"""
        now = time.time()
        if not e:
            self._vel = self._vel_prev = None
            return
        p = self._vel_prev
        if p is None or p[0] != e["slot"]:
            self._vel_prev, self._vel = (e["slot"], now, e["pos"]), None
            return
        dt = now - p[1]
        if dt >= 0.2:
            v = ((e["pos"][0] - p[2][0]) / dt, (e["pos"][1] - p[2][1]) / dt)
            if math.hypot(*v) > 1500:            # > 15 m/s = teleport / respawn glitch, ignore
                v = (0.0, 0.0)
            self._vel = v if self._vel is None else (0.5 * self._vel[0] + 0.5 * v[0], 0.5 * self._vel[1] + 0.5 * v[1])
            self._vel_prev = (e["slot"], now, e["pos"])

    def _collect_target(self):
        """nearest pickup (type 9169) not banned; done when it vanishes from memory (= taken)"""
        now = time.time()
        lara_z = getattr(self, "_lara_z", None)
        lst = [e for e in self.en.collectables(5000.0)
               if lara_z is None or abs(e["pos"][2] - lara_z) < 800]          # not 20 m down a cliff
        full = getattr(self, "_kind_full", {})
        self._kind_full = full
        if self.col is not None:
            cur = [e for e in lst if e["handle"] == self.col["handle"]]
            if not cur:
                print(f"collected pickup slot {self.col['slot']} (kind {self.col.get('kind')})"
                      if self.col["dist"] < 400 else f"pickup slot {self.col['slot']} out of range")
                if self.col["dist"] < 400 and self.col.get("kind") in full:
                    del full[self.col["kind"]]             # took one of this kind: it is not full any more
                if self.col["dist"] < 400:
                    self._clip("collected")
                    self._col_banned[self.col["handle"]] = now + 120   # co-op: taken pickups flicker back
                    cp_ = self.col["pos"]
                    for e in lst:
                        if math.dist(e["pos"], cp_) < 100:
                            self._col_banned[e["handle"]] = now + 120
                    if now < getattr(self, "need_resources_until", 0):
                        # craft resource check: crafting failed for lack of materials -> allow it again only
                        # after new materials were picked up (was: blind 2-min timer)
                        self._res_pickups = getattr(self, "_res_pickups", 0) + 1
                        if self._res_pickups >= 2:
                            self.need_resources_until = 0
                            print("collected 2 pickups since crafting failed - crafting allowed again")
                self.col = None
            else:
                self.col = cur[0]
                d = self.col["dist"]
                closer = [e for e in lst if self._col_banned.get(e["handle"], 0) < now and e["handle"] != self.col["handle"]
                          and full.get(e.get("kind"), 0) < now
                          and e["dist"] < d - 200 and e["dist"] < 0.6 * d]
                if closer and d > 250:
                    # a much closer pickup turned up (seen live: feathers under Lara while she chased a tree's wood
                    # 5 m away): take that one first
                    print(f"switching to closer pickup slot {closer[0]['slot']} at {closer[0]['dist'] / 100:.1f} m "
                          f"(was {d / 100:.1f} m)")
                    self.col, self._col_best, self._col_near_since = closer[0], None, None
                    return self.col["pos"]
                if self._col_best is None or d < self._col_best[0] - 300:
                    self._col_best = (d, now)
                if d < 200:
                    self._col_near_since = self._col_near_since or now
                    if now - getattr(self, "_full_ocr_t", 0) > 1.5 and now - self._col_near_since > 1.0:
                        self._full_ocr_t = now          # need-check: does the game say this resource is full?
                        threading.Thread(target=self._check_full_msg, daemon=True).start()
                    if getattr(self, "_full_seen", False):
                        self._full_seen = False
                        print("game says FULL / MAX for this pickup - not needed")
                        self._col_near_since = now - 16
                    if now - self._col_near_since > 15:     # (25 s wasted ~3 min/round on full-inventory pickups)
                        # not taken: inventory full for this resource (seen live: tree wood) -> skip it and every
                        # pickup within 5 m of it (trees come in groups), and pause optional collecting for 2 min
                        print(f"pickup slot {self.col['slot']}: X did not take it in 15 s (inventory full?) - "
                              f"skipping it and pickups near it, collecting paused 2 min unless hungry")
                        if self.col.get("kind") is not None:
                            full[self.col["kind"]] = now + 600   # inventory full for this resource kind
                            print(f"  -> kind {self.col['kind']} marked FULL for 10 min")
                        cp = self.col["pos"]
                        for e in lst:
                            if math.hypot(e["pos"][0] - cp[0], e["pos"][1] - cp[1]) < 500:
                                self._col_banned[e["handle"]] = now + 600
                        if self.col.get("kind") is None:
                            self._col_pause_until = now + 120   # unknown kind: pause optional collecting
                        self.col = None
                elif now - self._col_best[1] > 25:
                    print(f"pickup slot {self.col['slot']} unreachable (no progress 25 s) - skipping")
                    self._col_banned[self.col["handle"]] = now + 300
                    self.col = None
        hungry = bool(getattr(self, "_icons", None) and self._icons[0] != "green")
        if self.col is None and (hungry or now >= getattr(self, "_col_pause_until", 0)):
            fires = getattr(self, "_fires", ())
            free = [e for e in lst if self._col_banned.get(e["handle"], 0) < now
                    and not any(math.dist(e["pos"], f) < 300 for f in fires)
                    and full.get(e.get("kind"), 0) < now]
            if free:
                self.col, self._col_best, self._col_near_since = free[0], None, None
                print(f"pickup target slot {self.col['slot']} at {self.col['dist'] / 100:.1f} m, "
                      f"pos {self.col['pos'][0]:.0f}/{self.col['pos'][1]:.0f}/{self.col['pos'][2]:.0f}")
        return self.col["pos"] if self.col else None

    def _check_full_msg(self):
        try:
            import gamekeys
            words = {w.strip(".,!:;'\"") for w, _, _ in gamekeys.screen_words()}
        except Exception:
            return
        if words & {"FULL", "MAX", "MAXED", "MAXIMUM", "CAPACITY"}:
            self._full_seen = True

    def lara_hp(self):
        if not hasattr(self, "_hp_reader"):
            from health import Health
            self._hp_reader = Health(self.r2)
        v = self._hp_reader.read()
        return v["health"] if v else None

    def _hunt_target(self, p2):
        """self-defence first: any human within 25 m; else nearest animal (max HP >= 100) within 60 m.
        Remembers where a target died (vanished from the live list while within 40 m) for looting."""
        everything = self.en.scan(6000.0, include_dead=True)
        now = time.time()
        if self.loot is not None and getattr(self, "loot_slot", None) is not None:
            b = [e for e in everything if e["slot"] == self.loot_slot]
            if b:
                self.loot = b[0]["pos"]                # bodies slide (ragdoll / slopes): follow the live position
            if now - self.loot_since > 45:
                print("could not reach the body in 45 s - giving up on it")
                self.loot = self.loot_slot = None
        alive = [e for e in everything if not e["dead"] and e["health"] and (e["max"] or 0) >= 100]
        humans = [e for e in alive if e["type"] in HUMAN_TYPES and e["dist"] < 2500]
        self.danger = tuple(e["pos"] for e in alive if e["type"] in HUMAN_TYPES)
        self.npcs = tuple(e["pos"] for e in alive if e["dist"] < 4000)   # their positions = walkable ground
        animals = [e for e in alive if e["type"] not in HUMAN_TYPES and self._banned.get(e["slot"], 0) < now]
        if self._target is not None and not any(e["slot"] == self._target["slot"] for e in alive):
            body = [e for e in everything if e["slot"] == self._target["slot"] and e["dead"]]
            if body:                                   # the game marked it dead (or HP <= 0): a real kill
                if self._target["type"] not in HUMAN_TYPES:
                    self.loot, self.loot_slot, self.loot_since = body[0]["pos"], body[0]["slot"], now
                self._clip("kill")
                print(f"target slot {self._target['slot']} (type {self._target['type']}) down at "
                      f"{self._target['pos'][0]:.0f},{self._target['pos'][1]:.0f}")
            else:
                print(f"target slot {self._target['slot']} lost (fled / unloaded, not a kill)")
            self._target = None
        if humans and (self._target is None or self._target["type"] not in HUMAN_TYPES):
            self._target = humans[0]
            print(f"HUMAN threat slot {self._target['slot']} type {self._target['type']} "
                  f"{self._target['dist'] / 100:.0f} m - defending")
            return self._target
        if self._target is not None:                  # stick with the current target
            self._target = [e for e in alive if e["slot"] == self._target["slot"]][0]
            d = self._target["dist"]
            fleeing = False
            if self._vel and self._target["dist"] > 1:
                ux = (self._target["pos"][0] - p2[0]) / self._target["dist"]
                uy = (self._target["pos"][1] - p2[1]) / self._target["dist"]
                fleeing = self._vel[0] * ux + self._vel[1] * uy > 100      # running away: not "unreachable"
            stalking = getattr(self, "brain_state", "") in ("HUNT", "COMBAT", "STALK")
            if self._best is None or d < self._best[0] - 300 or d < 3200 or fleeing or not stalking:
                # progress / in range / fleeing / bot busy with something else (collecting): restart the 20 s clock
                self._best = (d, now)                 # got 3 m closer: progress
            elif now - self._best[1] > 20 and self._target["type"] not in HUMAN_TYPES:
                print(f"target slot {self._target['slot']} unreachable (no progress in 20 s) - skipping it")
                self._banned[self._target["slot"]] = now + 120
                self._target, self._best = None, None
                return None
            return self._target
        if animals:
            self._target, self._best = animals[0], None
            print(f"new target slot {self._target['slot']} type {self._target['type']} "
                  f"{self._target['dist'] / 100:.0f} m, hp {self._target['health']}")
            return self._target
        return None

    def apply(self, cmd, dt):
        self.aiming = bool(cmd is not None and cmd.aim)
        # shot camera: crop of the screen centre when the draw starts, at release, and 0.6 s after
        if SCREEN and cmd is not None:
            was = getattr(self, "_fire_prev", False)
            now = time.time()
            if cmd.fire and not was:
                self._shot_n = getattr(self, "_shot_n", 0) + 1
            if was and not cmd.fire:
                if cmd.state != "CRAFT" and getattr(self, "_prev_state", None) != "CRAFT":
                    self._clip(f"shot{self._shot_n:03d}")    # 3 s before the release + 2 s after, at 30 fps
                    self.arrows = max(0, self.arrows - 1)
                    self.shots_since_craft = getattr(self, "shots_since_craft", 0) + 1
                    print(f"shot released, ~{self.arrows} arrows left (estimate)")
            if cmd.state == "UNSTUCK" and getattr(self, "_prev_state", None) != "UNSTUCK":
                self._clip("stuck")
            self._prev_state = cmd.state
            self._fire_prev = cmd.fire
        self.last_cmd = cmd

    def _clip(self, tag):
        if CAP is None:
            return
        e = self.enemy or {}
        CAP.event(tag, {"target_type": e.get("type"), "target_hp": e.get("health"),
                        "target_dist_m": round(e.get("dist", 0) / 100, 1), "view": [str(v) for v in self.view],
                        "icons": getattr(self, "_icons", None)})

    def _snap(self, tag):
        from PIL import ImageGrab
        os.makedirs("shots", exist_ok=True)
        w, h = getattr(crosshair_red, "size", (1920, 1080))
        e = self.enemy or {}
        ImageGrab.grab(bbox=(w // 2 - 240, h // 2 - 150, w // 2 + 240, h // 2 + 150)).save(
            f"shots/{self._shot_n:03d}_{tag}_d{e.get('dist', 0) / 100:.0f}m_hp{e.get('health')}.png")


PAD_EARLY = []


# ---------------- pad output ----------------
def now_drop_ok(pad):
    """B is a short tap (0.12 s), at most every 0.5 s."""
    now = time.time()
    if now > getattr(pad, "_drop_until", 0) + 0.4:
        pad._drop_until = now + 0.12
    return now < pad._drop_until


class PadOut:
    def __init__(self, live):
        self.live = live
        self.mash = False
        if live:
            import threading
            threading.Thread(target=self._masher, daemon=True).start()
        self.pad = vg.VX360Gamepad() if live else None
        self._held = set()
        self._jump_until = 0.0

    def send(self, cmd):
        if not self.live:
            return
        self.pad.left_joystick_float(x_value_float=max(-1, min(1, cmd.lx)), y_value_float=max(-1, min(1, cmd.ly)))
        self.pad.right_joystick_float(x_value_float=max(-1, min(1, cmd.rx)), y_value_float=max(-1, min(1, cmd.ry)))
        self.pad.left_trigger_float(value_float=1.0 if cmd.aim else 0.0)
        self.pad.right_trigger_float(value_float=1.0 if cmd.fire else 0.0)
        want = set()
        if cmd.sprint:
            want.add("sprint")
        self.mash = bool(getattr(cmd, "mash", False))
        if cmd.interact and not self.mash:
            want.add("interact")
        if cmd.drop and now_drop_ok(self):
            want.add("drop")
        now = time.time()
        if cmd.jump and now > self._jump_until + 0.3:
            self._jump_until = now + 0.12
        if now < self._jump_until:
            want.add("jump")
        for name in want - self._held:
            self.pad.press_button(button=BUTTONS[name])
        for name in self._held - want:
            self.pad.release_button(button=BUTTONS[name])
        self._held = want
        self.pad.update()

    def _masher(self):
        """X pressed / released every 30 ms while self.mash is set: ~16 presses per second, whatever the main
        loop's tick rate is (the bot ticks at ~14 Hz with the screen checks)"""
        X = BUTTONS["interact"]
        while True:
            if self.mash and self.pad is not None:
                self.pad.press_button(button=X); self.pad.update()
                time.sleep(0.03)
                self.pad.release_button(button=X); self.pad.update()
                time.sleep(0.03)
            else:
                time.sleep(0.02)

    def neutral(self):
        self.mash = False
        if self.live:
            self.pad.reset()
            self.pad.update()
            self._held = set()


# ---------------- status window + loop ----------------
def main():
    out = PadOut(live=not DEMO)          # create the virtual pad FIRST so Nucleus can see it
    PAD_EARLY.append(out.pad)
    world = DemoWorld() if DEMO else GameWorld(solo=SOLO)
    global CAP
    if SCREEN and not DEMO:
        from capture import Capture
        CAP = Capture(fps=30).start()
        import threading
        threading.Thread(target=red_thread, daemon=True).start()
    nav = None
    if (HUNT or SOLO) and "--no-map" not in sys.argv:
        from navmap import NavMap
        os.makedirs("maps", exist_ok=True)
        nav = NavMap.load(MAP_FILE)
        print(f"walk map: {len(nav.walk)} walked / {len(nav.block)} blocked cells loaded")
    brain = Brain(hunt=HUNT, nav=nav, coop=not SOLO)
    brain.set_mode(sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else ("auto" if HUNT else "follow"))
    keys_prev = {}
    if nav is not None and not DEMO:
        import threading
        threading.Thread(target=obstacle_thread, args=(world, nav), daemon=True).start()
    if SCREEN and not DEMO:
        import threading
        threading.Thread(target=arrows_thread, args=(world,), daemon=True).start()
        if SOLO:
            threading.Thread(target=hud_text_thread, args=(world,), daemon=True).start()
    if not SOLO and not DEMO:
        import threading
        threading.Thread(target=p1_watch_thread, args=(world,), daemon=True).start()
        if SCREEN:
            threading.Thread(target=coop_menu_thread, args=(world,), daemon=True).start()
        if getattr(world, "p2_pid", None):
            threading.Thread(target=log_p2_modules, args=(world.p2_pid,), daemon=True).start()
    if "--wander" in sys.argv:
        wx, wy = map(float, sys.argv[sys.argv.index("--wander") + 1].split(","))
        brain.wander = (wx, wy, 0.0)
    if "--loot" in sys.argv and hasattr(world, "loot"):
        lx_, ly_ = map(float, sys.argv[sys.argv.index("--loot") + 1].split(","))
        world.loot = (lx_, ly_, 0.0)
    enabled = {"on": AUTOSTART}
    t_start = time.time()
    f8_prev = {"down": False}
    f7_prev = {"down": False}
    f6_prev = {"down": False}
    was_paused = {"v": False}

    root = tk.Tk()
    root.title(("Lara Croft (AI)" if SOLO else "ROTTR AI partner") + (" (DEMO)" if DEMO else ""))
    root.attributes("-topmost", True)
    root.geometry("560x170+20+20")
    lbl = tk.Label(root, font=("Consolas", 11), justify="left", anchor="w")
    lbl.pack(fill="both", expand=True, padx=8, pady=6)
    last = {"t": time.time()}
    logf = open("bot_log.csv", "w", buffering=1)
    logf.write("t,on,state,p1x,p1y,p2x,p2y,p2z,dist,cam_deg,cam_known,lx,ly,sprint,jump,p1hp,rx,ry,fire,info,ehp,cam_pitch\n")

    explore = {"i": 0, "t": time.time(), "c": None}

    def explore_step():
        """walk a widening loop of waypoints around the start (60, 120, 180 m ...) to find crypts / landmarks"""
        xy = getattr(world, "_lara_xy", None)
        if xy is None:
            return
        if explore["c"] is None:
            explore["c"] = xy
        cs = getattr(world, "crypt_seen", None)
        if cs is not None and "DISCOVERED" in cs[1] and any(k in cs[1] for k in ("CRYPT", "CAVE", "TOMB", "RELIC")):
            # crypt found: stop exploring, go to where it was announced (entrance search starts there)
            if not explore.get("crypt"):
                explore["crypt"] = True
                print(f"explore: CRYPT found ('{cs[1]}') - heading there, exploring stopped")
            brain.wander = (cs[0][0], cs[0][1], cs[0][2])
            return
        i = explore["i"]
        rad, ang = 6000.0 * (1 + i // 8), (i % 8) * math.pi / 4
        wp = (explore["c"][0] + rad * math.cos(ang), explore["c"][1] + rad * math.sin(ang), 0.0)
        if math.hypot(wp[0] - xy[0], wp[1] - xy[1]) < 800 or time.time() - explore["t"] > 150:
            explore["i"] += 1
            explore["t"] = time.time()
            print(f"explore: next waypoint #{explore['i']}")
        brain.wander = wp

    def tick():
        now = time.time()
        world.bot_on = enabled["on"]
        if EXPLORE:
            explore_step()
        dt, last["t"] = now - last["t"], now
        down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F8) & 0x8000)
        if down and not f8_prev["down"]:
            enabled["on"] = not enabled["on"]
            if not enabled["on"]:
                out.neutral()
            elif hasattr(world, "cam"):
                world.cam.reset()          # camera may have been turned while the bot was off -> recalibrate
        f8_prev["down"] = down
        down7 = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F7) & 0x8000)
        if down7 and not f7_prev["down"] and hasattr(world, "set_home"):
            world.set_home()
        f7_prev["down"] = down7
        down6 = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F6) & 0x8000)
        if down6 and not f6_prev["down"] and out.live and not enabled["on"]:
            out.pad.press_button(button=BUTTONS["jump"]); out.pad.update()
            root.after(150, lambda: (out.pad.release_button(button=BUTTONS["jump"]), out.pad.update()))
        f6_prev["down"] = down6
        for vk, m in VK_MODES.items():                       # F1-F5: switch mode live
            dn = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
            if dn and not keys_prev.get(vk) and not DEMO:
                brain.set_mode(m)
                world.hunt_on = m != "follow"
                out.neutral()
                print(f"mode -> {m.upper()}")
            keys_prev[vk] = dn

        if MINUTES and time.time() - t_start > MINUTES * 60:
            print("time limit reached - stopping")
            close()
            return
        if KEEP_FOCUS and SOLO and time.time() - last.get("focus_chk", 0) > 1.0:
            last["focus_chk"] = time.time()
            u = ctypes.windll.user32
            fpid = ctypes.c_ulong()
            u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(fpid))
            if hasattr(world, "r2") and fpid.value != getattr(world, "game_pid", -1):
                last["unfocused"] = last.get("unfocused") or time.time()
                if time.time() - last["unfocused"] > 3:
                    import gamekeys
                    print("game lost focus - clicking back into it")
                    gamekeys.focus_game()
                    last["unfocused"] = None
            else:
                last["unfocused"] = None
        w = world.read()
        if brain.nav is not None and time.time() - last.get("map_save", 0) > 60:
            last["map_save"] = time.time()
            brain.nav.save(MAP_FILE)
        if SOLO and world.hunt_on and hasattr(world, "r2"):
            hp = world.lara_hp() if hasattr(world, "lara_hp") else None
            dead_at_start = hp is not None and hp <= 0 and not last.get("seen_alive")   # e.g. score screen at start
            if (hp is None or hp <= 0) and (dead_at_start or
                                            (last.get("alive_t") and time.time() - last["alive_t"] < 30)):
                # only a real death: alive a moment ago, now no health (loading screens never count)
                last["dead_since"] = last.get("dead_since") or time.time()
                if time.time() - last["dead_since"] > 8 and time.time() - last.get("death_fix", 0) > 60:
                    last["alive_t"], last["seen_alive"] = None, False
                    import gamekeys
                    print("Lara is dead (no health for 8 s) - continue + restart expedition")
                    out.neutral()
                    gamekeys.after_death(alive=lambda: (world.lara_hp() or 0) > 0)
                    last["death_fix"] = time.time()
                    world.loot = world._target = None
                    world.arrows, world.shots_since_craft = 10, 0
                    root.after(35000, gamekeys.focus_game)
            elif hp is not None and hp > 0:
                last["dead_since"], last["alive_t"], last["seen_alive"] = None, time.time(), True
        if getattr(world, "restart_wanted", False):
            # hunger blinking red: restart the expedition (keyboard, the game ignores the pad in menus)
            import gamekeys
            print("hunger or cold red - restarting the expedition")
            out.neutral()
            gamekeys.restart_expedition()
            world.restart_wanted = False
            world.loot = world._target = None
            world.arrows = 10
            world._icons = None
            world._red_since = None
            mode = brain.mode
            brain.__init__(hunt=HUNT, wander=brain.wander, nav=brain.nav)
            brain.set_mode(mode)
            root.after(30000, gamekeys.focus_game)     # after loading: click once so the game has focus
            root.after(int(TICK * 1000), tick)
            return
        paused = enabled["on"] and hasattr(world, "p2_focused") and world.p2_focused()
        if not paused and was_paused["v"] and hasattr(world, "cam"):
            world.cam.reset()              # camera may have moved while you were in P2's window
        was_paused["v"] = paused
        if paused:
            out.neutral()
            world.last_cmd = None
            cmd = Command(state="PAUSED", info="P2 window focused")
        elif enabled["on"]:
            if hasattr(world, "cam") and not world.cam.known:
                # calibration: walk straight forward until the camera direction is learned (~0.6-1 s)
                cmd = Command(lx=0.0, ly=1.0, state="CALIBRATE", info="learning camera")
            else:
                cmd = brain.step(w)
                if time.time() < getattr(world, "menu_escape_until", 0):
                    cmd = Command(drop=(now % 0.5) < 0.12, state="MENU", info="backing out of the camp menu (B)")
            out.send(cmd)
            world.apply(cmd, dt)
            world.brain_state = cmd.state
            if brain.give_up:
                brain.give_up = False
                t = getattr(world, "_target", None)
                if t is not None:
                    world._banned[t["slot"]] = time.time() + 120
                    print(f"target slot {t['slot']} never visible (25 s without a red crosshair) - skipping it 2 min")
                    world._target, world._best = None, None
            if brain.craft_done:
                brain.craft_done = False
                world._crafts_since_read = getattr(world, "_crafts_since_read", 0) + 1
                world._fail_run = 0          # re-check the HUD after this craft
                world.arrows += 5
                world.shots_since_craft = 0
                print(f"craft hold done, assuming ~{world.arrows} arrows (check the HUD / clip)")
                world._clip("craft")
        else:
            cmd = None
            if hasattr(world, "last_cmd"):
                world.last_cmd = None   # player is moving Lara: don't learn camera from the bot's old stick input
        d = math.hypot(w.p1[0] - w.p2[0], w.p1[1] - w.p2[1]) / 100
        cam = getattr(world, "cam", None)
        logf.write(f"{w.t:.3f},{int(enabled['on'])},{cmd.state if cmd else '-'},{w.p1[0]:.1f},{w.p1[1]:.1f},"
                   f"{w.p2[0]:.1f},{w.p2[1]:.1f},{w.p2[2]:.1f},{d:.2f},"
                   f"{math.degrees(cam.yaw) if cam else 0:.1f},{int(cam.known) if cam else 0},"
                   f"{cmd.lx if cmd else 0:.2f},{cmd.ly if cmd else 0:.2f},{int(cmd.sprint) if cmd else 0},"
                   f"{int(cmd.jump) if cmd else 0},{(getattr(world, 'p1_health', None) or {}).get('health', '') or getattr(world, '_hp_prev', '') or ''},"
                   f"{cmd.rx if cmd else 0:.2f},{cmd.ry if cmd else 0:.2f},{int(cmd.fire) if cmd else 0},"
                   f"{(cmd.info if cmd else '').replace(',', ';')},"
                   f"{(getattr(world, 'enemy', None) or {}).get('health', '')},"
                   f"{math.degrees(w.cam_pitch) if w.cam_pitch is not None else ''}\n")
        extra = ""
        if hasattr(world, "cam"):
            src = "memory" if getattr(world, "cam_from_memory", False) else \
                ("learned" if world.cam.known else "learning")
            extra = f"\nCam yaw: {math.degrees(world.cam.yaw):.0f} deg ({src})"
            if SOLO:
                extra += f"\nHome: {'set (F7)' if world.home else 'press F7 to set'}"
        lbl.config(text=(f"{'Lara Croft' if SOLO else 'Bot'}: {'ON ' if enabled['on'] else 'OFF'}   mode {brain.mode.upper()}   "
                         f"(F1 follow F2 hunt F3 collect F4 auto F5 guard, F8 on/off)\n"
                         f"State: {cmd.state if cmd else '-'}  {cmd.info if cmd else ''}\n"
                         + (f"Lara Croft: HP {world.lara_hp() or '?'}" if SOLO else f"Distance to P1: {d:.1f} m")
                         + (f"   enemy {world.enemy['dist'] / 100:.0f} m (type {world.enemy['type']})"
                            if getattr(world, "enemy", None) else "") + "\n"
                         + ("" if SOLO else f"P1 downed: {'YES' if w.p1_downed else 'no'}")
                         + (f"   HP {world.p1_health['health']}/{world.p1_health['max']}"
                            if getattr(world, "p1_health", None) else "") + extra))
        root.after(int(TICK * 1000), tick)

    def close():
        if brain.nav is not None:
            brain.nav.save(MAP_FILE)
            print(f"walk map saved: {len(brain.nav.walk)} walked / {len(brain.nav.block)} blocked cells")
        if CAP is not None:
            CAP.stop()
        out.neutral()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
