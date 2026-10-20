"""Find the camera's look direction (unit vector x, y, z) in ROTTR's memory.  One game, read-only + virtual pad.

1. collect every float triple with length 1 (candidate direction vectors) in readable/writable memory
2. move the camera with the virtual pad's right stick (up, down, right, left) and keep only the candidates
   that change exactly like a camera would (up/down -> z changes, right/left -> heading changes, z stays)
3. print survivors + save camscan_result.json; also looks for a pointer to them inside ROTTR.exe

    venv\\Scripts\\python camscan.py      (click into the game during the countdown, hands off ~20 s)
"""
import ctypes
import ctypes.wintypes as wt
import json
import math
import sys
import time

import numpy as np
import psutil
import vgamepad as vg

from probe_players import Reader, k32


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p), ("AllocationProtect", wt.DWORD),
                ("PartitionId", wt.WORD), ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD)]


k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


def regions(r):
    addr, out = 0, []
    mbi = MBI()
    while addr < 0x7FFFFFFFFFFF and k32.VirtualQueryEx(r.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if mbi.State == 0x1000 and mbi.Protect in (0x04, 0x40) and size <= 512 << 20:   # committed, RW / RWX
            out.append((base, size))
        addr = base + size
    return out


def read_floats(r, base, size):
    b = r.read(base, size)
    return None if b is None else np.frombuffer(b, dtype=np.float32)


def collect(r):
    """-> int64 array of addresses of unit-length float triples (4-byte aligned)"""
    found, total = [], 0
    for base, size in regions(r):
        f = read_floats(r, base, size)
        if f is None or len(f) < 3:
            continue
        total += size
        a, b, c = f[:-2], f[1:-1], f[2:]
        with np.errstate(all="ignore"):
            n = a * a + b * b + c * c
            m = (np.abs(n - 1.0) < 2e-3) & (np.abs(c) < 0.97) & (np.hypot(a, b) > 0.2) & (a != b) & (b != c)
        idx = np.nonzero(m)[0]
        if len(idx):
            found.append(base + idx.astype(np.int64) * 4)
    print(f"scanned {total / 2**20:.0f} MB")
    return np.concatenate(found) if found else np.zeros(0, np.int64)


def sample(r, addrs):
    """read (x, y, z) at each address; groups by 64 KB page to keep the number of reads small"""
    out = np.full((len(addrs), 3), np.nan, np.float32)
    pages = addrs >> 16
    order = np.argsort(pages, kind="stable")
    sp = pages[order]
    starts = np.r_[0, np.nonzero(np.diff(sp))[0] + 1]
    ends = np.r_[starts[1:], len(sp)]
    for s, e in zip(starts, ends):
        ids = order[s:e]
        lo = int(addrs[ids].min())
        hi = int(addrs[ids].max()) + 12
        f = r.read(lo, hi - lo)
        if f is None:
            continue
        offs = (addrs[ids] - lo).astype(np.int64)
        for k in range(3):
            out[ids, k] = _gather(f, offs + 4 * k)
    return out


def _gather(buf, offs):
    raw = np.frombuffer(buf + b"\0" * 8, dtype=np.uint8)
    idx = offs[:, None] + np.arange(4)[None, :]
    return raw[idx].copy().view(np.float32).ravel()


def heading(v):
    return np.degrees(np.arctan2(v[:, 1], v[:, 0]))


def main():
    procs = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "rottr.exe"]
    if len(procs) != 1:
        raise SystemExit(f"need exactly one ROTTR.exe (found {len(procs)})")
    r = Reader(procs[0].pid)
    pad = vg.VX360Gamepad()
    print("collecting unit vectors (takes a few seconds)...")
    t0 = time.time()
    addrs = collect(r)
    print(f"{len(addrs):,} candidates in {time.time() - t0:.1f} s")
    print("starting in 5 s - click into the game window, then hands off")
    time.sleep(5)

    def stick(rx, ry, secs):
        pad.right_joystick_float(x_value_float=rx, y_value_float=ry)
        pad.update()
        time.sleep(secs)
        pad.right_joystick_float(x_value_float=0.0, y_value_float=0.0)
        pad.update()
        time.sleep(0.4)                      # let the camera settle

    def keep(mask, what):
        nonlocal addrs, cur
        addrs, cur = addrs[mask], cur[mask]
        print(f"  after {what:16s}: {len(addrs):,} left")
        if len(addrs) == 0:
            pad.reset(); pad.update()
            raise SystemExit("no candidates left")

    stick(0, 0, 0.1)
    cur = sample(r, addrs)
    for rnd in range(2):
        prev = cur
        time.sleep(0.3)
        cur = sample(r, addrs)
        keep(np.all(np.abs(cur - prev) < 1e-4, axis=1), "still")                 # camera at rest -> no change
        prev = cur
        stick(0, 1.0, 0.35)                                                       # look up
        cur = sample(r, addrs)
        dz_up = cur[:, 2] - prev[:, 2]
        keep(np.abs(dz_up) > 0.08, "look up")
        dz_up = dz_up[np.abs(dz_up) > 0.08]
        prev = cur
        stick(0, -1.0, 0.35)                                                      # look down
        cur = sample(r, addrs)
        dz_dn = cur[:, 2] - prev[:, 2]
        keep(np.sign(dz_dn) == -np.sign(dz_up), "look down")
        prev = cur
        stick(1.0, 0, 0.3)                                                        # turn right (~23 deg)
        cur = sample(r, addrs)
        dh = (heading(cur) - heading(prev) + 180) % 360 - 180
        print(f"  turn right dh sample: {np.round(dh[:12], 1)}  dz {np.round((cur[:, 2] - prev[:, 2])[:12], 3)}")
        mr = (np.abs(dh) > 5) & (np.abs(dh) < 170) & (np.abs(cur[:, 2] - prev[:, 2]) < 0.1)
        dh_r = dh[mr]
        keep(mr, "turn right")
        prev = cur
        stick(-1.0, 0, 0.3)                                                       # turn left
        cur = sample(r, addrs)
        dh = (heading(cur) - heading(prev) + 180) % 360 - 180
        keep((np.sign(dh) == -np.sign(dh_r)) & (np.abs(dh) > 5), "turn left")
        if len(addrs) == 0:
            break
    pad.reset()
    pad.update()

    res = []
    for a, v in zip(addrs[:40], cur[:40]):
        a = int(a)
        rel = a - r.base
        where = f"ROTTR.exe+{rel:#x}" if 0 <= rel < 0x4000000 else "heap"
        res.append({"addr": a, "where": where, "v": [float(x) for x in v],
                    "heading_deg": float(math.degrees(math.atan2(v[1], v[0]))),
                    "pitch_deg": float(math.degrees(math.asin(max(-1, min(1, v[2])))))})
        print(f"  {a:#x}  {where:22s} heading {res[-1]['heading_deg']:7.1f}  pitch {res[-1]['pitch_deg']:6.1f}")
    json.dump({"pid": procs[0].pid, "base": r.base, "found": res}, open("camscan_result.json", "w"), indent=1)
    print(f"saved camscan_result.json ({len(res)} survivors)")


if __name__ == "__main__":
    main()
