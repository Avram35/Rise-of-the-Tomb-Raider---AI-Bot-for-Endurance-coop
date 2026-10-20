"""Obstacle-course simulator for the learned map + A* planner (no game needed).

The bot does not know the obstacles; it only notices that it stops moving (like in the game). Each course:
walk to a body 40-70 m away. Old brain (straight line + side-steps) vs new brain (NavMap + A*).

    venv\\Scripts\\python nav_sim.py          prints a table; exits 1 if the planner is not clearly better
"""
import math
import random
import sys

from brain import Brain, World
from navmap import NavMap

DT = 0.05
SPEED = 300.0          # cm/s walking


class Course:
    def __init__(self, name, start, goal, rects=(), circles=()):
        self.name, self.start, self.goal = name, start, goal
        self.rects, self.circles = list(rects), list(circles)   # rect (x0, y0, x1, y1); circle (x, y, r)

    def blocked(self, p):
        x, y = p
        for x0, y0, x1, y1 in self.rects:
            if x0 < x < x1 and y0 < y < y1:
                return True
        for cx, cy, r in self.circles:
            if (x - cx) ** 2 + (y - cy) ** 2 < (r + 30) ** 2:      # + Lara's radius
                return True
        return False


def courses():
    out = [
        Course("wall with gap", (0, 0), (0, 5000),
               rects=[(-3000, 2400, -300, 2600), (300, 2400, 3000, 2600)]),   # 6 m gap in the middle
        Course("wall, gap far left", (0, 0), (0, 5000),
               rects=[(-1500, 2400, 4000, 2600)]),                                          # go round the left end
        Course("U-trap (cul-de-sac)", (0, 0), (0, 6000),
               rects=[(-1200, 2500, 1200, 2700), (-1400, 800, -1200, 2700), (1200, 800, 1400, 2700)]),
        Course("rock wall + cliff edge", (0, 0), (3500, 4500),
               rects=[(-500, 2000, 3000, 2300), (2800, 2000, 3100, 4000)]),
    ]
    # 3 random forests: 120 trunks (0.3-0.8 m) + thicket patches between start and goal
    for seed in (1, 2, 3):
        rnd = random.Random(seed)
        circles = []
        while len(circles) < 120:
            c = (rnd.uniform(-2500, 2500), rnd.uniform(500, 5500), rnd.uniform(30, 80))
            if math.hypot(c[0], c[1]) > 300 and math.hypot(c[0], c[1] - 6000) > 300:
                circles.append(c)
        rects = [(rnd.uniform(-1500, 500), rnd.uniform(1500, 4500), 0, 0) for _ in range(4)]
        rects = [(x, y, x + rnd.uniform(500, 1200), y + rnd.uniform(300, 700)) for x, y, _, _ in rects]
        out.append(Course(f"forest {seed}", (0, 0), (0, 6000), rects=rects, circles=circles))
    return out


def run(course, use_nav, limit_s=180, nav=None):
    b = Brain(hunt=True, nav=nav if nav is not None else (NavMap() if use_nav else None))
    p = [float(course.start[0]), float(course.start[1]), 0.0]
    goal = (course.goal[0], course.goal[1], 0.0)
    walked = 0.0
    for i in range(int(limit_s / DT)):
        t = i * DT
        if math.hypot(p[0] - goal[0], p[1] - goal[1]) < 150:
            return t, walked, b
        c = b.step(World(tuple(p), tuple(p), 0.0, loot=goal, t=t))
        if c.lx or c.ly:
            ang = math.atan2(-c.lx, c.ly)                       # camera yaw 0
            sp = SPEED * min(1.0, math.hypot(c.lx, c.ly)) * (1.6 if c.sprint else 1.0)
            nxt = (p[0] + math.cos(ang) * sp * DT, p[1] + math.sin(ang) * sp * DT)
            if not course.blocked(nxt):
                walked += math.hypot(nxt[0] - p[0], nxt[1] - p[1])
                p[0], p[1] = nxt
            else:                                               # slide along the obstacle a little (like the game)
                for nx, ny in ((nxt[0], p[1]), (p[0], nxt[1])):
                    if not course.blocked((nx, ny)):
                        walked += math.hypot(nx - p[0], ny - p[1])
                        p[0], p[1] = nx, ny
                        break
    return None, walked, b


def main():
    rows, better, worse = [], 0, 0
    for c in courses():
        t_old, w_old, _ = run(c, False)
        t_new, w_new, b = run(c, True)
        t_again, _, _ = run(c, True, nav=b.nav)           # same place again, map remembered (saved per area)
        rows.append((c.name, t_old, t_new, w_new / 100, len(b.nav.block), t_again))
        if t_new is not None and (t_old is None or t_new < t_old * 0.9):
            better += 1
        elif t_new is None or (t_old is not None and t_new > t_old * 1.1):
            worse += 1
    fmt = lambda t: "STUCK (180 s)" if t is None else f"{t:6.1f} s"
    print(f"{'course':24s} {'old brain':>14s} {'new (map+A*)':>14s} {'2nd visit':>14s} {'walked':>8s} {'blocked':>8s}")
    for name, a, bb, wk, nb, ag in rows:
        print(f"{name:24s} {fmt(a):>14s} {fmt(bb):>14s} {fmt(ag):>14s} {wk:7.0f}m {nb:8d}")
    print(f"planner better on {better}/{len(rows)} courses, worse on {worse}")
    stuck_new = sum(1 for r in rows if r[2] is None)
    ok = stuck_new == 0 and worse == 0
    print("NAV SIM PASSED" if ok else "NAV SIM FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
