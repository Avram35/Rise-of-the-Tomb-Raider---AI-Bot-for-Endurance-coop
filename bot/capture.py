"""Continuous screen capture for the bot: always-fresh frames + short video clips around events.

Screenshots taken on demand arrived 0.5-1.5 s late (the shot was already gone). Instead a background thread grabs
the primary monitor at FPS frames per second (dxcam = Windows Desktop Duplication, ~2-5 ms per frame; mss as
fallback) and keeps the last BUFFER_S seconds in RAM at reduced size.

    cap = Capture(); cap.start()
    frame, t = cap.latest()            # numpy RGB (full res, newest frame) + its time.time()
    cap.event("shot_release", meta)    # writes clips/<time>_shot_release.mp4: 3 s before + 2 s after
    cap.stop()

Review a clip:  venv\\Scripts\\python capture.py stills clips/xxx.mp4   -> every 5th frame as PNG next to it
Self-test:      venv\\Scripts\\python capture.py test                   -> fps + a 3 s test clip of the desktop
"""
import collections
import json
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, "clips")


class Capture:
    def __init__(self, fps=30, buffer_s=4.0, clip_scale=0.5, before_s=3.0, after_s=2.0):
        self.fps, self.clip_scale = fps, clip_scale
        self.before_s, self.after_s = before_s, after_s
        self.buf = collections.deque(maxlen=int(fps * buffer_s))      # (t, small frame BGR) for clips
        self._latest = (None, 0.0)
        self._lock = threading.Lock()
        self._run = False
        self._pending = []           # clips waiting for their "after" part: [deadline, name, meta, frames]
        self.measured_fps = 0.0
        self._grab = self._make_grabber()

    def _make_grabber(self):
        try:
            import dxcam
            cam = dxcam.create(output_color="RGB")
            if cam.grab() is None:                 # dxcam returns None when nothing changed; try again later
                time.sleep(0.05)
            self.backend = "dxcam"
            last = [None]

            def grab():
                f = cam.grab()
                if f is not None:
                    last[0] = f
                return last[0]
            return grab
        except Exception:
            import mss
            sct = mss.mss()
            mon = sct.monitors[1]
            self.backend = "mss"
            return lambda: np.asarray(sct.grab(mon))[:, :, 2::-1]      # BGRA -> RGB

    def start(self):
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self):
        self._run = False
        time.sleep(0.1)
        for p in self._pending:                     # flush whatever we have
            self._write(p[1], p[2], p[3])
        self._pending = []

    def latest(self):
        with self._lock:
            return self._latest

    def _loop(self):
        import cv2
        period, n, t0 = 1.0 / self.fps, 0, time.time()
        while self._run:
            ts = time.time()
            f = self._grab()
            if f is not None:
                with self._lock:
                    self._latest = (f, ts)
                small = cv2.resize(f, None, fx=self.clip_scale, fy=self.clip_scale, interpolation=cv2.INTER_AREA)
                small = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
                self.buf.append((ts, small))
                for p in self._pending:
                    p[3].append((ts, small))
                done = [p for p in self._pending if ts >= p[0]]
                for p in done:
                    self._pending.remove(p)
                    threading.Thread(target=self._write, args=(p[1], p[2], p[3]), daemon=True).start()
                n += 1
                if ts - t0 >= 2.0:
                    self.measured_fps, n, t0 = n / (ts - t0), 0, ts
            time.sleep(max(0.0, period - (time.time() - ts)))

    def event(self, name, meta=None):
        """save a clip: before_s seconds before now + after_s seconds after (written in the background)"""
        now = time.time()
        before = [(t, f) for t, f in list(self.buf) if t >= now - self.before_s]
        stamp = time.strftime("%H%M%S", time.localtime(now)) + f"_{int(now * 1000) % 1000:03d}"
        self._pending.append([now + self.after_s, f"{stamp}_{name}", dict(meta or {}, event_t=now), before])

    def _write(self, name, meta, frames):
        import cv2
        if not frames:
            return
        os.makedirs(CLIPS, exist_ok=True)
        h, w = frames[0][1].shape[:2]
        path = os.path.join(CLIPS, name + ".mp4")
        vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
        ev = meta.get("event_t", frames[0][0])
        for t, f in frames:
            f = f.copy()
            cv2.putText(f, f"{t - ev:+.2f}s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            vw.write(f)
        vw.release()
        meta["frames"] = len(frames)
        json.dump(meta, open(os.path.join(CLIPS, name + ".json"), "w"), indent=1, default=str)


def stills(path, every=5):
    """extract every Nth frame of a clip as PNGs (for Claude to look at)"""
    import cv2
    out = os.path.splitext(path)[0] + "_frames"
    os.makedirs(out, exist_ok=True)
    v = cv2.VideoCapture(path)
    i = k = 0
    while True:
        ok, f = v.read()
        if not ok:
            break
        if i % every == 0:
            cv2.imwrite(os.path.join(out, f"{i:04d}.png"), f)
            k += 1
        i += 1
    print(f"{k} stills from {i} frames -> {out}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "stills":
        stills(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5)
    else:
        cap = Capture().start()
        time.sleep(3.5)
        f, t = cap.latest()
        print(f"backend {cap.backend}, {cap.measured_fps:.0f} fps, frame {None if f is None else f.shape}, "
              f"age {1000 * (time.time() - t):.0f} ms")
        cap.event("selftest", {"note": "desktop test"})
        time.sleep(2.5)
        cap.stop()
        time.sleep(1.5)
        print("clips:", sorted(os.listdir(CLIPS))[-2:])
