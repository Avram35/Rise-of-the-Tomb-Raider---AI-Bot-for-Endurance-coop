# Rise of the Tomb Raider: AI Bot for Endurance co-op

An AI co-op partner for **Rise of the Tomb Raider's Endurance mode** on PC. You and the AI play Endurance co-op
**on one PC**. You are Player 1. The AI plays **Player 2 (Lara Croft)** in a small window: it follows you,
revives you, fights with you, collects near you, copies your jumps, and walks only in your footsteps near
crypt traps.

It reads the game's memory (positions, health, animals, pickups) plus a few screen checks (crosshair colour,
menus), then drives a **virtual Xbox controller**. Your Steam install is never modified: the app makes two
**game copies** (hard links, no extra disk space) and patches only those copies.

> Work in progress. Tested only on **Steam build 9575745** (Windows 11). Another game version will be detected
> and refused, so nothing gets patched wrongly.

---

## Requirements
| What | Why |
|---|---|
| Rise of the Tomb Raider (Steam), Steam running | the game (you must own it) |
| Windows 10 / 11, 64-bit | Windows OCR, XInput |
| [Python 3.11+](https://www.python.org/downloads/) (64-bit, tick "Add to PATH") | runs the app |
| [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases) | the AI's virtual controller |
| [Nucleus Co-op](https://github.com/SplitScreen-Me/splitscreenme-nucleus/releases) **installed once** | only for its emulator files (`utils` folder); Nucleus itself is not used |
| A controller for you | the AI uses a second, virtual one |

## Install
1. Install Python, ViGEmBus (reboot if asked) and Nucleus Co-op. Nucleus only needs to be unpacked somewhere.
2. Download / clone this repository.
3. Double-click **`start.bat`**. The first start creates `bot\venv` and installs the Python packages
   (a few minutes), then the app opens.

## How to play
The app finds the game and the Nucleus `utils` folder by itself. Check the three folders at the top, then:

1. **Prepare game copies.** Do this once, and again after a game update. It takes about 1 minute.
2. **Start AI partner.** This creates the AI's controller and waits for the games.
3. **Start both games.** Your game opens with your settings. The AI's game opens as an 800x600 window on low
   settings.
4. In **your** game, host an Endurance co-op expedition. In the AI's window, join it. While the AI is off,
   **F6** taps A on its controller for the menus.
5. In the game, press **F1** (follow), then **F8** (AI on).

Plug your controller in **before** step 2, so yours is controller 1 and the AI's is controller 2.

### Keys (while the game runs)
| Key | Action |
|---|---|
| **F8** | AI on / off |
| **F1** | Follow (stays close to you, revives you) |
| **F2** | Hunt animals |
| **F3** | Collect pickups near you (still defends itself) |
| **F4** | Auto (hunt + collect + loot), only within 10 m of you |
| **F5** | Guard (holds this spot, shoots what comes close) |
| **F6** | Tap A on the AI's controller (menus, while the AI is off) |

## What the AI does
- **Following:** walks your exact path, catches up, stands close (both of you fit in the extraction circle),
  and revives you when you're downed.
- **Imitation:** where you jump while running (gaps, spike pits, ledges), it jumps at the same spot.
- **Combat:** aims from the real camera with target lead, shoots only on a red crosshair with a clear line of
  sight, and fights back right away when something hurts it. Humans take priority.
- **Collecting / hunting:** only near you; quiet stalking, loots bodies, skips pickups the game says are
  full. It never presses X next to a campfire, and backs out of the base camp menu if it ends up there.
- **Crafting:** crafts arrows (holds RT) when low, after checking it has materials.
- **Crypts:** near known trap parts it walks only where you walked, slowly, and never jumps.
- **Navigation:** a learned walk map + A* planner. When stuck it re-plans first, the way the game's own NPC
  navigation does (see `docs/NPC_NAV_RE.md`), and it sidesteps animals and people early.

## What the app patches (in the game copies only)
| Patch | Why |
|---|---|
| FMOD profiler off (both) | the 2nd copy can't open the same profiler port (fatal audio error) |
| No pause on focus loss (both) | the window you're not clicking on keeps running |
| Own single-instance lock (AI copy) | lets two copies run at once |
| Reads controller 2 (AI copy) | the AI's game uses the virtual pad, yours uses your pad |
| Skip broken leaderboard entries (AI copy) | the Steam emulator sends bad leaderboard data, which crashed the game at the pause menu and at the end screen |

Each patch is checked against the exact bytes first. On another game version the app stops with a message.

## Known issues
- Aiming at fast-moving animals is weak (many shots per kill).
- Crypt traps are only partly known (ceiling spikes yes; pressure plates and spike pits not yet).
- English only (menus are read with OCR).
- The AI's pause-menu "Friends' Scores" list is empty (on purpose, see the patch table).
- Two full copies of the game need a lot of memory: close other programs (16 GB RAM recommended).

## Troubleshooting
| Problem | Fix |
|---|---|
| Red message about the emulator files | point the 2nd folder to Nucleus' `utils` folder |
| "pattern found 0x" when preparing | different game version: not supported yet |
| AI's game doesn't react | start the AI partner **before** the games; plug in your own pad first |
| The game stops when the virtual pad connects | click the game window once |

## For developers
- `bot/coop_gui.py`: the app. `bot/coop_launcher.py`: game copies, patches, emulator settings, launching.
- `bot/brain.py`: decisions (FOLLOW, CATCHUP, HUNT, STALK, COMBAT, DANGER, COLLECT, PICKUP, LOOT, CRAFT,
  UNSTUCK...). `bot/run_bot.py`: reads the games, screen checks, virtual pad, F-keys, status window.
- `bot/navmap.py`: walk map + A*. `bot/enemies.py`, `bot/health.py`: memory readers.
- `bot/sim_test.py`, `bot/nav_sim.py`: offline tests (no game needed).

## Credits
- [ViGEmBus](https://github.com/nefarius/ViGEmBus) + [vgamepad](https://github.com/yannbouteiller/vgamepad),
  [dxcam](https://github.com/ra1nty/DXcam), [winocr](https://github.com/GitHub30/winocr), pymem, customtkinter.
- The Goldberg Steam Emulator and Nemirtinga's Epic emulator come from your Nucleus Co-op install. They are not
  part of this repository. Thanks to the Nucleus Co-op team.

## Disclaimer
Fan project, not affiliated with Crystal Dynamics or Square Enix. "Tomb Raider" and "Lara Croft" are their
trademarks. You need your own copy of the game. No game files are included or distributed. For local co-op fun
only: please don't submit bot-made scores to leaderboards.

## License
MIT, see [LICENSE](LICENSE).
