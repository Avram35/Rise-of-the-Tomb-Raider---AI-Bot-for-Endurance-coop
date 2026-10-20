# Why the game's NPCs never get stuck (static RE, 2026-10-20)

Source: Ghidra on ROTTR_9575745.exe. Script `ghidra/XrefString.java` decompiles every function that uses a given
string. Output is in `ghidra/stuck.c` and `ghidra/stuck2.c`. Units are cm.

## Middleware
The game uses **NavPower by BabelFlux**: the `bfx::` classes and the string "PrepareForNavpowerSimulate".
Relevant classes: Planner, Path/PolylinePath, MoverImpl, Walker, Surfer/NavigatorSurfer, Jumper, LinkImpl/HalfLink
(off-mesh links), RepulsorImpl, BoxObstacle/ConvexObstacle, Locomoter.
Tuning strings: MoverTune::m_sidestepFraction, m_backpedalFraction, m_flockAcc, m_pathAcc, m_tightTurnDegrees,
m_pathSharingPenalty, ObstacleDat::m_penaltyMult.

## The core reason: NPCs do not walk the 3D world, they walk the navmesh
- A **navmesh** is baked when the level is built: a set of polygons covering the ground an NPC can stand on.
  Cliffs, trees and rocks are already holes in that mesh. That is why trees and cliffs are missing from the obstacle
  list (only small movable crates and barrels are BoxObstacles).
- The Planner runs A* over the mesh, so every path it returns is valid by construction.
- The **mover** is a point that slides along the mesh (Surfer/Walker). The animation and the body follow the mover;
  the mover never follows the body. It cannot be "pushed into a rock", because a rock is not part of the mesh.
- Jumps and climbs are explicit **links** between mesh pieces (Jumper / LinkImpl / LinkReservation).
  NPCs never try a jump on the off-chance that it gets them over something.

## The safety nets (decompiled)
1. **RejoinSeperatedMover** (FUN_1405182a0). If the body drifts more than **0.75 m** from the mover, the game casts
   along the mesh towards it. If that fails, it calls the relocate function (#3).
2. **Mover drift** (FUN_14053efd0). If the mover and the body are more than **3 m** apart, the mover is destroyed
   and recreated (FUN_1404f47b0).
3. **Relocate / "Stuck Mover"** (FUN_140539650):
   - It finds the closest point on the navmesh to the NPC and **teleports the mover there**.
   - If there is no mesh nearby, or that point is more than **3 m** away horizontally, it sets the stuck flag
     (npc+0x1932 bit 4).
   - While that flag is set, it retries **every 2.5 s** ("UpdateMover - Stuck Mover").
   - A delayed version runs before each nav simulate step: "PrepareForNavpowerSimulate - Delayed Teleport",
     flag npc+0x193a bit 0x20.
4. **Crowd avoidance** (also in FUN_14053efd0). For every other NPC within **3 m** and inside **±45°** ahead, it sets
   a "blocked ahead" animation variable plus a steer value of **angle × -12**. NPCs sidestep each other early.
   They do not bump into each other and then back out.
5. Repulsors, flocking acceleration and a path-sharing penalty make NPCs spread out, so they do not queue on the
   same line.

So the honest answer is that they do get stuck sometimes, but the game hides it: it snaps the mover back onto the
mesh, and as a last resort teleports the NPC.

## What Lara's bot can't copy
- Lara has no navmesh mover. She is a physics character driven by a pad, so it can't snap or teleport her.
- Reading the navmesh polygons out of memory would be the real prize. The Planner and PlannerInstance are single
  objects (found by vtable scan), but the mesh layout inside them is still unknown. Calling the game's own
  closest-point function (FUN_1400d7bc0) would need code injection. That is possible in single player, but it is a
  big step.

## What the bot CAN copy (ranked)
1. **Plan on walkable ground only, and treat everything else as solid.** The NavMap already does a weaker version
   of this. Make "walked by an NPC" cells (where the mesh is) cheap and unknown cells expensive.
   NPC trails are free samples of the navmesh.
2. **Detect separation early, the way the 0.75 m rejoin check works.** Every tick, compare Lara's position with the
   point she should have reached on the planned path. If she is more than 0.75 m off the line, or has gained
   nothing for 1 s, **re-plan from where she is now and mark the blocked cell**. Do this instead of running the
   back / side / jump / climb escape sequence.
3. **Jump only where there is a reason**, like the game's links: a ledge that really has a height step, or a
   pickup above her. Never jump as a random escape move. In round J that would cut most of the 68 jumps.
4. **Sidestep early** when an NPC or a known box is within 3 m and inside ±45° ahead. Steer away by angle × gain
   before contact. This is the game's own rule.
5. **The retry timer:** if a target point is unreachable, move the goal to the nearest walkable cell. Retry every
   2.5 s up to a limit, then ban it.
