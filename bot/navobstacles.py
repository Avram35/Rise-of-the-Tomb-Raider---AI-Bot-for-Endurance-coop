r"""The game's own navigation obstacles (bfx::BoxObstacle) -> solid cells in the bot's walk map.

Layout (Ghidra, BoxObstacle vtable slots 1-3, build 9575745):
  +0x00 vtable (ROTTR.exe+0xD12158)      +0x10 -> bfx::Planner (shared)
  +0x20 / +0x28 -> neighbouring BoxObstacles (linked)  -> walk them all from one box
  +0x58 / +0x68 / +0x78  rotation rows (3 floats + pad)   +0x88 world position (x, y, z)
  +0xD8 local min (x, y, z)   +0xE4 local max (x, y, z)    (world = R * local + pos)
No static pointer leads here, so one box is found by a memory scan (background, <= ~1 min), the rest by links.

    venv\\Scripts\\python navobstacles.py      find all boxes, print the ones near Lara
"""
import ctypes
import math
import struct
import time

import numpy as np

from camscan import MBI, k32

BOX_VT = 0xD12158


def find_one_box(r, deadline_s=120):
    """first BoxObstacle in memory (scan stops at the first hit)"""
    vt = np.uint64(r.base + BOX_VT)
    addr, mbi, t0 = 0, MBI(), time.time()
    while addr < 0x7FFFFFFFFFFF and k32.VirtualQueryEx(r.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if mbi.State == 0x1000 and (mbi.Protect & 0xEE) and not (mbi.Protect & 0x100):
            for off in range(0, size, 64 << 20):
                b = r.read(base + off, min(64 << 20, size - off))
                if not b:
                    continue
                a = np.frombuffer(b[:len(b) // 8 * 8], dtype=np.uint64)
                hit = np.nonzero(a == vt)[0]
                if len(hit):
                    return base + off + int(hit[0]) * 8
                if time.time() - t0 > deadline_s:
                    return None
        addr = base + size
    return None


def is_box(r, a):
    b = r.read(a, 8)
    return bool(b) and struct.unpack("<Q", b)[0] == r.base + BOX_VT


def all_boxes(r, start, limit=5000):
    """every BoxObstacle reachable through the +0x20 / +0x28 links"""
    seen, todo = {start}, [start]
    while todo and len(seen) < limit:
        a = todo.pop()
        b = r.read(a + 0x20, 0x10)
        if not b:
            continue
        for p in struct.unpack("<2Q", b):
            if p and p not in seen and 0x10000 < p < 0x7FFFFFFFFFFF and is_box(r, p):
                seen.add(p)
                todo.append(p)
    return sorted(seen)


def read_box(r, a):
    """-> dict(pos, corners [(x, y) x4], zmin, zmax) in world cm, or None"""
    b = r.read(a, 0xF0)
    if not b:
        return None
    rx = struct.unpack_from("<3f", b, 0x58)
    ry = struct.unpack_from("<3f", b, 0x68)
    rz = struct.unpack_from("<3f", b, 0x78)
    t = struct.unpack_from("<3f", b, 0x88)
    lo = struct.unpack_from("<3f", b, 0xD8)
    hi = struct.unpack_from("<3f", b, 0xE4)
    if not all(map(math.isfinite, rx + ry + rz + t + lo + hi)):
        return None

    def world(p):          # rows are the basis vectors: world = p.x * rx + p.y * ry + p.z * rz + t
        return tuple(p[0] * rx[k] + p[1] * ry[k] + p[2] * rz[k] + t[k] for k in range(3))
    pts = [world((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    xs, ys, zs = [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts]
    # footprint: convex hull of the 8 corners projected to the ground
    hull = _hull([(p[0], p[1]) for p in pts])
    return {"addr": a, "pos": t, "corners": hull, "zmin": min(zs), "zmax": max(zs),
            "size": (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))}


def _hull(points):
    pts = sorted(set((round(x, 1), round(y, 1)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def footprint_cells(box, cell=100.0):
    """1 m cells whose centre lies inside the box footprint (plus cells under the corners)"""
    poly = box["corners"]
    if len(poly) < 3:
        return set()
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    out = set()

    def inside(x, y):
        s = None
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            c = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if s is None:
                s = c >= 0
            elif (c >= 0) != s:
                return False
        return True
    for cx in range(int(math.floor(min(xs) / cell)), int(math.floor(max(xs) / cell)) + 1):
        for cy in range(int(math.floor(min(ys) / cell)), int(math.floor(max(ys) / cell)) + 1):
            if inside((cx + 0.5) * cell, (cy + 0.5) * cell):
                out.add((cx, cy))
    for x, y in poly:
        out.add((int(math.floor(x / cell)), int(math.floor(y / cell))))
    return out


if __name__ == "__main__":
    from bfxscan import reader
    from health import INSTANCE_RVA
    r = reader()
    t0 = time.time()
    one = find_one_box(r)
    print(f"first box after {time.time() - t0:.1f} s: {one and hex(one)}")
    if one:
        t1 = time.time()
        boxes = all_boxes(r, one)
        print(f"{len(boxes)} boxes via links in {1000 * (time.time() - t1):.0f} ms")
        lara = r.vec4(r.base + INSTANCE_RVA + 0x10)[:3]
        rows = [b for b in (read_box(r, a) for a in boxes) if b]
        rows.sort(key=lambda b: math.dist(b["pos"][:2], lara[:2]))
        for b in rows[:8]:
            print(f"  {math.dist(b['pos'][:2], lara[:2]) / 100:5.1f} m  pos {[round(v) for v in b['pos']]}  "
                  f"size {[round(v) for v in b['size']]} cm  z {b['zmin']:.0f}..{b['zmax']:.0f}  "
                  f"cells {len(footprint_cells(b))}")
