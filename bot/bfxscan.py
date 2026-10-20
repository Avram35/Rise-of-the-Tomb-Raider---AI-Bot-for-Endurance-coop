r"""Find live bfx (navigation library) objects by their vtable pointer and dump them (reverse-engineering helper).

    venv\\Scripts\\python bfxscan.py count             how many objects of each bfx class exist
    venv\\Scripts\\python bfxscan.py dump PolylinePath 3   hex/float/pointer view of the first 3 objects
Vtable RVAs come from Ghidra RTTI (ghidra/classes_nav.txt), build 9575745.
"""
import struct
import sys

import numpy as np
import psutil

from camscan import regions
from probe_players import Reader

VT = {"AreaHandlePath": 0xd12048, "BoxObstacle": 0xd12158, "ConvexObstacle": 0xd12198, "MoverImpl": 0xd11048,
      "MoverImpl2": 0xd11070, "MoverSpaceComponent": 0xd11090, "ObstacleImpl": 0xd12118, "Path": 0xd12028,
      "Planner": 0xd12078, "PlannerInstance": 0xd120a8, "PolylinePath": 0xd12068}


def reader():
    p = [q for q in psutil.process_iter(["name"]) if (q.info["name"] or "").lower() == "rottr.exe"][0]
    return Reader(p.pid)


def find_objects(r, names=None):
    """{class: [object addresses]} - qword-aligned slots holding the class vtable address"""
    want = {r.base + rva: n for n, rva in VT.items() if names is None or n in names}
    keys = np.array(sorted(want), dtype=np.uint64)
    out = {n: [] for n in want.values()}
    for base, size in regions(r):
        b = r.read(base, size)
        if not b:
            continue
        a = np.frombuffer(b[:len(b) // 8 * 8], dtype=np.uint64)
        hit = np.nonzero(np.isin(a, keys))[0]
        for i in hit:
            out[want[int(a[i])]].append(base + int(i) * 8)
    return out


def dump(r, addr, size=0x100):
    b = r.read(addr, size)
    if not b:
        print("  unreadable")
        return
    for o in range(0, size, 16):
        q = struct.unpack_from("<2Q", b, o)
        f = struct.unpack_from("<4f", b, o)
        i = struct.unpack_from("<4i", b, o)
        fs = " ".join(f"{x:10.1f}" if abs(x) < 1e6 and abs(x) > 1e-3 else "         -" for x in f)
        print(f"  +{o:03x}  {q[0]:#018x} {q[1]:#018x} | {fs} | {i}")


if __name__ == "__main__":
    r = reader()
    if len(sys.argv) < 2 or sys.argv[1] == "count":
        for n, lst in find_objects(r).items():
            print(f"{n:20s} {len(lst):6d}")
    else:
        n = sys.argv[2]
        k = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        for a in find_objects(r, [n])[n][:k]:
            print(f"{n} @ {a:#x}")
            dump(r, a)
