"""Keyboard/mouse helpers for game menus (the game ignores virtual-pad presses in menus; keyboard works).

    restart_expedition()   Esc -> Restart Expedition -> Yes  (pause menu: 2nd item; dialog default is NO)
    focus_game()           click the centre of the screen (the game loses focus on every pad connect/disconnect)
"""
import ctypes
import time

u = ctypes.windll.user32
VK = {"esc": 0x1B, "down": 0x28, "up": 0x26, "left": 0x25, "right": 0x27, "enter": 0x0D}


def key(name, hold=0.06):
    vk = VK[name]
    sc = u.MapVirtualKeyW(vk, 0)
    ext = 1 if name in ("down", "up", "left", "right") else 0
    u.keybd_event(vk, sc, ext, 0)
    time.sleep(hold)
    u.keybd_event(vk, sc, ext | 2, 0)
    time.sleep(0.25)


def focus_game():
    w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    u.SetCursorPos(w // 2, h // 3)
    u.mouse_event(2, 0, 0, 0, 0)
    time.sleep(0.05)
    u.mouse_event(4, 0, 0, 0, 0)


def restart_expedition():
    key("esc")
    time.sleep(1.2)
    key("down")                 # Resume -> Restart Expedition
    key("enter")
    time.sleep(1.2)
    key("left")                 # dialog: NO is selected -> move to YES
    key("enter")


def click_in_game(fx, fy):
    """click at a fraction (0..1) of the game window's client area"""
    import run_bot
    r = run_bot.game_rect()
    if not r:
        return False
    x, y = int(r[0] + fx * (r[2] - r[0])), int(r[1] + fy * (r[3] - r[1]))
    u.SetCursorPos(x, y)
    time.sleep(0.1)
    u.mouse_event(2, 0, 0, 0, 0)
    time.sleep(0.05)
    u.mouse_event(4, 0, 0, 0, 0)
    return True


def screen_words():
    """OCR the game window: [(WORD, cx, cy)] in screen pixels"""
    import run_bot
    import winocr
    im = run_bot.grab_frame()
    r = run_bot.game_rect() or (0, 0, im.size[0], im.size[1])
    res = winocr.recognize_pil_sync(im.convert("L"), "en")
    out = []
    for line in res.get("lines", []):
        for wd in line.get("words", []):
            b = wd.get("bounding_rect", {})
            out.append((wd.get("text", "").upper(), r[0] + b.get("x", 0) + b.get("width", 0) / 2,
                        r[1] + b.get("y", 0) + b.get("height", 0) / 2))
    return out


def click_xy(x, y):
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    u.mouse_event(2, 0, 0, 0, 0)
    time.sleep(0.05)
    u.mouse_event(4, 0, 0, 0, 0)


def after_death(alive=lambda: False, timeout=150):
    """after Lara dies: read each menu screen with OCR and press the right thing (2026-10-20):
    Score -> Enter, Stats -> Enter, Timeline -> click RESTART (EXPEDITION), Expeditions / Challenges / Cards ->
    click CONTINUE / START; loading screens -> wait. Ends when alive() says Lara is back."""
    focus_game()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if alive():
            print("after_death: Lara is alive again")
            return True
        try:
            words = screen_words()
        except Exception as e:
            print("after_death OCR:", e)
            time.sleep(2)
            continue
        names = {w for w, _, _ in words}
        if "RESTART" in names and ("TIMELINE" in names or "EXPEDITION" in names):
            w = [x for x in words if x[0] == "RESTART"][0]
            print("after_death: timeline -> RESTART EXPEDITION")
            click_xy(w[1], w[2]); time.sleep(0.8); key("enter"); time.sleep(6)
        elif "SCORE" in names or "STATS" in names or "TIMELINE" in names:
            print("after_death:", "score" if "SCORE" in names else ("stats" if "STATS" in names else "timeline"),
                  "-> Enter")
            key("enter"); time.sleep(3)
        elif names & {"EXPEDITIONS", "CHALLENGES", "CARDS"} and names & {"CONTINUE", "START"}:
            cont = [x for x in words if x[0] == "CONTINUE"]
            w = cont[0] if cont else max((x for x in words if x[0] == "START"), key=lambda x: x[2])   # lowest START
            print(f"after_death: setup page -> {w[0]}")
            click_xy(w[1], w[2]); time.sleep(0.8); key("enter"); time.sleep(3)
        else:
            time.sleep(3)          # loading / intro
    print("after_death: gave up after", timeout, "s")
    return False


if __name__ == "__main__":
    import sys
    time.sleep(1)
    {"restart": restart_expedition, "focus": focus_game, "death": after_death}[sys.argv[1]]()
