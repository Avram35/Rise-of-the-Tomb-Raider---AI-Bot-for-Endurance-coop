"""Read-only probe: follow ROTTR's player manager to every player's Lara object and print positions.

Chain (from Ghidra, ROTTR.exe Steam build 9575745):
  manager  = [base + 0x2D8BB78]                     (DAT_142d8bb78, passed to FUN_140581bb0)
  entries  = [manager + 0x330], count = u32 [manager + 0x328]
  user     = [[entry + 0x338] + 0x28]               (owner user, compared with the local user)
  instance = [[entry + 0x60E8] + 0x31A8]            (FUN_140581e20)
  position = float x,y @ instance+0x10, z @ +0x18, w @ +0x1C   (AGInstanceGetPosNode)

    venv\\Scripts\\python probe_players.py [pid]      (prints 10 samples, 0.5 s apart)
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time

import psutil

MANAGER_RVA = 0x2D8BB78

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi")
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]


def pick_process():
    procs = [p for p in psutil.process_iter(["name", "exe"]) if (p.info["name"] or "").lower() == "rottr.exe"]
    if len(sys.argv) > 1:
        procs = [p for p in procs if p.pid == int(sys.argv[1])]
    if not procs:
        raise SystemExit("ROTTR.exe not running")
    for p in procs:
        print(f"ROTTR.exe pid {p.pid}  {p.info['exe']}")
    return procs[0]


class Reader:
    def __init__(self, pid):
        self.h = k32.OpenProcess(0x0410, False, pid)  # VM_READ | QUERY_INFORMATION
        if not self.h:
            raise SystemExit("OpenProcess failed")
        self.base = self._module_base(pid)

    def _module_base(self, pid):
        mods = (ctypes.c_void_p * 1024)()
        need = wt.DWORD()
        psapi.EnumProcessModulesEx(self.h, mods, ctypes.sizeof(mods), ctypes.byref(need), 0x03)
        name = ctypes.create_unicode_buffer(260)
        for m in mods[: need.value // ctypes.sizeof(ctypes.c_void_p)]:
            psapi.GetModuleBaseNameW(self.h, ctypes.c_void_p(m), name, 260)
            if name.value.lower() == "rottr.exe":
                return m
        raise SystemExit("ROTTR.exe module not found")

    def read(self, addr, size):
        buf = (ctypes.c_char * size)()
        n = ctypes.c_size_t()
        if not addr or not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
            return None
        return bytes(buf[: n.value])

    def u64(self, addr):
        b = self.read(addr, 8)
        return struct.unpack("<Q", b)[0] if b and len(b) == 8 else 0

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b and len(b) == 4 else 0

    def vec4(self, addr):
        b = self.read(addr, 16)
        return struct.unpack("<4f", b) if b and len(b) == 16 else None


def players(r):
    mgr = r.u64(r.base + MANAGER_RVA)
    out = []
    if not mgr:
        return mgr, out
    lst, cnt = r.u64(mgr + 0x330), r.u32(mgr + 0x328)
    for i in range(min(cnt, 16)):
        e = r.u64(lst + i * 8)
        if not e:
            continue
        user = r.u64(r.u64(e + 0x338) + 0x28)
        sub = r.u64(e + 0x60E8)
        inst = r.u64(sub + 0x31A8) if sub else 0
        pos = r.vec4(inst + 0x10) if inst else None
        out.append({"i": i, "entry": e, "user": user, "instance": inst, "pos": pos})
    return mgr, out


if __name__ == "__main__":
    p = pick_process()
    r = Reader(p.pid)
    print(f"module base {r.base:#x}  manager ptr @ {r.base + MANAGER_RVA:#x}")
    for k in range(10):
        mgr, ps = players(r)
        line = f"[{k}] manager={mgr:#x} players={len(ps)}"
        for q in ps:
            pos = "none" if not q["pos"] else "(%.1f, %.1f, %.1f, w=%.2f)" % q["pos"]
            line += f" | #{q['i']} user={q['user']:#x} inst={q['instance']:#x} pos={pos}"
        print(line)
        time.sleep(0.5)
