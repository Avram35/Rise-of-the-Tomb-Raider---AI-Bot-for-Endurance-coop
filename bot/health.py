"""Read-only: Lara's health from memory (ROTTR.exe Steam build 9575745).

Chain (Ghidra, AGHealthAndDamageNode::update FUN_140727dc0 + FUN_140235870):
  instance = base + 0x24EF3D0                       (local Lara; position at +0x10)
  hc       = [[instance + 0xD8] + idx * 8]          idx = u32 [base + 0x1101D10], valid if idx < u32[instance + 0xD0]
  health   = i32 [[hc + 0x2A8] + 0x28]              (current; node case 0, "alive" = health >= 1)
  max      = i32 [hc + 0x2C4]                       (health % = health / max, FUN_140235870)
  flags    = u32 [hc + 0x2C0]                       (bit 3 set/cleared by node case 0xD, bit 4 = alternate stage table)
  stage    = u32 [hc + 0x4C0]                       (health stage index)
Downed (verified live): health = -99 and stage = 5 while down; revive -> 75 (stage 2) -> full.

    venv\\Scripts\\python health.py               live print of every ROTTR.exe (both Nucleus instances)
    venv\\Scripts\\python health.py record 120    record 120 s -> health_log.csv + health_dump.bin (all instances)
"""
import struct
import sys
import time

import psutil

from probe_players import Reader, norm_exe

INSTANCE_RVA = 0x24EF3D0
HC_INDEX_RVA = 0x1101D10
DUMP_SIZE = 0x600


class Health:
    def __init__(self, reader):
        self.r = reader

    def component(self):
        r = self.r
        inst = r.base + INSTANCE_RVA
        idx, count = r.u32(r.base + HC_INDEX_RVA), r.u32(inst + 0xD0)
        if idx is None or count is None or idx >= count:
            return None
        table = r.u64(inst + 0xD8)
        return r.u64(table + idx * 8) if table else None

    def read(self):
        """-> dict(health, max, pct, flags, stage) or None"""
        hc = self.component()
        if not hc:
            return None
        hp_obj = self.r.u64(hc + 0x2A8)
        raw = self.r.read(hp_obj + 0x28, 4) if hp_obj else None
        mx, flags, stage = self.r.u32(hc + 0x2C4), self.r.u32(hc + 0x2C0), self.r.u32(hc + 0x4C0)
        if raw is None or mx is None:
            return None
        hp = struct.unpack("<i", raw)[0]
        mx = struct.unpack("<i", struct.pack("<I", mx))[0]
        return {"health": hp, "max": mx, "pct": hp / mx if mx > 0 else 0.0, "flags": flags, "stage": stage}

    def dump(self):
        hc = self.component()
        return (self.r.read(hc, DUMP_SIZE) if hc else None) or b"\0" * DUMP_SIZE


def instances():
    out = []
    for p in psutil.process_iter(["name", "exe"]):
        if (p.info["name"] or "").lower() == "rottr.exe":
            exe = norm_exe(p.info["exe"])
            tag = "P1" if "instance0" in exe else "P2" if "instance1" in exe else "solo"
            out.append((tag, Health(Reader(p.pid))))
    if not out:
        raise SystemExit("ROTTR.exe not running")
    return sorted(out)


def main():
    games = instances()
    if len(sys.argv) > 1 and sys.argv[1] == "record":
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 120
        t0 = time.time()
        log = open("health_log.csv", "w")
        log.write("t,who,health,max,pct,flags,stage\n")
        dumps = open("health_dump.bin", "wb")     # per sample: f64 t, u8 who, DUMP_SIZE bytes
        print(f"recording {secs:.0f} s - get hurt, go down, get revived; press Ctrl+C to stop early")
        try:
            while time.time() - t0 < secs:
                t = time.time() - t0
                for i, (tag, h) in enumerate(games):
                    v = h.read()
                    if v:
                        log.write(f"{t:.3f},{tag},{v['health']},{v['max']},{v['pct']:.3f},{v['flags']:#x},{v['stage']}\n")
                    dumps.write(struct.pack("<dB", t, i) + h.dump())
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        print("saved health_log.csv + health_dump.bin")
        return
    while True:
        line = []
        for tag, h in games:
            v = h.read()
            line.append(f"{tag}: " + (f"{v['health']}/{v['max']} ({v['pct'] * 100:.0f}%) flags={v['flags']:#x} "
                                      f"stage={v['stage']}" if v else "not found"))
        print("   ".join(line))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
