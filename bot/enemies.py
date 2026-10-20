"""Read-only: every live NPC (wolves, bears, soldiers...) near Lara (ROTTR.exe Steam build 9575745).

From Ghidra (AGBattleConductor_GetNPCTypeCount FUN_14072b000):
  instance array = base + 0x24EEAD0, 0x120 bytes per slot, < 30000 slots; slot valid if low 32 bits of u64[slot+0x118] == slot id
                   (high bits = generation counter)
                   (Lara = slot 8 = base + 0x24EF3D0)
  NPC            = u32[inst+0xC8] bit 7 set and NpcComponent = [[inst+0xD8] + u32[base+0x1101D14]*8] != 0
                   (NpcComponent byte +0x1931 bit 0 set -> skipped by the game, probably disabled)
  type id        = u32 [[inst+0x80] + 8] + 0x50   (object type; wolves etc. still to identify)
  health         = same chain as Lara (health.py): hc = [[inst+0xD8] + u32[base+0x1101D10]*8]
  position       = floats at inst+0x10

    venv\\Scripts\\python enemies.py        live list of NPCs within 60 m of Lara (every ROTTR.exe / instance)
"""
import math
import struct
import sys
import time

from health import INSTANCE_RVA
from probe_players import Reader

ARRAY_RVA = 0x24EEAD0
STRIDE = 0x120
SLOTS = 30000
NPC_INDEX_RVA = 0x1101D14
HC_INDEX_RVA = 0x1101D10


class Enemies:
    def __init__(self, reader):
        self.r = reader
        self.types = {}

    def _i32(self, addr):
        b = self.r.read(addr, 4)
        return struct.unpack("<i", b)[0] if b else None

    def scan(self, max_dist=6000.0, include_dead=False):
        """-> list of dict(slot, pos, health, max, type, dist) for NPCs within max_dist of Lara, nearest first"""
        r = self.r
        lara = r.vec4(r.base + INSTANCE_RVA + 0x10)
        blob = r.read(r.base + ARRAY_RVA, SLOTS * STRIDE)
        npc_idx, hc_idx = r.u32(r.base + NPC_INDEX_RVA), r.u32(r.base + HC_INDEX_RVA)
        if not blob or not lara or npc_idx is None:
            self.counts = {"array_read": bool(blob), "lara": bool(lara), "npc_index": npc_idx}
            return []
        out = []
        c = self.counts = {"valid": 0, "flag80": 0, "npc": 0, "enabled": 0, "near": 0}
        for i in range(SLOTS):
            o = i * STRIDE
            h = struct.unpack_from("<Q", blob, o + 0x118)[0]
            if h == 0 or (h & 0xFFFFFFFF) != i or i == 8:
                continue
            c["valid"] += 1
            if not (struct.unpack_from("<I", blob, o + 0xC8)[0] >> 7) & 1:
                continue
            c["flag80"] += 1
            ncomp = struct.unpack_from("<I", blob, o + 0xD0)[0]
            table = struct.unpack_from("<Q", blob, o + 0xD8)[0]
            if not table or npc_idx >= ncomp:
                continue
            npc = r.u64(table + npc_idx * 8)
            if not npc:
                continue
            c["npc"] += 1
            flag = r.read(npc + 0x1931, 1)
            dead = bool(flag and flag[0] & 1)
            if dead and not include_dead:
                continue
            c["enabled"] += 1
            x, y, z = struct.unpack_from("<3f", blob, o + 0x10)
            d = math.hypot(x - lara[0], y - lara[1])
            if d > max_dist or not all(map(math.isfinite, (x, y, z))):
                continue
            c["near"] += 1
            hp = mx = None
            if hc_idx is not None and hc_idx < ncomp:
                hc = r.u64(table + hc_idx * 8)
                if hc:
                    hp_obj = r.u64(hc + 0x2A8)
                    hp = self._i32(hp_obj + 0x28) if hp_obj else None
                    mx = self._i32(hc + 0x2C4)
            t = None
            obj = struct.unpack_from("<Q", blob, o + 0x80)[0]
            if obj:
                p = r.u64(obj + 8)
                t = r.u32(p + 0x50) if p else None
            out.append({"slot": i, "pos": (x, y, z), "health": hp, "max": mx, "type": t, "dist": d,
                        "dead": dead or (hp is not None and hp <= 0)})
        out.sort(key=lambda e: e["dist"])
        return out

    def collectables(self, max_dist=5000.0, types=(9169,)):
        """pickups (type 9169 = boxes, wood, ...; vanishes when taken) within max_dist of Lara, nearest first.
        Distance filter first (positions are in the array blob), type lookup (2 reads) only for close slots."""
        r = self.r
        lara = r.vec4(r.base + INSTANCE_RVA + 0x10)
        blob = r.read(r.base + ARRAY_RVA, SLOTS * STRIDE)
        if not blob or not lara:
            return []
        out = []
        for i in range(SLOTS):
            o = i * STRIDE
            h = struct.unpack_from("<Q", blob, o + 0x118)[0]
            if h == 0 or (h & 0xFFFFFFFF) != i or i == 8:
                continue
            x, y, z = struct.unpack_from("<3f", blob, o + 0x10)
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            d = math.hypot(x - lara[0], y - lara[1])
            if d > max_dist:
                continue
            obj = struct.unpack_from("<Q", blob, o + 0x80)[0]
            p = r.u64(obj + 8) if obj else None
            t = r.u32(p + 0x50) if p else None
            if t in types:
                out.append({"slot": i, "handle": h, "type": t, "pos": (x, y, z), "dist": d, "kind": None})
        # kind of each pickup = type of the partner object at the same spot (7982 tree/wood, 7986 feathers drop...)
        if out:
            keys = {(round(e["pos"][0] / 30), round(e["pos"][1] / 30), round(e["pos"][2] / 30)): e for e in out}
            for i in range(SLOTS):
                o = i * STRIDE
                h = struct.unpack_from("<Q", blob, o + 0x118)[0]
                if h == 0 or (h & 0xFFFFFFFF) != i:
                    continue
                x, y, z = struct.unpack_from("<3f", blob, o + 0x10)
                if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                    continue
                e = keys.get((round(x / 30), round(y / 30), round(z / 30)))
                if e is None or e["kind"] is not None or i == e["slot"]:
                    continue
                obj = struct.unpack_from("<Q", blob, o + 0x80)[0]
                p = r.u64(obj + 8) if obj else None
                t = r.u32(p + 0x50) if p else None
                if t is not None and t not in (89, 9169) and 7000 <= t < 8000:
                    e["kind"] = t
        out.sort(key=lambda e: e["dist"])
        return out

    def nearest_alive(self, max_dist=2000.0):
        for e in self.scan(max_dist):
            # max HP >= 100: skip critters (rabbits, birds: 1/1 HP); humans/wolves have 1400-3000
            if e["health"] is not None and e["health"] > 0 and (e["max"] or 0) >= 100:
                return e
        return None


def main():
    import psutil
    games = []
    for p in psutil.process_iter(["name", "exe"]):
        if (p.info["name"] or "").lower() == "rottr.exe":
            exe = (p.info["exe"] or "").lower()
            games.append(("P1" if "instance0" in exe else "P2" if "instance1" in exe else "solo", Enemies(Reader(p.pid))))
    if not games:
        raise SystemExit("ROTTR.exe not running")
    while True:
        for tag, en in sorted(games, key=lambda g: g[0]):
            t0 = time.time()
            lst = en.scan()
            print(f"--- {tag}: {len(lst)} NPCs within 60 m  (scan {1000 * (time.time() - t0):.0f} ms)  "
                  f"filter counts {getattr(en, 'counts', {})}")
            for e in lst[:12]:
                print(f"  slot {e['slot']:5d}  type {e['type']}  {e['dist'] / 100:5.1f} m  "
                      f"hp {e['health']}/{e['max']}  pos {e['pos'][0]:.0f},{e['pos'][1]:.0f},{e['pos'][2]:.0f}")
        time.sleep(1.0)


if __name__ == "__main__":
    main()
