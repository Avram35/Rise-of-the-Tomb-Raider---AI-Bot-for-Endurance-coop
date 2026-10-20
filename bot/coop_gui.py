"""ROTTR Endurance co-op AI partner: one window for everything.

  1. Prepare game copies   (instances P1 / P2, patched exe copies, emulator files)  - once, and after game updates
  2. Start AI partner      (run_bot.py: creates the virtual controller, waits for the games)
  3. Start both games      (your game + the AI's 800x600 game)
Then host a co-op expedition in your game and join it from the AI's game; F1 + F8 switch the AI on.
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter.filedialog as fd

import customtkinter as ctk

import coop_launcher as cl

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

KEYS = ("F8  AI on / off        F1  follow (stay with you, revive)\n"
        "F2  hunt animals       F3  collect pickups near you\n"
        "F4  auto (all of it, only within 10 m of you)      F5  guard this spot\n"
        "F6  tap A on the AI's controller (menus, while the AI is off)")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("ROTTR Co-op AI Partner")
        self.geometry("820x700")
        self.minsize(700, 560)
        self.cfg = cl.load_config()
        self.bot = None
        self.q = queue.Queue()

        ctk.CTkLabel(self, text="Rise of the Tomb Raider - Endurance co-op AI partner",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(14, 2))
        ctk.CTkLabel(self, text="You play Player 1. The AI plays Player 2 (Lara Croft) in a small window.",
                     text_color="gray70").pack()

        box = ctk.CTkFrame(self)
        box.pack(fill="x", padx=16, pady=12)
        self.entries = {}
        for row, (key, label) in enumerate((("game", "Game folder (with ROTTR.exe)"),
                                            ("emulators", "Emulator files (Nucleus 'utils' folder)"),
                                            ("instances", "Game copies folder (same drive as the game)"))):
            ctk.CTkLabel(box, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=10, pady=4)
            e = ctk.CTkEntry(box, width=420)
            e.insert(0, self.cfg.get(key, ""))
            e.grid(row=row, column=1, sticky="ew", padx=4, pady=4)
            ctk.CTkButton(box, text="...", width=36, command=lambda k=key: self.browse(k)).grid(row=row, column=2,
                                                                                                padx=(0, 10))
            self.entries[key] = e
        box.grid_columnconfigure(1, weight=1)
        self.status = ctk.CTkLabel(self, text="", anchor="w", justify="left")
        self.status.pack(fill="x", padx=20)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=8)
        self.b_build = ctk.CTkButton(btns, text="1.  Prepare game copies", command=self.build, height=40)
        self.b_bot = ctk.CTkButton(btns, text="2.  Start AI partner", command=self.start_bot, height=40)
        self.b_games = ctk.CTkButton(btns, text="3.  Start both games", command=self.start_games, height=40)
        for i, b in enumerate((self.b_build, self.b_bot, self.b_games)):
            b.grid(row=0, column=i, padx=4, sticky="ew")
            btns.grid_columnconfigure(i, weight=1)
        opt = ctk.CTkFrame(self, fg_color="transparent")
        opt.pack(fill="x", padx=16)
        self.no_combat = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opt, text="AI only follows + revives (no shooting)", variable=self.no_combat).pack(side="left")
        ctk.CTkButton(opt, text="Stop games", width=110, fg_color="gray30", command=self.stop_games).pack(side="right")
        ctk.CTkButton(opt, text="Stop AI", width=90, fg_color="gray30", command=self.stop_bot).pack(side="right", padx=6)

        ctk.CTkLabel(self, text=KEYS, justify="left", anchor="w", font=ctk.CTkFont(family="Consolas", size=12),
                     text_color="gray75").pack(fill="x", padx=20, pady=(10, 4))
        self.live = ctk.CTkLabel(self, text="AI partner: not running", justify="left", anchor="w",
                                 font=ctk.CTkFont(family="Consolas", size=13), fg_color="gray17", corner_radius=6,
                                 height=96)
        self.live.pack(fill="x", padx=16, pady=(4, 2))
        self.log = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12))
        self.log.pack(fill="both", expand=True, padx=16, pady=(4, 14))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_status()
        self.after(150, self.pump)

    # ---------------------------------------------------------------- helpers
    def current(self):
        cfg = {k: e.get().strip() for k, e in self.entries.items()}
        cl.save_config(cfg)
        return cfg

    def browse(self, key):
        d = fd.askdirectory(initialdir=self.entries[key].get() or "C:\\")
        if d:
            self.entries[key].delete(0, "end")
            self.entries[key].insert(0, os.path.normpath(d))
            self.refresh_status()

    def refresh_status(self):
        cfg = self.current()
        problems = cl.check(cfg)
        built = os.path.exists(os.path.join(cfg.get("instances", ""), "P2", "ROTTR.exe"))
        if problems:
            self.status.configure(text="\n".join("! " + p for p in problems), text_color="#ff8a80")
        else:
            self.status.configure(text="Ready." + ("" if built else "  Press 1 first (once)."), text_color="#9be7a0")

    def say(self, line):
        self.q.put(line if line.endswith("\n") else line + "\n")

    def pump(self):
        try:
            while True:
                self.log.insert("end", self.q.get_nowait())
                self.log.see("end")
        except queue.Empty:
            pass
        if self.bot is not None and self.bot.poll() is not None:
            self.say(f"AI partner stopped (exit code {self.bot.returncode})")
            self.bot = None
        self.b_bot.configure(text="2.  AI partner running" if self.bot else "2.  Start AI partner")
        self.show_live()
        self.after(150, self.pump)

    def show_live(self):
        """the AI's status (written by run_bot.py to status.json), shown here instead of its floating window"""
        if self.bot is None:
            self.live.configure(text="  AI partner: not running", text_color="gray60")
            return
        try:
            import json
            import time
            st = json.load(open(os.path.join(HERE, "status.json")))
            if time.time() - st["t"] > 3:
                raise ValueError
            on = "ON" if st["on"] else "OFF (F8)"
            txt = (f"  AI: {on}    mode {st['mode'].upper()}    state {st['state']}\n"
                   f"  {st['info'][:90]}\n"
                   f"  distance to you {st['dist_m']:.1f} m" + ("    YOU ARE DOWN - coming" if st["p1_downed"] else ""))
            self.live.configure(text=txt, text_color="#9be7a0" if st["on"] else "gray80")
        except (OSError, ValueError, KeyError):
            self.live.configure(text="  AI partner: waiting for both games...", text_color="gray70")

    def run_bg(self, title, fn):
        def job():
            self.say(f"--- {title}")
            try:
                import contextlib
                import io

                class W(io.TextIOBase):
                    def write(s, t):
                        if t.strip():
                            self.say(t.rstrip())
                        return len(t)
                with contextlib.redirect_stdout(W()):
                    fn()
                self.say(f"--- {title}: done")
            except SystemExit as ex:
                self.say(f"--- {title}: {ex}")
            except Exception as ex:
                self.say(f"--- {title} failed: {ex}")
            self.after(0, self.refresh_status)
        threading.Thread(target=job, daemon=True).start()

    # ---------------------------------------------------------------- actions
    def build(self):
        cfg = self.current()
        self.run_bg("preparing game copies (first time ~1 min)", lambda: cl.build(cfg))

    def start_games(self):
        cfg = self.current()
        if self.bot is None:
            self.say("tip: start the AI partner (2) first, so its controller exists before the AI's game starts")
        self.run_bg("starting both games (~1 min)", lambda: cl.start(cfg))

    def start_bot(self):
        if self.bot is not None:
            return
        args = [PY, "-u", "run_bot.py", "--no-window"] + (["--no-combat"] if self.no_combat.get() else [])
        self.bot = subprocess.Popen(args, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, text=True, bufsize=1, encoding="utf-8",
                                    errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        self.say("--- AI partner started: it waits for both games, then F1 + F8 in the game")

        def reader(p):
            last = ""
            for line in p.stdout:
                if line.startswith("waiting for") and last.startswith("waiting for"):
                    continue                                  # don't flood the log while waiting
                last = line
                self.say(line.rstrip())
        threading.Thread(target=reader, args=(self.bot,), daemon=True).start()

    def stop_bot(self):
        if self.bot is not None:
            self.bot.terminate()

    def stop_games(self):
        inst = os.path.normcase(os.path.abspath(self.current().get("instances", "")))
        try:
            import psutil
            n = 0
            for p in psutil.process_iter(["name", "exe"]):
                if (p.info["name"] or "").lower() == "rottr.exe" and \
                        os.path.normcase(p.info["exe"] or "").startswith(inst):
                    p.kill()
                    n += 1
            self.say(f"stopped {n} game(s)")
        except Exception as ex:
            self.say(f"stop failed: {ex}")

    def close(self):
        self.stop_bot()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
