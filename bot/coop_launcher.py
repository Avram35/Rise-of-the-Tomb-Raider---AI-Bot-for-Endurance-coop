"""Own 2-player launcher for ROTTR Endurance co-op on one PC (replaces Nucleus: no injected hooks).

What it does (the same jobs Nucleus did, done with plain files):
  1. instance folders  instances/P1, instances/P2: hard links to the Steam install (no disk space, same drive),
     a real COPY of ROTTR.exe with patches:
       - FMOD profiler off (2nd copy can't open the profiler port -> fatal "FMOD socket error")       [3 sites]
       - no pause when the window loses focus ("SystemPause" check at 0x1401a007d: JZ -> JMP)        [1 site]
       - P2 only: single-instance lock renamed TR2MUTEX -> TR2MUTE2, so both copies can run          [1 site]
  2. Steam: P1 = real Steam (your account). P2 = Goldberg emulator (name "Lara Croft"), profile kept in
     %APPDATA%\\Goldberg SteamEmu Saves.
  3. EOS (the co-op lobbies): Nemirtinga's Epic emulator in BOTH folders, LAN on, so the two copies find each other.
  4. Controllers: P1 reads controller 1 (your pad). P2's exe is patched to read controller 2 (the bot's virtual
     pad; start run_bot.py first so it exists). No XInputPlus DLL (it was the common factor in the crashes).
  5. Graphics registry: P1 starts with your settings; P2 with an 800x600 window + low quality; then restored.

    venv\\Scripts\\python coop_launcher.py build     create / refresh the instance folders (first time ~1 min)
    venv\\Scripts\\python coop_launcher.py start     build if needed, then launch P1, wait, launch P2
Needs: Steam running, Nucleus' utils folder (for the emulator DLLs) at NUCLEUS below.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "coop_config.json")


def find_game():
    """Steam library folders (registry + libraryfolders.vdf) -> ...\\common\\Rise of the Tomb Raider, or None"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        steam = r"C:\Program Files (x86)\Steam"
    libs = [steam]
    vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        text = open(vdf, encoding="utf-8", errors="replace").read()
        libs += [os.path.normpath(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    for lib in libs:
        g = os.path.join(lib, "steamapps", "common", "Rise of the Tomb Raider")
        if os.path.exists(os.path.join(g, "ROTTR.exe")):
            return g
    return None


def load_config():
    cfg = {}
    if os.path.exists(CONFIG):
        try:
            cfg = json.load(open(CONFIG))
        except ValueError:
            cfg = {}
    if not cfg.get("game"):
        cfg["game"] = find_game() or ""
    if not cfg.get("emulators"):
        cfg["emulators"] = r"C:\Nucleus\utils" if os.path.isdir(r"C:\Nucleus\utils") else ""
    if not cfg.get("instances") and cfg["game"]:
        # same drive as the game (hard links only work within one drive); drive root, not Program Files (no admin)
        cfg["instances"] = os.path.join(os.path.splitdrive(cfg["game"])[0] + "\\", "ROTTR_Coop_Instances")
    return cfg


def save_config(cfg):
    json.dump(cfg, open(CONFIG, "w"), indent=2)


def check(cfg):
    """-> problems in plain words (empty list = ready)"""
    out = []
    if not os.path.exists(os.path.join(cfg.get("game") or "-", "ROTTR.exe")):
        out.append("Game folder not found (the folder that contains ROTTR.exe).")
    for need in (("GoldbergEmu", "experimental", "steam_api64.dll"),
                 ("NemirtingasEpicEmu", "x64", "EOSSDK-Win64-Shipping.dll")):
        if not os.path.exists(os.path.join(cfg.get("emulators") or "-", *need)):
            out.append(f"Emulator file missing: {os.path.join(*need)} - install Nucleus Co-op once and pick its "
                       f"'utils' folder.")
    g, i = cfg.get("game") or "", cfg.get("instances") or ""
    if g and i and os.path.splitdrive(g)[0].lower() != os.path.splitdrive(i)[0].lower():
        out.append("The instances folder must be on the same drive as the game.")
    return out


CFG = load_config()
GAME, NUCLEUS, INST = CFG.get("game", ""), CFG.get("emulators", ""), CFG.get("instances", "")
GB_SAVES = os.path.join(os.environ["APPDATA"], "Goldberg SteamEmu Saves")
REG = r"SOFTWARE\Crystal Dynamics\Rise of the Tomb Raider\Graphics"

PATCHES_ALL = [
    (bytes.fromhex("41B8A0002080"), bytes.fromhex("41B880002080"), 3, "FMOD profiler off"),
    (bytes.fromhex("ff92e000000084c00f843a010000"), bytes.fromhex("ff92e000000084c090e93a010000"), 1,
     "no pause on focus loss"),
]
PATCH_P2 = [("TR2MUTEX".encode("utf-16le"), "TR2MUTE2".encode("utf-16le"), 1, "own single-instance lock"),
            # leaderboard crash (pause menu "Friends' Scores" + end-of-run screen): Goldberg returns entries with a
            # garbage detail count, FUN_140563350 then reads past its 64-int array -> access violation at
            # ROTTR.exe+0x563553 (all Nucleus-era crashes + the first own-launcher pause crash). P2 skips the
            # downloaded entries: "if (GetDownloadedLeaderboardEntry(...))" JZ -> JMP.
            (bytes.fromhex("ff90f000000084c00f8486020000"), bytes.fromhex("ff90f000000084c090e986020000"), 1,
             "skip broken emulator leaderboard entries")]
OWN_FILES = {"rottr.exe", "steam_api64.dll", "steamclient64.dll", "steam_appid.txt", "steam_interfaces.txt",
             "eossdk-win64-shipping.dll", "xinput1_3.dll", "xinput9_1_0.dll", "xinput1_4.dll", "xinputplus.ini",
             "disable_lan_only.txt"}
INTERFACES = ["SteamClient017", "SteamUser019", "SteamFriends015", "SteamUtils009", "SteamMatchMaking009",
              "SteamMatchMakingServers002", "STEAMUSERSTATS_INTERFACE_VERSION011", "STEAMAPPS_INTERFACE_VERSION008",
              "SteamNetworking005", "STEAMREMOTESTORAGE_INTERFACE_VERSION014", "STEAMSCREENSHOTS_INTERFACE_VERSION003",
              "STEAMHTTP_INTERFACE_VERSION002", "SteamController006", "STEAMUGC_INTERFACE_VERSION010",
              "STEAMAPPLIST_INTERFACE_VERSION001", "STEAMMUSIC_INTERFACE_VERSION001",
              "STEAMMUSICREMOTE_INTERFACE_VERSION001", "STEAMHTMLSURFACE_INTERFACE_VERSION_004",
              "STEAMINVENTORY_INTERFACE_V002", "STEAMVIDEO_INTERFACE_V002", "STEAMPARENTALSETTINGS_INTERFACE_VERSION001"]


def link_tree(dst):
    """hard-link every game file into dst (skipped if already there); per-instance files are handled later"""
    n = 0
    for root, dirs, files in os.walk(GAME):
        rel = os.path.relpath(root, GAME)
        out = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(out, exist_ok=True)
        for f in files:
            if rel == "." and f.lower() in OWN_FILES:
                continue
            target = os.path.join(out, f)
            if not os.path.exists(target):
                os.link(os.path.join(root, f), target)
                n += 1
    return n


def pad_patches(slot):
    """make this copy read XInput controller `slot` (0-3) instead of always the first one - no XInputPlus DLL.
    Pad object setup FUN_140cb91e0: mov edi,edx -> lea edi,[rdx+slot] (the skipped JZ only matters without XInput).
    Connect check FUN_140ca15c0: xor ecx,ecx -> mov ecx,ebp (ebp = 1 there) for slot >= 1."""
    out = [(bytes.fromhex("8bfa488bd97431488d5424208bcf"),
            bytes.fromhex("488bd98d7a%02x90488d5424208bcf" % slot), 1, f"reads controller {slot + 1}")]
    if slot >= 1:
        out.append((bytes.fromhex("4084ed7413488d54242033c9ff9388000000"),
                    bytes.fromhex("4084ed7413488d5424208bcdff9388000000"), 1, "connect check on that controller"))
    return out


def pad_slots():
    """(your slot, the AI's slot) from pad_slots.json, written by run_bot.py when it creates the virtual pad"""
    try:
        p = json.load(open(os.path.join(HERE, "pad_slots.json")))
        bot, human = p["bot"], p.get("human")
        if human is None:
            human = 0 if bot != 0 else 1
        return human, bot
    except (OSError, ValueError, KeyError):
        return 0, 1


def patched_exe(dst, player):
    data = open(os.path.join(GAME, "ROTTR.exe"), "rb").read()
    slot = pad_slots()[player - 1]
    for old, new, count, what in PATCHES_ALL + (PATCH_P2 if player == 2 else []) + pad_patches(slot):
        found = data.count(old)
        if found != count:
            raise SystemExit(f"ROTTR.exe: '{what}' pattern found {found}x (expected {count}) - different game build?")
        data = data.replace(old, new)
        print(f"  P{player}: patched {what}")
    open(os.path.join(dst, "ROTTR.exe"), "wb").write(data)


def epic_settings(dst, player):
    """Nemirtinga's Epic emulator settings: overlay OFF (its renderer hook runs generated code; both P2 crashes in
    the first launcher tests were DEP violations inside EOSSDK on that path), LAN on, own name and ids per player"""
    import random
    p = os.path.join(dst, "nepice_settings", "NemirtingasEpicEmu.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cfg = json.load(open(p)) if os.path.exists(p) else {}
    rnd = random.Random(f"rottr-coop-player{player}")       # stable ids per player across rebuilds
    cfg.setdefault("epicid", "%032x" % rnd.getrandbits(128))
    cfg.setdefault("productuserid", "%032x" % rnd.getrandbits(128))
    cfg.update({"appid": cfg.get("appid", "InvalidAppid"), "disable_online_networking": False, "enable_lan": True,
                "enable_overlay": False, "language": "en", "log_level": "off", "savepath": "appdata",
                "signaling_servers": [], "unlock_dlcs": True,
                "username": "Lara Croft" if player == 2 else "Player 1"})
    json.dump(cfg, open(p, "w"), indent=2)


def build(cfg=None):
    global GAME, NUCLEUS, INST
    if cfg:
        GAME, NUCLEUS, INST = cfg["game"], cfg["emulators"], cfg["instances"]
    problems = check({"game": GAME, "emulators": NUCLEUS, "instances": INST})
    if problems:
        raise SystemExit("\n".join(problems))
    for player in (1, 2):
        dst = os.path.join(INST, f"P{player}")
        os.makedirs(dst, exist_ok=True)
        print(f"P{player}: {link_tree(dst)} new links in {dst}")
        patched_exe(dst, player)
        shutil.copyfile(os.path.join(NUCLEUS, "NemirtingasEpicEmu", "x64", "EOSSDK-Win64-Shipping.dll"),
                        os.path.join(dst, "EOSSDK-Win64-Shipping.dll"))
        open(os.path.join(dst, "steam_appid.txt"), "w").write("391220")
        epic_settings(dst, player)
        for f in ("xinput1_3.dll", "xinput9_1_0.dll", "xinput1_4.dll", "XInputPlus.ini"):   # old XInputPlus files
            if os.path.exists(os.path.join(dst, f)):
                os.remove(os.path.join(dst, f))
        if player == 1:                                   # your real Steam account
            shutil.copyfile(os.path.join(GAME, "steam_api64.dll"), os.path.join(dst, "steam_api64.dll"))
        else:                                             # Goldberg for the bot
            gb = os.path.join(NUCLEUS, "GoldbergEmu", "experimental")
            for f in ("steam_api64.dll", "steamclient64.dll"):
                if os.path.exists(os.path.join(gb, f)):
                    shutil.copyfile(os.path.join(gb, f), os.path.join(dst, f))
            open(os.path.join(dst, "steam_interfaces.txt"), "w").write("\n".join(INTERFACES) + "\n")
            open(os.path.join(dst, "disable_lan_only.txt"), "w").write("")
            st = os.path.join(GB_SAVES, "settings")
            os.makedirs(st, exist_ok=True)
            open(os.path.join(st, "account_name.txt"), "w").write("Lara Croft")
    print("instances ready:", INST)


def reg_read():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG) as k:
        out, i = {}, 0
        while True:
            try:
                n, v, t = winreg.EnumValue(k, i)
            except OSError:
                return out
            out[n] = (v, t)
            i += 1


def reg_write(vals):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG, 0, winreg.KEY_SET_VALUE) as k:
        for n, (v, t) in vals.items():
            winreg.SetValueEx(k, n, 0, t, v)


def start(cfg=None):
    h, b = pad_slots()
    print(f"controllers: you = slot {h + 1}, AI = slot {b + 1}")
    build(cfg)                                   # quick when the copies exist: re-patches the exes for these slots
    saved = reg_read()
    print("starting Player 1 (you) ...")
    subprocess.Popen([os.path.join(INST, "P1", "ROTTR.exe")], cwd=os.path.join(INST, "P1"))
    time.sleep(30)                                         # P1 reads the registry at start
    d = winreg.REG_DWORD
    p2 = {"Fullscreen": (0, d), "ExclusiveFullscreen": (0, d), "WindowWidth": (800, d), "WindowHeight": (600, d),
          "UserWindowWidth": (800, d), "UserWindowHeight": (600, d), "WindowMaximized": (0, d)}
    for q in ("TextureQuality", "TextureFiltering", "ShadowQuality", "SoftShadowQuality", "SSAAQuality", "DOFQuality",
              "MotionBlur", "AmbientOcclusionQuality", "Bloom", "VignetteBlur", "LevelOfDetail", "DynamicFoliage",
              "Tessellation", "LensFlares", "ScreenEffects", "FilmGrain", "ScreenSpaceReflections", "TressFX",
              "SpecularReflectionQuality", "SMAA", "FXAA"):
        if q in saved:
            p2[q] = (0, d)
    reg_write(p2)
    print("starting Player 2 (bot, 800x600 low) ...")
    subprocess.Popen([os.path.join(INST, "P2", "ROTTR.exe")], cwd=os.path.join(INST, "P2"))
    time.sleep(30)
    reg_write(saved)                                       # your settings back
    print("both started; registry restored. Start / host the co-op expedition in P1, join from P2.")


if __name__ == "__main__":
    {"build": build, "start": start}.get((sys.argv[1:] or ["start"])[0], start)()
