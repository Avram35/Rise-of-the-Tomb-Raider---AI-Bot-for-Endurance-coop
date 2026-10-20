"""Object census: every live instance near Lara (not only NPCs) -> find type ids of food, fires, boxes, wood...

Uses the same global instance array as enemies.py (base+0x24EEAD0, 0x120-byte slots). For each valid slot near Lara
prints type id, distance, flags and whether it has an NpcComponent / health component.

    venv\\Scripts\\python census.py near 8          everything within 8 m, grouped by type (stand next to a bush/box/fire)
    venv\\Scripts\\python census.py snap before    save a snapshot (everything within 40 m) to census_before.json
    venv\\Scripts\\python census.py diff before after   types/slots that appeared, vanished or changed flags
                                                    (e.g. snap, eat the berries / light the fire, snap, diff)
Result goes into TYPES in census_types.json: {"<type id>": "food" | "fire" | "loot" | "wood" | ...}
"""
import json
import math
import os
import struct
import sys
from collections import defaultdict

import psutil

from enemies import ARRAY_RVA, HC_INDEX_RVA, NPC_INDEX_RVA, SLOTS, STRIDE
from health import INSTANCE_RVA
from probe_players import Reader

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN = {7281: "human", 7278: "human", 7819: "boar", 7509: "deer", 7507: "deer", 7534: "squirrel/critter",
         7392: "critter", 9169: "COLLECTABLE (box / wood / ...): vanishes when taken", 7285: "animal 1000hp (wolf?)", 7038: "animal 1000hp"}


def reader():
    p = [q for q in psutil.process_iter(["name"]) if (q.info["name"] or "").lower() == "rottr.exe"]
    if not p:
        raise SystemExit("ROTTR.exe not running")
    return Reader(p[0].pid)


def instances(r, radius_cm):
    lara = r.vec4(r.base + INSTANCE_RVA + 0x10)
    blob = r.read(r.base + ARRAY_RVA, SLOTS * STRIDE)
    npc_idx, hc_idx = r.u32(r.base + NPC_INDEX_RVA), r.u32(r.base + HC_INDEX_RVA)
    out = []
    for i in range(SLOTS):
        o = i * STRIDE
        h = struct.unpack_from("<Q", blob, o + 0x118)[0]
        if h == 0 or (h & 0xFFFFFFFF) != i or i == 8:
            continue
        x, y, z = struct.unpack_from("<3f", blob, o + 0x10)
        if not all(map(math.isfinite, (x, y, z))):
            continue
        d = math.dist((x, y, z), lara[:3])
        if d > radius_cm:
            continue
        flags = struct.unpack_from("<I", blob, o + 0xC8)[0]
        ncomp = struct.unpack_from("<I", blob, o + 0xD0)[0]
        table = struct.unpack_from("<Q", blob, o + 0xD8)[0]
        has_npc = bool(table and npc_idx is not None and npc_idx < ncomp and r.u64(table + npc_idx * 8))
        has_hp = bool(table and hc_idx is not None and hc_idx < ncomp and r.u64(table + hc_idx * 8))
        obj = struct.unpack_from("<Q", blob, o + 0x80)[0]
        t = None
        if obj:
            p = r.u64(obj + 8)
            t = r.u32(p + 0x50) if p else None
        out.append({"slot": i, "handle": h, "type": t, "pos": [round(x), round(y), round(z)], "dist_m": round(d / 100, 1),
                    "flags": flags, "ncomp": ncomp, "npc": has_npc, "hp": has_hp})
    return sorted(out, key=lambda e: e["dist_m"])


def show_near(r, m):
    groups = defaultdict(list)
    for e in instances(r, m * 100):
        groups[e["type"]].append(e)
    for t, lst in sorted(groups.items(), key=lambda kv: kv[1][0]["dist_m"]):
        e = lst[0]
        print(f"type {t!s:>6} x{len(lst):<3} nearest {e['dist_m']:5.1f} m  flags {e['flags']:#010x}  comps {e['ncomp']:3d}"
              f"  npc={int(e['npc'])} hp={int(e['hp'])}  {KNOWN.get(t, '')}")


def main():
    a = sys.argv[1:] or ["near", "8"]
    r = reader()
    if a[0] == "near":
        show_near(r, float(a[1]) if len(a) > 1 else 8)
    elif a[0] == "snap":
        json.dump(instances(r, 4000), open(os.path.join(HERE, f"census_{a[1]}.json"), "w"))
        print("saved", f"census_{a[1]}.json")
    elif a[0] == "diff":
        A = {e["handle"]: e for e in json.load(open(os.path.join(HERE, f"census_{a[1]}.json")))}
        B = {e["handle"]: e for e in json.load(open(os.path.join(HERE, f"census_{a[2]}.json")))}
        for h in A.keys() - B.keys():
            print("GONE   ", A[h]["type"], A[h]["dist_m"], "m", KNOWN.get(A[h]["type"], ""))
        for h in B.keys() - A.keys():
            print("NEW    ", B[h]["type"], B[h]["dist_m"], "m", KNOWN.get(B[h]["type"], ""))
        for h in A.keys() & B.keys():
            if A[h]["flags"] != B[h]["flags"] or A[h]["ncomp"] != B[h]["ncomp"]:
                print("CHANGED", A[h]["type"], A[h]["dist_m"], "m", f"flags {A[h]['flags']:#x} -> {B[h]['flags']:#x}")


if __name__ == "__main__":
    main()
