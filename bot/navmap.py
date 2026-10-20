"""Learned walk map + A* planner (the game has no readable collision map, so the bot learns one).

Sparse grid of CELL-cm squares in world X/Y:
  walked   Lara actually passed through it         -> cheap (cost 0.6)
  unknown  never seen                              -> normal (cost 1.0)
  blocked  Lara pushed into it and did not move    -> forbidden (expires after BLOCK_TTL s, the world may differ
                                                      from what we thought: a ledge we can climb, an animal...)
  danger   near a human                            -> expensive (set per plan call)

    nav = NavMap();  nav.walked(pos);  nav.blocked_ahead(pos, heading)
    path = nav.plan(start, goal)      # list of (x, y) waypoints (simplified), or None
    nav.save(path) / NavMap.load(path)
"""
import heapq
import json
import math
import time

CELL = 100.0          # 1 m cells: tree trunks are ~0.5-1.5 m
BLOCK_TTL = 300.0     # forget a blocked cell after 5 min
W_WALKED, W_UNKNOWN = 0.6, 1.0
W_NPC = 0.5           # cells an animal/human walked: the game's own navmesh says "walkable" -> preferred
NEIGH = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
         (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]


def cell_of(p):
    return int(math.floor(p[0] / CELL)), int(math.floor(p[1] / CELL))


def center(c):
    return (c[0] + 0.5) * CELL, (c[1] + 0.5) * CELL


class NavMap:
    def __init__(self):
        self.walk = set()
        self.npc_cells = set()   # cells where NPCs were seen (proof of walkable navmesh)
        self._z = 0.0
        self.solid = {}          # cell -> (zmin, zmax) of a game nav obstacle (bfx::BoxObstacle) - never expires
        self.block = {}          # cell -> time it was marked

    # ---------------- learning ----------------
    def walked(self, p):
        self.walk.add(cell_of(p))            # (never un-blocks: Lara pressed against a wall stands on its edge)

    def npc_walked(self, p):
        """an animal / human stands here: walkable ground (their AI walks the game's navmesh). Also clears a
        block we put on that cell (our bump was wrong or the obstacle moved)."""
        c = cell_of(p)
        self.walk.add(c)
        self.npc_cells.add(c)
        self.block.pop(c, None)

    def set_solid(self, cells_with_z):
        """replace the game-obstacle layer: {cell: (zmin, zmax)} (from navobstacles.py)"""
        self.solid = dict(cells_with_z)

    def solid_at(self, c, z):
        """a game obstacle in this cell at walking height z (box top above the knees, bottom below the head)"""
        zr = self.solid.get(c)
        return zr is not None and zr[1] > z + 40 and zr[0] < z + 180

    def blocked_ahead(self, p, heading, depth=0.8, width=2):
        """we pushed toward `heading` (radians) from p and did not move: block the cell(s) in front"""
        ax, ay = p[0] + math.cos(heading) * depth * CELL, p[1] + math.sin(heading) * depth * CELL
        c0 = cell_of((ax, ay))
        here = cell_of(p)
        if c0 == here:                        # still inside our own cell: look a bit further
            ax, ay = p[0] + math.cos(heading) * (depth + 0.6) * CELL, p[1] + math.sin(heading) * (depth + 0.6) * CELL
            c0 = cell_of((ax, ay))
        now = time.time()
        # the cell straight ahead, plus its side neighbours (perpendicular to the heading) when width > 0
        px, py = -math.sin(heading), math.cos(heading)
        for k in range(-width, width + 1):
            c = cell_of((ax + px * k * CELL * 0.7, ay + py * k * CELL * 0.7)) if k else c0
            if c != here:
                self.block[c] = now
                self.walk.discard(c)
        return c0

    def is_blocked(self, c, now=None):
        t = self.block.get(c)
        if t is None:
            return False
        if (now or time.time()) - t > BLOCK_TTL:
            del self.block[c]
            return False
        return True

    # ---------------- planning ----------------
    def plan(self, start, goal, max_expand=40000, danger=(), danger_r=1500.0):
        """A* from start to goal (world cm). danger: list of (x, y) of humans -> cost up within danger_r.
        Returns simplified waypoints [(x, y), ...] ending at the goal, or None if no path."""
        s, g = cell_of(start), cell_of(goal)
        if s == g:
            return [tuple(goal[:2])]
        now = time.time()
        dz = [(d[0], d[1]) for d in danger]
        z0 = start[2] if len(start) > 2 else 0.0
        self._z = z0

        def cost(c):
            if c != g and (self.is_blocked(c, now) or self.solid_at(c, z0)):
                return None
            w = W_NPC if c in self.npc_cells else (W_WALKED if c in self.walk else W_UNKNOWN)
            if dz:
                cx, cy = center(c)
                for hx, hy in dz:
                    dd = math.hypot(cx - hx, cy - hy)
                    if dd < danger_r:
                        w += 4.0 * (1 - dd / danger_r)
            return w

        def hdist(c):
            return math.hypot(c[0] - g[0], c[1] - g[1]) * W_NPC

        openq = [(hdist(s), 0.0, s)]
        best = {s: 0.0}
        came = {}
        n = 0
        while openq and n < max_expand:
            f, gc, c = heapq.heappop(openq)
            if c == g:
                break
            if gc > best.get(c, 1e18):
                continue
            n += 1
            for dx, dy, step in NEIGH:
                nb = (c[0] + dx, c[1] + dy)
                w = cost(nb)
                if w is None:
                    continue
                if dx and dy and (self.is_blocked((c[0] + dx, c[1]), now) or self.is_blocked((c[0], c[1] + dy), now)):
                    continue                    # no diagonal squeezing between two blocked cells
                ng = gc + step * w
                if ng < best.get(nb, 1e18):
                    best[nb] = ng
                    came[nb] = c
                    heapq.heappush(openq, (ng + hdist(nb), ng, nb))
        if g not in came:
            return None
        cells = [g]
        while cells[-1] != s:
            cells.append(came[cells[-1]])
        cells.reverse()
        return self._simplify(cells, goal)

    def _line_free(self, a, b):
        """grid line a->b crosses no blocked cell (sampled every 1/3 cell)"""
        (ax, ay), (bx, by) = center(a), center(b)
        n = max(1, int(math.hypot(bx - ax, by - ay) / (CELL / 3)))
        for i in range(1, n):
            t = i / n
            c = cell_of((ax + (bx - ax) * t, ay + (by - ay) * t))
            if self.is_blocked(c) or self.solid_at(c, self._z):
                return False
        return True

    def _simplify(self, cells, goal):
        """string pulling: keep only the corners we need"""
        out, i = [], 0
        while i < len(cells) - 1:
            j = len(cells) - 1
            while j > i + 1 and not self._line_free(cells[i], cells[j]):
                j -= 1
            out.append(center(cells[j]))
            i = j
        out[-1] = tuple(goal[:2])
        return out

    # ---------------- persistence ----------------
    def save(self, path):
        now = time.time()
        json.dump({"cell": CELL, "walk": sorted(self.walk), "npc": sorted(self.npc_cells),
                   "block": [[c[0], c[1], round(now - t)] for c, t in self.block.items() if now - t < BLOCK_TTL]},
                  open(path, "w"))

    @classmethod
    def load(cls, path):
        m = cls()
        try:
            d = json.load(open(path))
        except (OSError, ValueError):
            return m
        now = time.time()
        m.walk = {tuple(c) for c in d.get("walk", [])}
        m.npc_cells = {tuple(c) for c in d.get("npc", [])}
        m.block = {(c[0], c[1]): now - c[2] for c in d.get("block", [])}
        return m
