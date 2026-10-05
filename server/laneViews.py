"""Views from the player's lane: the zone seen from where players stand. Eyes stand on the lane's reached walkable floor and on swim
boxes' open water, never where only a smaller player fits, one candidate per eyeBlock block of them. A tour spreads eyes away from where
players arrive, each facing the longest run of reached ground ahead of it, and is kept beside its lane (tour-<laneKey>.json) while its
inputs hold, so an unchanged zone shows the same views. A target's views look at an object, a region, or a point from the eyes that see
it, spread round it, with how much of each of its sides players see and the bearings no player sees it from. Points and headings are
in zone axes, headings 0 = +Y, clockwise, as every tool takes them."""
import hashlib
import json
import math
import os
from pathlib import Path

import numpy
from mcp.server.mcpserver.exceptions import ToolError

import laneDrawing
import planDrawing
import playerReach
import playerScale

eyeBlock = 16.0
tourEyes = 8
targetEyes = 6
bearings = 16
sectorDegrees = 360 / bearings
lookAhead = 10.0
# Sights to a target are tried from this many of the nearest eyes in each bearing sector, which bounds the casts for a large zone.
sightsPerSector = 32
# A sight to a target's point counts as clear when nothing blocks it short of this far before the point.
targetSightShort = 1.0
# A box's side is sampled at these shares across and up, inset from its edges so a sight to one never grazes the next side.
sideShares = (0.1, 0.5, 0.9)
regionSamples = 7
regionPointLift = 1.0
sideNames = ("north", "east", "south", "west", "top")
north, east, south, west, top = range(5)
directionNames = (
  "west", "west-north-west", "north-west", "north-north-west", "north", "north-north-east", "north-east", "east-north-east", "east",
  "east-south-east", "south-east", "south-south-east", "south", "south-south-west", "south-west", "west-south-west",
)
# Of the eyes seeing as much of a target, the one nearest this many of its radii from its middle is chosen: about where the client's
# view holds it whole.
framingRadii = 2.5
# The rules a kept tour was chosen by: a tour chosen by other code is chosen again.
rulesDigest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

eyeFill = (255, 214, 0)
eyeOutline = (20, 20, 20)
eyeRadius = 15
eyeNumberSize = 19
eyeArrowLength = 44
targetColor = (20, 20, 20)
targetHalo = (255, 255, 255)
unseenColor = (220, 20, 20)
unseenAlpha = 100
# The unseen bearings are drawn out to this share of the plan's shorter side from the target's middle.
unseenReach = 0.42


def farSight(zone):
  """How far a player sees: the far clip, or the fog's end when fog is on and nearer, past which the client draws nothing but fog."""
  return float(min(zone["maxClip"], zone["fogEnd"]) if zone["fogOn"] else zone["maxClip"])


def directionName(headingDegrees):
  """A heading's nearest of the sixteen compass directions in the plan's frame (north +X, east -Y)."""
  return directionNames[round(headingDegrees / sectorDegrees) % bearings]


def headingOf(dx, dy):
  """The heading (degrees, 0 = +Y, clockwise) of plan steps."""
  return numpy.degrees(numpy.arctan2(dx, dy)) % 360


def constantsUsed():
  return {name: {"value": getattr(playerScale, name), "source": playerScale.sources[name]} for name in ("eyeHeight", "swimEyeAboveSurface")}


def limits(far, targeted):
  """What can make the views miss what players see, besides the lane's own limits."""
  stated = [
    f"eyes stand on the lane: one candidate per {eyeBlock:g}-unit block (in plan and in height) of reached walkable floor, at eyeHeight over it,"
    " and of reached water open to the air, at swimEyeAboveSurface over its box's top; floor only a smaller player reaches holds none",
    f"eyes look no farther than the far sight, {far:g}: maxClip, or the fog's end when fog is on and nearer",
  ]
  if targeted:
    stated += [
      f"what players see of the target is tried from the {sightsPerSector} eyes nearest it in each of {bearings} bearing sectors, by sights cast"
      " against what the zone ships as collision (bridgeLane.clearSights): a boundary blocks a sight though players see through it, and"
      " cutout cards, water, and what players pass through block none",
      "an object or a point is judged by its bounds (at least playerHeight tall): a sight reaching a point on them counts as seeing it,"
      " so bounds that take in ground or air round the object (a span across a canyon, a tree's crown) read seen where only that is",
    ]
  return stated


def lowestReached(nodes):
  """Each column's lowest reached node's height as a (columnsZ, columnsX) array, infinite where none is reached: a column's nodes lie
  together, lowest first."""
  lane = nodes.lane
  lowest = numpy.full(lane.columnsZ * lane.columnsX, numpy.inf, dtype=numpy.float32)
  for start in range(0, lane.nodeCount, laneDrawing.chunkNodes):
    part = slice(start, min(lane.nodeCount, start + laneDrawing.chunkNodes))
    reached = numpy.flatnonzero(nodes.reached[part])
    if not len(reached):
      continue
    flat = nodes.z[part][reached].astype(numpy.int64) * lane.columnsX + nodes.x[part][reached]
    first = numpy.append(True, flat[1:] != flat[:-1])
    columns = flat[first]
    lowest[columns] = numpy.minimum(lowest[columns], nodes.height[part][reached[first]])
  return lowest.reshape(lane.columnsZ, lane.columnsX)


def eyeCandidates(nodes, description):
  """Where eyes may stand: per block eyeBlock across (in columns from the lane's grid corner) and eyeBlock tall (from height 0) of
  reached walkable floor (label walk) and of reached water (label swim) whose box's top lies open to the air at the eye, the member
  nearest the block's middle (ties to the first). Returns {node, label, standing, eye} arrays: a floor's eye eyeHeight over it, a
  swimmer's swimEyeAboveSurface over the box's top, where the lane floats him. Read a run of whole tiles at a time, which hold whole
  blocks when a tile is a whole number of blocks across."""
  lane = nodes.lane
  eligible = nodes.label == playerReach.walkLabel
  wet = numpy.flatnonzero(nodes.wet & (nodes.label == playerReach.swimLabel))
  holder = wet - 1
  while True:
    back = (holder >= 0) & nodes.wet[numpy.maximum(holder, 0)]
    if not back.any():
      break
    holder[back] -= 1
  safe = numpy.maximum(holder, 0)
  sameColumn = (holder >= 0) & (nodes.x[safe] == nodes.x[wet]) & (nodes.z[safe] == nodes.z[wet])
  boxTops = numpy.array([box["high"][2] for box in description["swimBoxes"]], dtype=numpy.float64)
  eligible[wet[sameColumn & (boxTops[nodes.box[wet]] + playerScale.swimEyeAboveSurface <= lane.height(nodes.high[safe]))]] = True
  across = max(1, round(eyeBlock / lane.cellSize))
  side = across * lane.cellSize
  rows = lane.columnsX // across + 1
  # A block's members are ranked by their distance from its middle in steps this fine, so a block and a rank sort as one integer.
  steps = 64
  ranks = math.ceil(math.sqrt(2 * side * side + eyeBlock * eyeBlock) / 2 * steps) + 1
  perTile = lane.tileCells * lane.tileCells
  tileStarts = nodes.columnStart[::perTile]
  picked, heights = [], []
  tile = 0
  while tile < len(tileStarts) - 1:
    last = tile + 1
    while last < len(tileStarts) - 1 and tileStarts[last + 1] - tileStarts[tile] <= laneDrawing.chunkNodes:
      last += 1
    start = int(tileStarts[tile])
    members = start + numpy.flatnonzero(eligible[start:int(tileStarts[last])])
    tile = last
    if not len(members):
      continue
    height = nodes.height[members].astype(numpy.float64)
    water = nodes.wet[members]
    height[water] = boxTops[nodes.box[members[water]]]
    columnZ, columnX = nodes.z[members].astype(numpy.int64), nodes.x[members].astype(numpy.int64)
    level = numpy.floor(height / eyeBlock).astype(numpy.int64)
    offCenter = numpy.sqrt(
      ((columnZ % across + 0.5) * lane.cellSize - side / 2) ** 2 + ((columnX % across + 0.5) * lane.cellSize - side / 2) ** 2
      + (height - (level + 0.5) * eyeBlock) ** 2
    )
    block = ((columnZ // across) * rows + columnX // across) * (int(level.max() - level.min()) + 1) + (level - level.min())
    order = numpy.argsort(block * ranks + numpy.rint(offCenter * steps).astype(numpy.int64), kind="stable")
    first = order[numpy.append(True, block[order][1:] != block[order][:-1])]
    picked.append(members[first])
    heights.append(height[first])
  if not picked:
    return {"node": numpy.zeros(0, dtype=numpy.int64), "label": numpy.zeros(0, dtype=numpy.uint8), "standing": numpy.zeros((0, 3)), "eye": numpy.zeros((0, 3))}
  picked, height = numpy.concatenate(picked), numpy.concatenate(heights)
  planX, planY = nodes.plan(picked)
  standing = numpy.stack([planX, planY, height], axis=1)
  lift = numpy.where(nodes.wet[picked], playerScale.swimEyeAboveSurface, playerScale.eyeHeight)
  return {"node": picked, "label": nodes.label[picked], "standing": standing, "eye": standing + numpy.stack([numpy.zeros(len(lift)), numpy.zeros(len(lift)), lift], axis=1)}


def spread(points, chosenAlready, count):
  """Farthest-point sampling: up to count of points, each the farthest from chosenAlready and those picked before it; with each one's
  distance from them when picked (its spacing), which never grows. Stops where every point left coincides with one chosen."""
  nearest = numpy.full(len(points), numpy.inf)
  for point in chosenAlready:
    nearest = numpy.minimum(nearest, numpy.linalg.norm(points - point, axis=1))
  picked, spacing = [], []
  while len(picked) < min(count, len(points)):
    pick = int(numpy.argmax(nearest))
    if picked and nearest[pick] <= 0:
      break
    picked.append(pick)
    spacing.append(float(nearest[pick]))
    nearest = numpy.minimum(nearest, numpy.linalg.norm(points - points[pick], axis=1))
  return picked, spacing


def headingRuns(lowest, lane, eye, far):
  """How far reached ground runs ahead of an eye along each of the bearings headings (from 0, every sectorDegrees), up to far: to the
  last column before one no one reaches or whose lowest reached floor rises more than playerHeight over the eye."""
  step = lane.cellSize / 2
  steps = numpy.arange(1, int(far / step) + 1) * step
  headings = numpy.radians(numpy.arange(bearings) * sectorDegrees)
  x = eye[0] + numpy.sin(headings)[:, None] * steps[None, :]
  y = eye[1] + numpy.cos(headings)[:, None] * steps[None, :]
  columnX = numpy.floor((y - lane.origin[0]) / lane.cellSize).astype(numpy.int64)
  columnZ = numpy.floor((x - lane.origin[2]) / lane.cellSize).astype(numpy.int64)
  inside = (columnX >= 0) & (columnX < lane.columnsX) & (columnZ >= 0) & (columnZ < lane.columnsZ)
  floor = lowest[numpy.clip(columnZ, 0, lane.columnsZ - 1), numpy.clip(columnX, 0, lane.columnsX - 1)]
  blocked = ~(inside & (floor <= eye[2] + playerScale.playerHeight))
  first = numpy.where(blocked.any(axis=1), blocked.argmax(axis=1), len(steps))
  return numpy.where(first > 0, steps[numpy.maximum(first, 1) - 1], 0.0)


def roundedPoint(point):
  return [round(float(value), 2) for value in point]


def tourPath(lane):
  return lane.path.parent / f"tour-{lane.path.stem.removeprefix('lane-')}.json"


def tourInputs(far):
  return {
    "rules": rulesDigest, "farSight": far, "eyeBlock": eyeBlock, "eyes": tourEyes, "eyeHeight": playerScale.eyeHeight,
    "swimEyeAboveSurface": playerScale.swimEyeAboveSurface, "playerHeight": playerScale.playerHeight,
  }


def requireCandidates(candidates):
  if not len(candidates["node"]):
    raise ToolError("The lane reaches no walkable floor a player's height clear and no water open to the air, so no eye stands on it")


def tour(nodes, description, far):
  """The tour's eyes on a lane: tourEyes of eyeCandidates spread away from the lane's seeds (the safe point and the stored entries,
  where players arrive), each facing the heading with the longest run of reached ground; kept beside the lane and reused while its
  inputs hold. Returns (tour, "computed" or "reused", its path)."""
  lane = nodes.lane
  path = tourPath(lane)
  inputs = tourInputs(far)
  if path.is_file():
    kept = json.loads(path.read_text(encoding="utf-8"))
    if kept["inputs"] == inputs:
      return kept, "reused", path
  candidates = eyeCandidates(nodes, description)
  requireCandidates(candidates)
  arrivals = numpy.array([seed["footing"] for seed in description["seeds"]], dtype=numpy.float64)
  picked, spacing = spread(candidates["standing"], arrivals, tourEyes)
  lowest = lowestReached(nodes)
  eyes = []
  for number, (index, gap) in enumerate(zip(picked, spacing), 1):
    eye = candidates["eye"][index]
    runs = headingRuns(lowest, lane, eye, far)
    best = int(numpy.argmax(runs))
    heading = best * sectorDegrees
    ahead = eye + lookAhead * numpy.array([math.sin(math.radians(heading)), math.cos(math.radians(heading)), 0.0])
    eyes.append({
      "number": number, "label": playerReach.labelNames[candidates["label"][index]], "standing": roundedPoint(candidates["standing"][index]),
      "eye": roundedPoint(eye), "headingDegrees": heading, "run": round(float(runs[best]), 1), "spacing": round(gap, 2),
      "view": {"eye": roundedPoint(eye), "target": roundedPoint(ahead)},
    })
  kept = {
    "inputs": inputs, "candidates": len(candidates["node"]),
    "arrivals": [{"name": seed["name"], "at": roundedPoint(seed["footing"])} for seed in description["seeds"]], "eyes": eyes,
  }
  partial = path.with_suffix(".json.partial")
  partial.write_text(json.dumps(kept), encoding="utf-8")
  os.replace(partial, path)
  for stale in path.parent.glob("tour-*.json"):
    if not (path.parent / f"lane-{stale.stem.removeprefix('tour-')}.bin").is_file():
      stale.unlink()
  return kept, "computed", path


def boxSides(low, high):
  """Points on a box's four sides and its top (sideShares across and up each, those that coincide on a side of no width kept once),
  each with its side and the half-space an eye must stand in to see it: (points, side, axis, sign, plane)."""
  points, sides, axes, signs, planes = [], [], [], [], []
  for side, axis, sign in ((north, 0, 1), (south, 0, -1), (west, 1, 1), (east, 1, -1), (top, 2, 1)):
    plane = high[axis] if sign > 0 else low[axis]
    first, second = [other for other in range(3) if other != axis]
    found = set()
    for a in sideShares:
      for b in sideShares:
        point = [0.0, 0.0, 0.0]
        point[axis] = plane
        point[first] = low[first] + a * (high[first] - low[first])
        point[second] = low[second] + b * (high[second] - low[second])
        if tuple(point) not in found:
          found.add(tuple(point))
          points.append(point)
          sides.append(side)
          axes.append(axis)
          signs.append(sign)
          planes.append(plane)
  return numpy.array(points), numpy.array(sides), numpy.array(axes), numpy.array(signs), numpy.array(planes)


def boxSubject(kind, name, low, high):
  """A target judged by its bounds, taken at least playerHeight tall, as a player standing on it is."""
  low, high = numpy.asarray(low, dtype=numpy.float64), numpy.asarray(high, dtype=numpy.float64).copy()
  high[2] = max(high[2], low[2] + playerScale.playerHeight)
  points, sides, axes, signs, planes = boxSides(low, high)
  return {
    "kind": kind, "name": name, "low": low, "high": high, "center": (low + high) / 2, "points": points, "side": sides, "axis": axes,
    "sign": signs, "plane": planes, "outline": None, "radius": float(numpy.linalg.norm(high - low)) / 2,
  }


def objectSubject(name, minimum, maximum):
  if minimum is None:
    raise ToolError(f"'{name}' has no bounds to look at (a collection instance of nothing)")
  return boxSubject("object", name, minimum, maximum)


def pointSubject(nodes, point):
  """A player standing on the lane's floor at [x, y] (its highest) or [x, y, z] (as an entry's footing is found)."""
  if not isinstance(point, list) or len(point) not in (2, 3) or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in point):
    raise ToolError(f"A target point is [x, y] or [x, y, z], got {point!r}")
  if nodes.lane.columnAt(point) is None:
    raise ToolError(f"The target point {point} lies off the lane's grid, outside what the zone ships")
  node = nodes.floorNodeAt(point)
  if node is None:
    raise ToolError(f"The lane holds no floor under the target point {point}")
  floor = float(nodes.height[node])
  return boxSubject("point", None, [point[0], point[1], floor], [point[0], point[1], floor])


def regionSubject(nodes, region):
  """A region judged by its floor: in a grid of regionSamples by regionSamples over it, the column nearest each cell's middle of those
  whose middle its outline holds, at its highest floor in the region's band, regionPointLift over it; each point's side is the side of
  the region's middle it lies toward, and any eye may see it."""
  lane = nodes.lane
  mask = playerReach.polygonMask(lane, region["outline"])
  inside = numpy.flatnonzero(~nodes.wet & (nodes.height >= region["bottom"]) & (nodes.height <= region["top"]) & mask[nodes.z, nodes.x])
  if not len(inside):
    raise ToolError(f"Region '{region['name']}' holds no floor from {region['bottom']:g} to {region['top']:g}, its band, for players to see")
  flat = nodes.z[inside].astype(numpy.int64) * lane.columnsX + nodes.x[inside]
  highest = inside[numpy.append(flat[1:] != flat[:-1], True)]
  planX, planY = nodes.plan(highest)
  height = nodes.height[highest].astype(numpy.float64)
  low, high = numpy.array([planX.min(), planY.min()]), numpy.array([planX.max(), planY.max()])
  size = numpy.maximum(high - low, lane.cellSize)
  cell = numpy.minimum(((numpy.stack([planX, planY], axis=1) - low) / size * regionSamples).astype(numpy.int64), regionSamples - 1)
  cellMiddle = low + (cell + 0.5) * size / regionSamples
  offCenter = numpy.hypot(planX - cellMiddle[:, 0], planY - cellMiddle[:, 1])
  key = cell[:, 0] * regionSamples + cell[:, 1]
  order = numpy.lexsort((numpy.arange(len(key)), offCenter, key))
  first = order[numpy.append(True, key[order][1:] != key[order][:-1])]
  points = numpy.stack([planX[first], planY[first], height[first] + regionPointLift], axis=1)
  center = points.mean(axis=0)
  toward = headingOf(points[:, 0] - center[0], points[:, 1] - center[1])
  quarter = numpy.round(toward / 90).astype(numpy.int64) % 4
  sides = numpy.array([west, north, east, south])[quarter]
  return {
    "kind": "region", "name": region["name"], "low": points.min(axis=0), "high": points.max(axis=0), "center": center, "points": points,
    "side": sides, "axis": numpy.full(len(points), -1), "sign": numpy.zeros(len(points)), "plane": numpy.zeros(len(points)),
    "outline": region["outline"], "radius": float(numpy.linalg.norm(points.max(axis=0) - points.min(axis=0))) / 2,
  }


def sightPlan(subject, candidates, far):
  """Which sights to cast: from the sightsPerSector eyes nearest the subject in each bearing sector round it, of those within far of it
  (and, for a box, outside its plan), to each of its points the eye stands in front of. Returns the eyes tried, each one's bearing from
  the subject's middle and distance to it, which sectors hold an eye within far, and the sights as (eye, point) index pairs."""
  eyes = candidates["eye"]
  center = subject["center"]
  if subject["kind"] == "region":
    nearest = numpy.full(len(eyes), numpy.inf)
    for point in subject["points"]:
      nearest = numpy.minimum(nearest, numpy.linalg.norm(eyes - point, axis=1))
    outside = numpy.ones(len(eyes), dtype=bool)
  else:
    nearest = numpy.linalg.norm(eyes - numpy.clip(eyes, subject["low"], subject["high"]), axis=1)
    outside = ~((eyes[:, 0] >= subject["low"][0]) & (eyes[:, 0] <= subject["high"][0]) & (eyes[:, 1] >= subject["low"][1]) & (eyes[:, 1] <= subject["high"][1]))
  distance = numpy.linalg.norm(eyes - center, axis=1)
  bearing = headingOf(eyes[:, 0] - center[0], eyes[:, 1] - center[1])
  sector = numpy.round(bearing / sectorDegrees).astype(numpy.int64) % bearings
  within = numpy.flatnonzero((nearest <= far) & outside)
  sectorHasEyes = numpy.zeros(bearings, dtype=bool)
  sectorHasEyes[sector[within]] = True
  order = within[numpy.lexsort((within, nearest[within], sector[within]))]
  rank = numpy.arange(len(order)) - numpy.searchsorted(sector[order], sector[order])
  tried = numpy.sort(order[rank < sightsPerSector])
  front = numpy.ones((len(tried), len(subject["points"])), dtype=bool)
  boxed = subject["axis"] >= 0
  if boxed.any():
    standing = eyes[tried][:, subject["axis"][boxed]]
    front[:, boxed] = (standing - subject["plane"][boxed]) * subject["sign"][boxed] > 0
  eyeIndex, pointIndex = numpy.nonzero(front)
  return {"tried": tried, "bearing": bearing[tried], "sector": sector[tried], "distance": distance[tried], "sectorHasEyes": sectorHasEyes, "eyeIndex": eyeIndex, "pointIndex": pointIndex}


def sights(subject, candidates, plan):
  """The sights a plan casts, as [eye, point] pairs for the bridge's clearSights."""
  eyes = candidates["eye"][plan["tried"]][plan["eyeIndex"]]
  return [[roundedPoint(eye), roundedPoint(point)] for eye, point in zip(eyes, subject["points"][plan["pointIndex"]])]


def unseenRuns(sectorSeen, sectorHasEyes):
  """The bearing sectors no eye sees from, joined where they touch and share why: [{fromDegrees, toDegrees, why}], why "blocked" (eyes
  stand there but none sees it) or "noReachedFloor" (no reached floor within the far sight)."""
  why = [None if seen else "blocked" if held else "noReachedFloor" for seen, held in zip(sectorSeen, sectorHasEyes)]
  if len(set(why)) == 1:
    return [] if why[0] is None else [{"fromDegrees": 0.0, "toDegrees": 360.0, "why": why[0]}]
  start = next(index for index in range(bearings) if why[index] != why[index - 1])
  runs = []
  for offset in range(bearings):
    index = (start + offset) % bearings
    if why[index] is None:
      continue
    if runs and runs[-1]["why"] == why[index] and runs[-1]["last"] == (index - 1) % bearings:
      runs[-1]["last"] = index
    else:
      runs.append({"first": index, "last": index, "why": why[index]})
  return [
    {"fromDegrees": (run["first"] * sectorDegrees - sectorDegrees / 2) % 360, "toDegrees": (run["last"] * sectorDegrees + sectorDegrees / 2) % 360, "why": run["why"]}
    for run in runs
  ]


def sectorsApart(sector, chosen):
  """How many sectors round a sector lies from the nearest chosen one, the whole circle when none is."""
  return min((min((sector - other) % bearings, (other - sector) % bearings) for other in chosen), default=bearings)


def judge(subject, candidates, plan, clear, count):
  """What the sights found: each side's seen share, the unseen bearing runs, and up to count eyes that see the subject, the best eye of
  each sector it is seen from (the most points, then the nearest framingRadii of its radii from its middle), spread round it (each next
  the sector farthest round from those chosen), in bearing order, each looking at the middle of the points it sees."""
  seen = numpy.zeros((len(plan["tried"]), len(subject["points"])), dtype=bool)
  seen[plan["eyeIndex"], plan["pointIndex"]] = numpy.asarray(clear, dtype=bool)
  pointSeen = seen.any(axis=0)
  sides = {}
  for side, name in enumerate(sideNames):
    held = subject["side"] == side
    sides[name] = round(float(pointSeen[held].mean()), 3) if held.any() else None
  sees = seen.sum(axis=1)
  sectorSeen = numpy.zeros(bearings, dtype=bool)
  sectorSeen[plan["sector"][sees > 0]] = True
  best = {}
  framing = framingRadii * subject["radius"]
  for index in numpy.flatnonzero(sees > 0):
    sector = int(plan["sector"][index])
    rank = (-int(sees[index]), abs(float(plan["distance"][index]) - framing), int(index))
    if sector not in best or rank < best[sector][0]:
      best[sector] = (rank, int(index))
  chosen = []
  while len(chosen) < min(count, len(best)):
    chosen.append(max(
      (sector for sector in best if sector not in chosen),
      key=lambda sector: (sectorsApart(sector, chosen), -best[sector][0][0], -best[sector][0][1], -sector),
    ))
  eyes = []
  for number, sector in enumerate(sorted(chosen, key=lambda sector: plan["bearing"][best[sector][1]]), 1):
    index = best[sector][1]
    candidate = plan["tried"][index]
    eye = candidates["eye"][candidate]
    eyes.append({
      "number": number, "label": playerReach.labelNames[candidates["label"][candidate]], "standing": roundedPoint(candidates["standing"][candidate]),
      "eye": roundedPoint(eye), "bearingDegrees": round(float(plan["bearing"][index]), 1), "distance": round(float(plan["distance"][index]), 1),
      "seenShare": round(float(sees[index]) / len(subject["points"]), 3),
      "view": {"eye": roundedPoint(eye), "target": roundedPoint(subject["points"][seen[index]].mean(axis=0))},
    })
  return {
    "sides": sides, "seenShare": round(float(pointSeen.mean()), 3), "unseenSectors": unseenRuns(sectorSeen, plan["sectorHasEyes"]),
    "eyesTried": len(plan["tried"]), "eyesSeeing": int((sees > 0).sum()), "eyes": eyes,
  }


def describeSubject(subject):
  described = {"kind": subject["kind"], "name": subject["name"], "center": roundedPoint(subject["center"])}
  described["bounds"] = {"minimum": roundedPoint(subject["low"]), "maximum": roundedPoint(subject["high"])}
  return described | {"points": len(subject["points"])}


def tourLabel(eye):
  return f"{eye['number']}: {eye['label']}, heading {eye['headingDegrees']:g} ({directionName(eye['headingDegrees'])}), reached ground {eye['run']:g} ahead"


def targetLabel(eye):
  return (
    f"{eye['number']}: {eye['label']}, from {eye['bearingDegrees']:g} ({directionName(eye['bearingDegrees'])}), {eye['distance']:g} away,"
    f" sees {eye['seenShare']:.0%} of it"
  )


def planFraming(points, margin, least):
  """A plan's center and the extents it must hold across (along y) and up (along x): the points' bounds, grown by margin each side,
  and never under least."""
  points = numpy.asarray(points, dtype=numpy.float64)
  low, high = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
  center = (low + high) / 2
  return [float(center[0]), float(center[1])], max(float(high[1] - low[1]) * (1 + 2 * margin), least), max(float(high[0] - low[0]) * (1 + 2 * margin), least)


class EyePlan(laneDrawing.LanePlan):
  """A lane plan (renderSketch's lane layer) with the eyes marked: each a numbered yellow disc with an arrow the way its view looks; for
  a target, the target outlined and the bearings no player sees it from shaded red out from its middle."""

  def __init__(self, analysis, description, eyes, subject=None, unseen=()):
    super().__init__(analysis, description, None, False)
    self.eyes, self.subject, self.unseen = eyes, subject, unseen

  def strokes(self, draw, frame):
    super().strokes(draw, frame)
    if self.subject is not None:
      center = self.subject["center"]
      reach = unseenReach * min(frame.width, frame.height)
      for run in self.unseen:
        end = run["toDegrees"] if run["toDegrees"] > run["fromDegrees"] else run["toDegrees"] + 360
        headings = numpy.radians(numpy.linspace(run["fromDegrees"], end, max(2, round((end - run["fromDegrees"]) / 3))))
        wedge = [frame.pixel(center)] + [frame.pixel((center[0] + reach * math.sin(heading), center[1] + reach * math.cos(heading))) for heading in headings]
        draw.polygon(wedge, fill=(*unseenColor, unseenAlpha), outline=(*unseenColor, 255), width=2)
      if self.subject["outline"] is not None:
        corners = [frame.pixel(point) for point in self.subject["outline"]]
      else:
        low, high = self.subject["low"], self.subject["high"]
        corners = [frame.pixel(point) for point in ((low[0], low[1]), (high[0], low[1]), (high[0], high[1]), (low[0], high[1]))]
      draw.polygon(corners, outline=(*targetHalo, 255), width=6)
      draw.polygon(corners, outline=(*targetColor, 255), width=3)
      x, y = frame.pixel(center)
      for dx, dy in ((-8, 0), (0, -8)):
        draw.line([(x + dx, y + dy), (x - dx, y - dy)], fill=(*targetColor, 255), width=3)
    for eye in self.eyes:
      x, y = frame.pixel(eye["eye"])
      if self.subject is None:
        heading = eye["headingDegrees"]
      else:
        heading = float(headingOf(eye["view"]["target"][0] - eye["eye"][0], eye["view"]["target"][1] - eye["eye"][1]))
      ahead = planDrawing.pixelDirection(heading)
      tip = (x + ahead[0] * eyeArrowLength, y + ahead[1] * eyeArrowLength)
      side = (-ahead[1], ahead[0])
      draw.line([(x, y), tip], fill=(*eyeOutline, 255), width=4)
      draw.polygon([tip, (tip[0] - ahead[0] * 12 + side[0] * 7, tip[1] - ahead[1] * 12 + side[1] * 7), (tip[0] - ahead[0] * 12 - side[0] * 7, tip[1] - ahead[1] * 12 - side[1] * 7)], fill=(*eyeOutline, 255))
      draw.ellipse([x - eyeRadius, y - eyeRadius, x + eyeRadius, y + eyeRadius], fill=(*eyeFill, 255), outline=(*eyeOutline, 255), width=3)

  def labels(self, board, frame):
    for eye in self.eyes:
      x, y = frame.pixel(eye["eye"])
      board.reserve([x - eyeRadius, y - eyeRadius, x + eyeRadius, y + eyeRadius])
      board.draw.text((x, y), str(eye["number"]), fill=eyeOutline, font=planDrawing.fontOf(eyeNumberSize), anchor="mm")
    super().labels(board, frame)

  def legend(self, frame=None):
    items = [("ring", eyeOutline, "eye, numbered as its view, its arrow the way it looks")]
    if self.subject is not None:
      items += [("box", targetColor, "the target"), ("fill", unseenColor, "bearings no player sees it from")]
    return items + super().legend(frame)
