# Rise of the Tomb Raider: AI Bot for Endurance co-op

An AI co-op partner for **Rise of the Tomb Raider's Endurance mode** on PC. Play Endurance co-op on **one PC**
(via [Nucleus Co-op](https://github.com/SplitScreen-Me/splitscreenme-nucleus)). The AI plays **Player 2
(Lara Croft)**: it follows you, revives you, fights with you, collects near you, and walks only in your
footsteps near crypt traps.

It reads the game's memory (positions, health, animals, pickups) plus a few screen checks (crosshair colour,
hunger/cold icons, text pop-ups), then drives a **virtual Xbox controller**. The game itself is never modified
on disk.

> Work in progress. Tested only on **Steam build 9575745** (Windows 11). Other game versions will need new memory
> addresses.

---

## Requirements
| What | Why |
|---|---|
| Rise of the Tomb Raider (Steam)| the game (you must own it) |
| Windows 10 / 11, 64-bit | Windows OCR, XInput |
| [Python 3.11+](https://www.python.org/downloads/) (64-bit, tick "Add to PATH") | runs the bot |
| [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases) | the virtual controller the AI uses |
| [Nucleus Co-op](https://github.com/SplitScreen-Me/splitscreenme-nucleus/releases) v2.4.2+ | two game instances on one PC |

## Install
1. Install Python and ViGEmBus (reboot if asked).
2. Download / clone this repository anywhere.
3. Double-click **`start.bat`**. The first start creates `bot\venv` and installs the Python packages
   (a few minutes), then opens the launcher.
4. Copy `nucleus\Rise of the Tomb Raider.js` into your `Nucleus\handlers\` folder.

Game settings: **windowed or borderless**, **English** language, HUD on, default controls.

## How to use
1. Plug in **your** controller.
2. `start.bat` → **Play → Co-op partner**. The bot creates its virtual controller and waits.
3. Start **Nucleus** → Rise of the Tomb Raider. Your pad = Player 1, the virtual pad = Player 2.
   If Player 2's window says "press a button", press **F6**.
4. Set up the co-op expedition, then press **F1** (follow) and **F8** (bot on).

### Keys (while the game runs)
| Key | Action |
|---|---|
| **F8** | AI on / off |
| **F1** | Follow mode (stay with Player 1, revive) |
| **F2** | Hunt mode (animals only) |
| **F3** | Collect mode (pickups only, still defends itself) |
| **F4** | Auto (hunt + collect + loot), only within 10 m of you |
| **F5** | Guard (hold position, shoot what comes close) |
| **F6** | Tap A on the virtual pad (menus, while the AI is off) |

## What the AI can do
- **Following:** walks your path (breadcrumbs), catches up, keeps a close follow spot, revives you when you're
  downed.
- **Combat:** aims from the real camera with target lead. Shoots only on a red crosshair with a clear line of
  sight. Danger mode: if something hurts Lara, it fights back right away. Humans take priority.
- **Hunting:** quiet stalking (no sprinting at animals), loots bodies.
- **Collecting:** walks to pickups and tries each side. Skips pickups the game says are full.
- **Crafting:** crafts arrows (holds RT) when low, and checks for materials first.
- **Navigation:** a learned walk map + A* planner. When stuck it re-plans first (the way the game's NPC navmesh
  does, see `docs/NPC_NAV_RE.md`). It only jumps where a climb is needed, and sidesteps animals/people early.
- **Crypts:** near known trap parts it only walks where you walked, slowly, and never jumps.

## Known issues
- Aiming at fast-moving animals is weak (many shots per kill).
- Crypt traps are only partly known (ceiling spikes yes; pressure plates and spike pits not yet).
- English only (menus are read with OCR).
- The game pauses each time the virtual controller connects: click the game window once.
- Player 2's game used to close at the pause menu / end screen. The handler v8 lists all Steam
  interfaces for the emulator, which should fix it (not confirmed yet).
- Only Steam build 9575745 is supported.

## Troubleshooting
| Problem | Fix |
|---|---|
| "ViGEmBus not found" / pad errors | install ViGEmBus, reboot |
| Player 2 doesn't react | start the bot **before** Nucleus; press F6 on "press a button" |
| Status window shows wrong HP / positions | different game build: addresses don't match |

## For developers
- `bot/brain.py`: decisions (states FOLLOW, CATCHUP, HUNT, STALK, COMBAT, DANGER, COLLECT, PICKUP, LOOT,
  CRAFT, UNSTUCK...)
- `bot/run_bot.py`: reads the game, screen checks, virtual pad, F-keys, status window
- `bot/navmap.py`: walk map + A*. `bot/enemies.py`, `bot/health.py`: memory readers
- `bot/sim_test.py`, `bot/nav_sim.py`: offline tests (no game needed): `venv\Scripts\python sim_test.py`

## Credits
- Nucleus handler adapted from **Talos91**'s Tomb Raider (2013) handler. Thanks to the **Nucleus Co-op** team.
- [ViGEmBus](https://github.com/nefarius/ViGEmBus) + [vgamepad](https://github.com/yannbouteiller/vgamepad),
  [dxcam](https://github.com/ra1nty/DXcam), [winocr](https://github.com/GitHub30/winocr), pymem.
- Goldberg Steam Emulator and Nemirtinga's Epic emulator are downloaded by Nucleus. They are not part of this repo.

## Disclaimer
Fan project, not affiliated with Crystal Dynamics or Square Enix. "Tomb Raider" and "Lara Croft" are their
trademarks. You need your own copy of the game. No game files are included. For local co-op fun only: please don't submit bot-made scores to leaderboards.

## License
MIT, see [LICENSE](LICENSE).
