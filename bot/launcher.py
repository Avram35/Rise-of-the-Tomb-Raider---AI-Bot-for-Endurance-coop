"""ROTTR AI Partner - control panel.   Double-click 'ROTTR Bot.bat' (or: venv\\Scripts\\pythonw launcher.py)

Sidebar pages: Play (bot modes), Diagnostics (memory / screen checks), Tools (calibration, tests),
Clips & Map (shot / stuck / kill videos, learned walk map). A status bar shows the game, controllers, Lara's HP
and the running tool; every check runs in a background thread so the window never freezes.
"""
import ctypes
import json
import os
import queue
import re
import subprocess
import threading
import time

import customtkinter as ctk

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
NUCLEUS = r"C:\Nucleus\NucleusCoop.exe"
CLIPS = os.path.join(HERE, "clips")
MAP = os.path.join(HERE, "maps", "endurance.json")

# colours
BG, PANEL, CARD, CARD_HI = "#0f1115", "#161a21", "#1d222b", "#252b36"
TEXT, MUTED, ACCENT, ACCENT_HI = "#e8eaed", "#8b93a1", "#d4a24c", "#e6b864"
OK, WARN, BAD, INFO = "#4caf7a", "#e0b341", "#e0565b", "#5aa9e6"

# page -> list of cards: (title, description, script args or special action, options)
PAGES = {
    "Play": [
        ("Co-op partner", "Two Nucleus instances: the bot drives Player 2 (follows, revives, fights). "
         "Start this FIRST, then Nucleus. F8 = on/off, F6 = tap A.", ["run_bot.py"], {}),
        ("Co-op, no combat", "Same as co-op partner, but only follows and revives.", ["run_bot.py", "--no-combat"], {}),
    ],
    "Diagnostics": [
        ("Objects near Lara", "Every object within N metres, grouped by type. Stand next to a berry bush, a supply "
         "box or a campfire to learn its type number.", "census", {"metres": "8"}),
        ("Enemies & animals", "Live list of animals and humans within 60 m (distance, HP, type).", ["enemies.py"], {}),
        ("Health", "Live health of every running game (P1 / P2).", ["health.py"], {}),
        ("Record health (3 min)", "Saves health_log.csv + health_dump.bin.", ["health.py", "record", "180"], {}),
    ],
    "Tools": [
        ("Find camera in memory", "Re-run only after a game update: scans memory while turning the camera.",
         ["camscan.py"], {}),
        ("Brain tests", "Offline tests of follow / revive / combat / hunt logic.", ["sim_test.py"], {}),
        ("Obstacle courses", "Walk map + path planner on 7 simulated courses (old vs new brain).", ["nav_sim.py"], {}),
        ("Demo (no game)", "Simulated world with the bot status window.", ["run_bot.py", "--demo"], {}),
    ],
}
LOG_RULES = [   # first match wins; whole words only (so "fired" is not "red", "aim error" in a test is fine)
    ("bad", re.compile(r"Traceback|Error:|\bexception\b|exit code [1-9]", re.I)),
    ("ok", re.compile(r"\bdown at\b|\bkill\b|\bPASSED\b|\bOK\b|\bsaved\b|finished \(ok\)")),
    ("warn", re.compile(r"\bunreachable\b|\bstuck\b|\brestarting\b|\blost\b|\bFAILED\b|hunger=red|giving up", re.I)),
    ("info", re.compile(r"new target|HUMAN threat|hunger=|walk map", re.I)),
]
ICONS = {"Play": "▶", "Diagnostics": "◎", "Tools": "⚙", "Clips & Map": "▣"}


def xinput_slots():
    try:
        x = ctypes.windll.xinput1_4

        class S(ctypes.Structure):
            _fields_ = [("p", ctypes.c_ulong), ("b", ctypes.c_ushort), ("t", ctypes.c_ubyte * 2), ("s", ctypes.c_short * 4)]
        return [i for i in range(4) if x.XInputGetState(i, ctypes.byref(S())) == 0]
    except Exception:
        return []


class Pill(ctk.CTkFrame):
    """small status chip: coloured dot + label"""
    def __init__(self, master, label):
        super().__init__(master, fg_color=CARD, corner_radius=14, height=30)
        self.dot = ctk.CTkLabel(self, text="●", text_color=MUTED, font=("Segoe UI", 13), width=14)
        self.dot.pack(side="left", padx=(10, 4))
        self.lbl = ctk.CTkLabel(self, text=label, text_color=TEXT, font=("Segoe UI", 12))
        self.lbl.pack(side="left", padx=(0, 12))

    def set(self, text, color):
        self.lbl.configure(text=text)
        self.dot.configure(text_color=color)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("ROTTR AI Partner")
        self.geometry("1240x840")
        self.minsize(980, 620)
        self.configure(fg_color=BG)
        self.proc, self.current, self.started = None, None, 0.0
        self.q = queue.Queue()
        self.status_q = queue.Queue()
        self.pages = {}
        self._build()
        self.show("Play")
        self.after(80, self._pump)
        threading.Thread(target=self._status_loop, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------ layout
    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, width=220)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        ctk.CTkLabel(side, text="ROTTR", font=("Segoe UI Black", 26), text_color=ACCENT).pack(anchor="w", padx=22, pady=(24, 0))
        ctk.CTkLabel(side, text="AI Partner", font=("Segoe UI", 15), text_color=MUTED).pack(anchor="w", padx=22, pady=(0, 22))
        self.nav = {}
        for name in list(PAGES) + ["Clips & Map"]:
            b = ctk.CTkButton(side, text=f"  {ICONS[name]}   {name}", anchor="w", height=42, corner_radius=10,
                              font=("Segoe UI Semibold", 14), fg_color="transparent", text_color=TEXT,
                              hover_color=CARD_HI, command=lambda n=name: self.show(n))
            b.pack(fill="x", padx=12, pady=3)
            self.nav[name] = b
        ctk.CTkFrame(side, fg_color="transparent").pack(fill="both", expand=True)
        ctk.CTkButton(side, text="Open Nucleus Co-op", height=38, corner_radius=10, fg_color=CARD,
                      hover_color=CARD_HI, text_color=TEXT, command=self.open_nucleus).pack(fill="x", padx=12, pady=4)
        ctk.CTkButton(side, text="Open bot folder", height=38, corner_radius=10, fg_color=CARD,
                      hover_color=CARD_HI, text_color=TEXT, command=lambda: os.startfile(HERE)).pack(fill="x", padx=12,
                                                                                                    pady=(4, 18))

        main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=5)
        main.grid_rowconfigure(2, weight=2, minsize=170)

        # status bar
        bar = ctk.CTkFrame(main, fg_color=BG)
        bar.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 8))
        self.pill_game = Pill(bar, "Game: checking…")
        self.pill_pad = Pill(bar, "Controllers: …")
        self.pill_hp = Pill(bar, "Lara: –")
        self.pill_run = Pill(bar, "Idle")
        for p in (self.pill_game, self.pill_pad, self.pill_hp, self.pill_run):
            p.pack(side="left", padx=(0, 8))
        self.stop_btn = ctk.CTkButton(bar, text="■  Stop", width=96, height=32, corner_radius=10, fg_color=CARD,
                                      hover_color="#c2444a", font=("Segoe UI Semibold", 13), command=self.stop,
                                      state="disabled")
        self.stop_btn.pack(side="right")

        # page area
        self.page_host = ctk.CTkFrame(main, fg_color=BG)
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=20)
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)
        for name, cards in PAGES.items():
            self.pages[name] = self._card_page(name, cards)
        self.pages["Clips & Map"] = self._clips_page()

        # log
        logbox = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=14)
        logbox.grid(row=2, column=0, sticky="nsew", padx=20, pady=(10, 18))
        logbox.grid_columnconfigure(0, weight=1)
        logbox.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(logbox, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        ctk.CTkLabel(head, text="Output", font=("Segoe UI Semibold", 14), text_color=TEXT).pack(side="left")
        self.autoscroll = ctk.BooleanVar(value=True)
        ctk.CTkButton(head, text="Clear", width=64, height=26, corner_radius=8, fg_color=CARD, hover_color=CARD_HI,
                      command=lambda: self.log.delete("1.0", "end")).pack(side="right")
        ctk.CTkSwitch(head, text="Auto-scroll", variable=self.autoscroll, text_color=MUTED, progress_color=ACCENT,
                      font=("Segoe UI", 12)).pack(side="right", padx=12)
        self.log = ctk.CTkTextbox(logbox, fg_color="#0b0d11", text_color=TEXT, font=("Cascadia Mono", 12),
                                  corner_radius=10, wrap="none")
        self.log.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        for tag, col in (("ok", OK), ("warn", WARN), ("bad", BAD), ("info", INFO), ("head", ACCENT), ("dim", MUTED)):
            self.log.tag_config(tag, foreground=col)
        self.write("Ready. Pick an action on the left.\n", "dim")

    def _card_page(self, name, cards):
        page = ctk.CTkScrollableFrame(self.page_host, fg_color=BG, corner_radius=0)
        page.grid_columnconfigure((0, 1), weight=1, uniform="c")
        ctk.CTkLabel(page, text=name, font=("Segoe UI Semibold", 22), text_color=TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(4, 10))
        r, col = 1, 0
        for i, (title, desc, action, opts) in enumerate(cards):
            hero = i == 0 and name == "Play"
            card = ctk.CTkFrame(page, fg_color=CARD, corner_radius=14, border_width=2 if hero else 0,
                                border_color=ACCENT)
            if hero:
                card.grid(row=r, column=0, columnspan=2, sticky="nsew", pady=(0, 10))
                r += 1
            else:
                card.grid(row=r, column=col, sticky="nsew", padx=(0, 10) if col == 0 else (0, 0), pady=(0, 10))
                col ^= 1
                r += col == 0
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(1, weight=1)
            ctk.CTkLabel(card, text=("★  " if hero else "") + title, font=("Segoe UI Semibold", 18 if hero else 16),
                         text_color=ACCENT if hero else TEXT).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))
            d = ctk.CTkLabel(card, text=desc, font=("Segoe UI", 12), text_color=MUTED, justify="left", anchor="nw")
            d.grid(row=1, column=0, sticky="nsew", padx=16)
            card.bind("<Configure>", lambda e, lab=d: lab.configure(wraplength=max(200, e.width - 40)))
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 14))
            entries = {}
            for k, v in opts.items():
                ctk.CTkLabel(row, text=k, font=("Segoe UI", 12), text_color=MUTED).pack(side="left", padx=(0, 4))
                e = ctk.CTkEntry(row, width=90 if k != "wander" else 110, height=30, corner_radius=8, fg_color=BG,
                                 border_color=CARD_HI)
                e.insert(0, v)
                e.pack(side="left", padx=(0, 10))
                entries[k] = e
            ctk.CTkButton(row, text="Run" if not hero else "▶  Start hunting", width=150 if hero else 86, height=34,
                          corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HI,
                          text_color="#1a1a1a", font=("Segoe UI Semibold", 13),
                          command=lambda a=action, t=title, en=entries: self.run_action(a, t, en)).pack(side="right")
        return page

    def _clips_page(self):
        page = ctk.CTkFrame(self.page_host, fg_color=BG)
        page.grid_columnconfigure(0, weight=3)
        page.grid_columnconfigure(1, weight=2)
        page.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(page, text="Clips & Map", font=("Segoe UI Semibold", 22), text_color=TEXT).grid(
            row=0, column=0, sticky="w", pady=(4, 10))
        self.clip_list = ctk.CTkScrollableFrame(page, fg_color=CARD, corner_radius=14, label_text="Latest clips",
                                                label_fg_color=CARD_HI, label_text_color=TEXT)
        self.clip_list.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        side = ctk.CTkFrame(page, fg_color=CARD, corner_radius=14)
        side.grid(row=1, column=1, sticky="nsew")
        ctk.CTkLabel(side, text="Walk map", font=("Segoe UI Semibold", 16), text_color=TEXT).pack(anchor="w", padx=16,
                                                                                                pady=(14, 4))
        self.map_lbl = ctk.CTkLabel(side, text="–", font=("Segoe UI", 13), text_color=MUTED, justify="left")
        self.map_lbl.pack(anchor="w", padx=16)
        ctk.CTkLabel(side, text="The bot learns where it can walk (1 m squares). Reset if the map is from a different "
                     "area or looks wrong.", font=("Segoe UI", 12), text_color=MUTED, wraplength=280,
                     justify="left").pack(anchor="w", padx=16, pady=(8, 10))
        ctk.CTkButton(side, text="Reset walk map", height=34, corner_radius=10, fg_color=CARD_HI, hover_color=BAD,
                      command=self.reset_map).pack(anchor="w", padx=16)
        ctk.CTkButton(side, text="Open clips folder", height=34, corner_radius=10, fg_color=CARD_HI,
                      hover_color=CARD_HI, command=lambda: (os.makedirs(CLIPS, exist_ok=True), os.startfile(CLIPS))
                      ).pack(anchor="w", padx=16, pady=10)
        return page

    def show(self, name):
        for n, p in self.pages.items():
            p.grid_forget()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        for n, b in self.nav.items():
            b.configure(fg_color=CARD_HI if n == name else "transparent", text_color=ACCENT if n == name else TEXT)
        if name == "Clips & Map":
            self.refresh_clips()

    # ------------------------------------------------------------------ actions
    def run_action(self, action, title, entries):
        if action == "census":
            args = ["census.py", "near", entries["metres"].get().strip() or "8"]
        else:
            args = action
        self.run(args, title)

    def run(self, args, title):
        if self.proc and self.proc.poll() is None:
            self.write(f"'{self.current}' is still running - press Stop first.\n", "warn")
            return
        self.current, self.started = title, time.time()
        self.write(f"\n━━ {title} ━━  {' '.join(args)}\n", "head")
        self.proc = subprocess.Popen([PY, "-u"] + args, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, bufsize=1, encoding="utf-8", errors="replace",
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        self.stop_btn.configure(state="normal", fg_color=BAD)
        if args and args[0] == "run_bot.py":
            self.write("Tip: the game loses focus when the virtual pad connects - click once into the game.\n", "info")

    def _reader(self, proc):
        for line in proc.stdout:
            self.q.put(line)
        proc.wait()
        self.q.put(("__done__", proc.returncode))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.write("Stopping…\n", "warn")

    def open_nucleus(self):
        if os.path.exists(NUCLEUS):
            os.startfile(NUCLEUS)
            self.write("Nucleus started (accept the admin prompt).\n", "info")
        else:
            self.write(f"{NUCLEUS} not found\n", "bad")

    def reset_map(self):
        if os.path.exists(MAP):
            os.replace(MAP, MAP + ".old")
            self.write("Walk map reset (old one kept as endurance.json.old).\n", "warn")
        self.refresh_clips()

    def refresh_clips(self):
        for w in self.clip_list.winfo_children():
            w.destroy()
        files = sorted((f for f in os.listdir(CLIPS) if f.endswith(".mp4")), reverse=True)[:60] if os.path.isdir(CLIPS) else []
        if not files:
            ctk.CTkLabel(self.clip_list, text="No clips yet - they appear after a hunt round (every shot, stuck "
                         "moment and kill).", text_color=MUTED, wraplength=420, justify="left").pack(anchor="w", padx=8, pady=8)
        for f in files:
            meta = {}
            try:
                meta = json.load(open(os.path.join(CLIPS, f[:-4] + ".json")))
            except (OSError, ValueError):
                pass
            kind = f.split("_", 2)[-1][:-4]
            col = OK if kind == "kill" else (WARN if kind == "stuck" else INFO)
            row = ctk.CTkFrame(self.clip_list, fg_color=CARD_HI, corner_radius=10)
            row.pack(fill="x", padx=6, pady=3)
            ctk.CTkLabel(row, text="●", text_color=col, width=16).pack(side="left", padx=(10, 4))
            info = f"{f[:2]}:{f[2:4]}:{f[4:6]}   {kind}"
            if meta.get("target_type"):
                info += f"   type {meta['target_type']}  hp {meta.get('target_hp')}  {meta.get('target_dist_m')} m"
            ctk.CTkLabel(row, text=info, font=("Segoe UI", 12), text_color=TEXT).pack(side="left", pady=6)
            ctk.CTkButton(row, text="Play", width=60, height=26, corner_radius=8, fg_color=ACCENT, hover_color=ACCENT_HI,
                          text_color="#1a1a1a", command=lambda p=os.path.join(CLIPS, f): os.startfile(p)).pack(
                side="right", padx=8)
        try:
            d = json.load(open(MAP))
            self.map_lbl.configure(text=f"{len(d.get('walk', []))} walked squares\n{len(d.get('block', []))} blocked "
                                        f"squares\nsaved {time.strftime('%H:%M', time.localtime(os.path.getmtime(MAP)))}")
        except (OSError, ValueError):
            self.map_lbl.configure(text="no map yet")

    # ------------------------------------------------------------------ log + status
    def write(self, text, tag=None):
        if tag is None:
            tag = next((t for t, rx in LOG_RULES if rx.search(text)), None)
        self.log.insert("end", text, tag)
        if int(self.log.index("end-1c").split(".")[0]) > 4000:
            self.log.delete("1.0", "1500.0")
        if self.autoscroll.get():
            self.log.see("end")

    def _pump(self):
        n = 0
        try:
            while n < 200:
                item = self.q.get_nowait()
                n += 1
                if isinstance(item, tuple):
                    code = item[1]
                    self.write(f"━━ finished ({'ok' if code == 0 else f'exit code {code}'}) ━━\n",
                               "ok" if code == 0 else "warn")
                    self.stop_btn.configure(state="disabled", fg_color=CARD)
                    self.current = None
                    if self.pages["Clips & Map"].winfo_ismapped():
                        self.refresh_clips()
                else:
                    self.write(item)
        except queue.Empty:
            pass
        try:
            while True:
                s = self.status_q.get_nowait()
                self.pill_game.set(*s["game"])
                self.pill_pad.set(*s["pads"])
                self.pill_hp.set(*s["hp"])
        except queue.Empty:
            pass
        if self.current:
            m, sec = divmod(int(time.time() - self.started), 60)
            self.pill_run.set(f"{self.current}  {m}:{sec:02d}", OK)
        else:
            self.pill_run.set("Idle", MUTED)
        self.after(80, self._pump)

    def _status_loop(self):
        import psutil
        reader, pid = None, None
        while True:
            try:
                procs = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "rottr.exe"]
                game = (f"Game: {len(procs)} running", OK) if procs else ("Game: not running", MUTED)
                slots = xinput_slots()
                pads = (f"Controllers: slot {', '.join(map(str, slots))}", OK if slots == [0] else WARN) \
                    if slots else ("Controllers: none", MUTED)
                hp = ("Lara: –", MUTED)
                if len(procs) == 1:
                    if pid != procs[0].pid:
                        from health import Health
                        from probe_players import Reader
                        reader, pid = Health(Reader(procs[0].pid)), procs[0].pid
                    v = reader.read()
                    if v:
                        pct = v["pct"]
                        hp = (f"Lara: {v['health']}/{v['max']} HP", OK if pct > 0.6 else WARN if pct > 0.25 else BAD)
                self.status_q.put({"game": game, "pads": pads, "hp": hp})
            except Exception:
                reader, pid = None, None
            time.sleep(1.5)

    def close(self):
        self.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
