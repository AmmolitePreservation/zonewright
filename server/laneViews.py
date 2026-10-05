"""Views from the player's lane: the zone seen from where players stand. Eyes stand on the lane's reached walkable floor and on swim
boxes' water open to the air, never where only a smaller player fits, on the ground the zone ships under them. A tour takes one candidate
per eyeBlock block, the member with the most open floor round it, and spreads its eyes away from where players arrive and from the
world's end, each facing the heading whose view holds the most reached ground; it is kept beside its lane (tour-<laneKey>.json) while its
inputs hold, so an unchanged zone shows the same views. A target's views look at an object (points spread over its surface), a region
(points on its floor), or a point (a player standing there) from the eyes that see it, tried from one candidate per targetEyeBlock block
in each bearing sector round it out to the far sight, spread round it; with how much of each of its sides players see and the bearings
no player sees it from. Points and headings are in zone axes, headings 0 = +Y, clockwise, as every tool takes them; pictures name
directions in the plan's frame (north +X, east -Y)."""
import hashlib
import json
import math
import os
from pathlib import Path

import numpy
from mcp.server.mcpserver.exceptions import ToolError
from PIL import ImageDraw

import buildTolerances
import laneDrawing
import planDrawing
import playerReach
import playerScale
import previewFrame

eyeBlock = 16.0
# A target's eyes are tried from one candidate per block this wide, so a strip of floor beside it narrower than eyeBlock holds some.
targetEyeBlock = 2.0
tourEyes = 8
targetEyes = 6
bearings = 16
sectorDegrees = 360 / bearings
lookAhead = 10.0
# A heading's view is judged by the reached ground along rays this far apart across the width of the frame a view renders at.
viewRayDegrees = sectorDegrees / 2
viewHalfField = math.degrees(math.atan(
  math.tan(math.radians(previewFrame.verticalFieldOfViewDegrees) / 2) * previewFrame.renderWidth / previewFrame.renderHeight
))
# Sights to a target are tried from this many eyes in each bearing sector, at distances spread evenly in proportion from the nearest to
# the farthest, which bounds the casts for a large zone.
sightsPerSector = 32
surfaceSamples = 96
# A player's box (a point target) is sampled at these shares across and up each side, inset from its edges.
sideShares = (0.1, 0.5, 0.9)
regionSamples = 7
sideNames = ("north", "east", "south", "west", "top", "underside")
north, east, south, west, top, underside = range(6)
directionNames = (
  "west", "west-north-west", "north-west", "north-north-west", "north", "north-north-east", "north-east", "east-north-east", "east",
  "east-south-east", "south-east", "south-south-east", "south", "south-south-west", "south-west", "west-south-west",
)
# Of the eyes seeing as much of a target, the one nearest this many of its radii from its middle is chosen: about where the client's
# view holds it whole.
framingRadii = 2.5
# The rules a kept tour was chosen by: a tour chosen by other code is chosen again.
rulesDigest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
# A block's members are ranked by their distance from its middle in steps this fine, so a block and a rank sort as one integer.
rankSteps = 64

eyeFill = (255, 214, 0)
eyeOutline = (20, 20, 20)
eyeRadius = 12
eyeNumberSize = 16
eyeArrowLength = 40
eyeArrowHead = 12
targetColor = (20, 20, 20)
targetHalo = (255, 255, 255)
targetPointRadius = 3
blockedColor = (220, 20, 20)
noFloorColor = (70, 70, 70)
arcAlpha = 210
arcGap = 8
arcWidth = 12
arcLeast = 14
markColor = (0, 225, 255)
markShadow = (10, 10, 10)
markWidth = 6
markArm = 28
markMargin = 10


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
    "eyes stand on the lane: on reached walkable floor, eyeHeight over the ground at its column's middle (found as standAt finds it,"
    " within seedTolerance of the lane's floor as a seed's footing is of its node, so a column whose middle lies off the floor crossing"
    " it, at a deck's or an arch's edge, holds none), and on reached water open to the air, swimming at swimEyeAboveSurface over its"
    " box's top where the water over the floor is deeper than"
    " eyeHeight - swimEyeAboveSurface, else wading at eyeHeight over the floor, as renderView's standAt views stand a player; the RoF2"
    " client swims a player only where the water is deeper than his avatarHeight (3.75 for the walked human), which playerScale does not"
    " hold; floor only a smaller player reaches holds none",
    f"eyes look no farther than the far sight, {far:g}: maxClip, or the fog's end when fog is on and nearer",
  ]
  if targeted:
    stated += [
      f"what players see of the target is tried from up to {sightsPerSector} eyes in each of {bearings} bearing sectors round it (one candidate"
      f" per {targetEyeBlock:g}-unit block of floor, at distances spread in proportion out to the far sight), by sights cast against what"
      " the zone ships as collision (bridgeLane.clearSights): a boundary blocks a sight though players see through it, and cutout cards,"
      " water, and what players pass through block none",
      f"an object is judged by {surfaceSamples} points spread over its surface by area, each seen only from in front of its face as its mesh"
      " winds it; a point by the player standing there (playerHeight tall); a region by points on its highest floor in its band",
    ]
  else:
    stated.append(
      f"a tour takes one candidate per {eyeBlock:g}-unit block (in plan and in height) and spreads its eyes away from the world's end"
      " (floor beside columns where the zone ships nothing), so floor players reach there, a leak mapReach reports, holds no tour eye"
    )
  return stated


def gridOfColumns(lane, values):
  """Per-column values in the lane's tile-major column order as a (columnsZ, columnsX) array."""
  return values.reshape(lane.tilesZ, lane.tilesX, lane.tileCells, lane.tileCells).transpose(0, 2, 1, 3).reshape(lane.columnsZ, lane.columnsX)


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


def wetEyes(nodes, description):
  """The reached water an eye may stand in, each node with where it stands and whether it swims: a player swims, his eye
  swimEyeAboveSurface over the box's top, where the water over the floor under it is deeper than eyeHeight - swimEyeAboveSurface, as
  renderView's standAt views stand him, else he wades on that floor, his eye eyeHeight over it; either eye must lie in the open space
  over that floor. Returns (nodes, standing heights, swimming), the nodes in order."""
  lane = nodes.lane
  wet = numpy.flatnonzero(nodes.wet & (nodes.label == playerReach.swimLabel))
  holder = wet - 1
  while True:
    back = (holder >= 0) & nodes.wet[numpy.maximum(holder, 0)]
    if not back.any():
      break
    holder[back] -= 1
  safe = numpy.maximum(holder, 0)
  sameColumn = (holder >= 0) & (nodes.x[safe] == nodes.x[wet]) & (nodes.z[safe] == nodes.z[wet])
  boxTops = numpy.array([box["high"][2] for box in description["swimBoxes"]] + [numpy.nan], dtype=numpy.float64)
  surface = boxTops[nodes.box[wet]]
  floor = nodes.height[safe].astype(numpy.float64)
  swimming = surface - floor > playerScale.eyeHeight - playerScale.swimEyeAboveSurface
  standing = numpy.where(swimming, surface, floor)
  eye = standing + numpy.where(swimming, playerScale.swimEyeAboveSurface, playerScale.eyeHeight)
  open = sameColumn & (eye <= lane.height(nodes.high[safe]))
  return wet[open], standing[open], swimming[open]


def openFloor(nodes, eligible):
  """Which columns hold a node an eye may stand on, as a (columnsZ, columnsX) array."""
  lane = nodes.lane
  found = numpy.zeros(lane.columnsZ * lane.columnsX, dtype=bool)
  for start in range(0, lane.nodeCount, laneDrawing.chunkNodes):
    stop = min(lane.nodeCount, start + laneDrawing.chunkNodes)
    members = start + numpy.flatnonzero(eligible[start:stop])
    found[nodes.z[members].astype(numpy.int64) * lane.columnsX + nodes.x[members]] = True
  return found.reshape(lane.columnsZ, lane.columnsX)


def clearance(opened, steps):
  """How many columns of open floor lie every way round each column (to the nearest column without any, corners counted), up to steps:
  a (columnsZ, columnsX) array; off the grid is no floor."""
  counted = numpy.zeros(opened.shape, dtype=numpy.uint8)
  current = opened.copy()
  for _ in range(steps):
    counted += current
    eroded = current.copy()
    for down, across in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
      target = eroded[max(down, 0):eroded.shape[0] + min(down, 0), max(across, 0):eroded.shape[1] + min(across, 0)]
      target &= current[max(-down, 0):current.shape[0] + min(-down, 0), max(-across, 0):current.shape[1] + min(-across, 0)]
    eroded[0, :] = eroded[-1, :] = False
    eroded[:, 0] = eroded[:, -1] = False
    current = eroded
  return counted


def eyeCandidates(nodes, description, block):
  """Where eyes may stand, one per block `block` across (in columns from the lane's grid corner) and `block` tall (from height 0) of
  reached walkable floor (label walk) and wetEyes' water: the member with the most open floor round it (clearance, out to half an
  eyeBlock), then the one nearest the block's middle, then the first. Returns {node, label, swimming, standing, eye} arrays (standing
  on the lane's floor, the top of its column's span, or a swimmer at the box's top; the eye over it) and the open floor (openFloor). Read
  a run of whole tiles at a time, which hold whole blocks when a tile is a whole number of blocks across."""
  lane = nodes.lane
  wet, wetStanding, wetSwimming = wetEyes(nodes, description)
  eligible = nodes.label == playerReach.walkLabel
  eligible[wet] = True
  opened = openFloor(nodes, eligible)
  steps = max(1, round(eyeBlock / 2 / lane.cellSize))
  room = clearance(opened, steps)
  across = max(1, round(block / lane.cellSize))
  side = across * lane.cellSize
  rows = lane.columnsX // across + 1
  ranks = math.ceil(math.sqrt(2 * side * side + block * block) / 2 * rankSteps) + 1
  perTile = lane.tileCells * lane.tileCells
  tileStarts = nodes.columnStart[::perTile]
  picked, heights, swims, rooms, places = [], [], [], [], []
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
    swimming = numpy.zeros(len(members), dtype=bool)
    water = nodes.wet[members]
    if water.any():
      at = numpy.searchsorted(wet, members[water])
      height[water] = wetStanding[at]
      swimming[water] = wetSwimming[at]
    columnZ, columnX = nodes.z[members].astype(numpy.int64), nodes.x[members].astype(numpy.int64)
    level = numpy.floor(height / block).astype(numpy.int64)
    offCenter = numpy.sqrt(
      ((columnZ % across + 0.5) * lane.cellSize - side / 2) ** 2 + ((columnX % across + 0.5) * lane.cellSize - side / 2) ** 2
      + (height - (level + 0.5) * block) ** 2
    )
    crowded = steps - room[columnZ, columnX].astype(numpy.int64)
    blockNumber = ((columnZ // across) * rows + columnX // across) * (int(level.max() - level.min()) + 1) + (level - level.min())
    order = numpy.argsort((blockNumber * (steps + 1) + crowded) * ranks + numpy.rint(offCenter * rankSteps).astype(numpy.int64), kind="stable")
    first = order[numpy.append(True, blockNumber[order][1:] != blockNumber[order][:-1])]
    picked.append(members[first])
    heights.append(height[first])
    swims.append(swimming[first])
    rooms.append(steps - crowded[first])
    places.append(numpy.stack([columnZ[first] // across, columnX[first] // across, level[first]], axis=1))
  if not picked:
    return {
      "node": numpy.zeros(0, dtype=numpy.int64), "label": numpy.zeros(0, dtype=numpy.uint8), "swimming": numpy.zeros(0, dtype=bool),
      "standing": numpy.zeros((0, 3)), "eye": numpy.zeros((0, 3)), "room": numpy.zeros(0, dtype=numpy.int64),
      "block": numpy.zeros((0, 3), dtype=numpy.int64), "open": opened,
    }
  picked, height, swimming = numpy.concatenate(picked), numpy.concatenate(heights), numpy.concatenate(swims)
  planX, planY = nodes.plan(picked)
  standing = numpy.stack([planX, planY, height], axis=1)
  return {
    "node": picked, "label": nodes.label[picked], "swimming": swimming, "standing": standing, "eye": eyesOver(standing, swimming),
    "room": numpy.concatenate(rooms), "block": numpy.concatenate(places), "open": opened,
  }


def openest(candidates):
  """Which candidates no candidate in a block beside theirs (in plan and in height, corners counted) on floor within playerHeight of
  theirs has more open floor round than: a strip of floor a block's edge cuts off keeps no candidate beside a roomier one."""
  block, room, height = candidates["block"], candidates["room"], candidates["standing"][:, 2]
  if not len(room):
    return numpy.zeros(0, dtype=bool)
  low = block.min(axis=0) - 1
  span = block.max(axis=0) - low + 2
  key = ((block[:, 0] - low[0]) * span[1] + block[:, 1] - low[1]) * span[2] + block[:, 2] - low[2]
  order = numpy.argsort(key)
  sortedKey = key[order]
  most = room.copy()
  for down in (-1, 0, 1):
    for across in (-1, 0, 1):
      for up in (-1, 0, 1):
        if down == across == up == 0:
          continue
        neighbor = key + (down * span[1] + across) * span[2] + up
        at = order[numpy.minimum(numpy.searchsorted(sortedKey, neighbor), len(order) - 1)]
        beside = (key[at] == neighbor) & (numpy.abs(height[at] - height) <= playerScale.playerHeight)
        most = numpy.where(beside, numpy.maximum(most, room[at]), most)
  return room >= most


def eyesOver(standing, swimming):
  """The eyes of players standing at points: eyeHeight over the ground, swimEyeAboveSurface over the water for a swimmer."""
  eye = numpy.array(standing, dtype=numpy.float64)
  eye[:, 2] += numpy.where(swimming, playerScale.swimEyeAboveSurface, playerScale.eyeHeight)
  return eye


def groundedPoints(candidates, chosen):
  """Where the chosen candidates that stand on ground (a floor, or the floor under a wader) stand on the lane: the points to find the
  ground under."""
  return candidates["standing"][chosen][~candidates["swimming"][chosen]]


def placedEyes(candidates, chosen, grounds):
  """The chosen candidates' standing points and eyes, those on ground stood on the ground found under them (grounds, in groundedPoints'
  order: the ground's height and its face's normal z, or None where none was found); a swimmer's are its candidate's. A candidate on
  ground stands only where that ground lies within playerReach.seedTolerance of its floor, as a seed's footing matches its node, at the
  lane's resolution (half a cell height): the lane holds a floor wherever a face crosses a column, so a column's middle may lie off it
  (a deck's or an arch's edge). Returns (which of the chosen stand, standing points, eyes), all three over the chosen."""
  standing = candidates["standing"][chosen].copy()
  swimming = candidates["swimming"][chosen]
  stands = numpy.ones(len(chosen), dtype=bool)
  floors = numpy.flatnonzero(~swimming)
  resolution = playerReach.reachGrid["cellHeight"] / 2
  for at, ground in zip(floors, grounds):
    if ground is None or abs(standing[at, 2] - ground[0]) > playerReach.seedTolerance(ground[1]) + resolution:
      stands[at] = False
    else:
      standing[at, 2] = ground[0]
  return stands, standing, eyesOver(standing, swimming)


def worldEnd(nodes, opened, lowest):
  """Where the world ends for a tour: columns of open floor beside one where the lane holds no node at all (the zone ships nothing
  there) or on the grid's edge, the first of each eyeBlock block, each at its lowest reached floor: [n, 3] points."""
  lane = nodes.lane
  shipped = numpy.pad(~gridOfColumns(lane, nodes.columnStart[1:] == nodes.columnStart[:-1]), 1, constant_values=False)
  beside = numpy.zeros(opened.shape, dtype=bool)
  for down in (-1, 0, 1):
    for across in (-1, 0, 1):
      beside |= ~shipped[1 + down:1 + down + opened.shape[0], 1 + across:1 + across + opened.shape[1]]
  columnZ, columnX = numpy.nonzero(opened & beside)
  if not len(columnZ):
    return numpy.zeros((0, 3))
  per = max(1, round(eyeBlock / lane.cellSize))
  _, first = numpy.unique((columnZ // per) * (lane.columnsX // per + 1) + columnX // per, return_index=True)
  columnZ, columnX = columnZ[first], columnX[first]
  planX = lane.origin[2] + (columnZ + 0.5) * lane.cellSize
  planY = lane.origin[0] + (columnX + 0.5) * lane.cellSize
  return numpy.stack([planX, planY, lowest[columnZ, columnX].astype(numpy.float64)], axis=1)


def distancesFrom(points, chosenAlready):
  """Each point's distance from the nearest of chosenAlready, infinite when there is none."""
  nearest = numpy.full(len(points), numpy.inf)
  for point in chosenAlready:
    nearest = numpy.minimum(nearest, numpy.linalg.norm(points - point, axis=1))
  return nearest


def spread(points, nearest, count):
  """Farthest-point sampling: up to count of points, each the farthest from what counts as chosen (nearest: each point's distance from
  it, -infinity for a point left out) and those picked before it; with each one's distance from them when picked (its spacing), which
  never grows. Stops where every point left coincides with one chosen or is left out."""
  nearest = nearest.copy()
  picked, spacing = [], []
  while len(picked) < min(count, len(points)):
    pick = int(numpy.argmax(nearest))
    if nearest[pick] == -numpy.inf or (picked and nearest[pick] <= 0):
      break
    picked.append(pick)
    spacing.append(float(nearest[pick]))
    nearest = numpy.minimum(nearest, numpy.linalg.norm(points - points[pick], axis=1))
  return picked, spacing


def headingRuns(lowest, lane, eye, far, headingsDegrees):
  """How far reached ground runs ahead of an eye along each heading, up to far: to the last column before one no one reaches or whose
  lowest reached floor rises more than playerHeight over the eye."""
  step = lane.cellSize / 2
  steps = numpy.arange(1, int(far / step) + 1) * step
  headings = numpy.radians(numpy.asarray(headingsDegrees, dtype=numpy.float64))
  x = eye[0] + numpy.sin(headings)[:, None] * steps[None, :]
  y = eye[1] + numpy.cos(headings)[:, None] * steps[None, :]
  columnX = numpy.floor((y - lane.origin[0]) / lane.cellSize).astype(numpy.int64)
  columnZ = numpy.floor((x - lane.origin[2]) / lane.cellSize).astype(numpy.int64)
  inside = (columnX >= 0) & (columnX < lane.columnsX) & (columnZ >= 0) & (columnZ < lane.columnsZ)
  floor = lowest[numpy.clip(columnZ, 0, lane.columnsZ - 1), numpy.clip(columnX, 0, lane.columnsX - 1)]
  blocked = ~(inside & (floor <= eye[2] + playerScale.playerHeight))
  first = numpy.where(blocked.any(axis=1), blocked.argmax(axis=1), len(steps))
  return numpy.where(first > 0, steps[numpy.maximum(first, 1) - 1], 0.0)


def viewHeading(lowest, lane, eye, far):
  """Which of the bearings headings an eye faces: the one whose view holds the most reached ground, the mean of headingRuns along rays
  viewRayDegrees apart across the frame's width, each weighted by the cosine of its share of the half field in quarter turns, so the
  middle of the view, what it is about, counts most and its edges least. Returns (heading in degrees, its own run, the view's mean
  run)."""
  half = math.floor(viewHalfField / viewRayDegrees)
  rays = numpy.arange(-half, half + 1) * viewRayDegrees
  weights = numpy.cos(numpy.radians(rays) / math.radians(viewHalfField) * math.pi / 2)
  headings = numpy.arange(bearings)[:, None] * sectorDegrees + rays[None, :]
  runs = headingRuns(lowest, lane, eye, far, headings.ravel()).reshape(bearings, len(rays))
  best = int(numpy.argmax(runs @ weights))
  return best * sectorDegrees, float(runs[best, half]), float(runs[best] @ weights / weights.sum())


def roundedPoint(point):
  return [round(float(value), 2) + 0.0 for value in point]


def tourPath(lane):
  return lane.path.parent / f"tour-{lane.path.stem.removeprefix('lane-')}.json"


def tourInputs(far):
  return {
    "rules": rulesDigest, "farSight": far, "eyeBlock": eyeBlock, "eyes": tourEyes, "eyeHeight": playerScale.eyeHeight,
    "swimEyeAboveSurface": playerScale.swimEyeAboveSurface, "playerHeight": playerScale.playerHeight,
    "groundSearch": [buildTolerances.groundSearchAbove, buildTolerances.groundSearchDistance], "viewHalfField": viewHalfField,
  }


def requireCandidates(candidates):
  if not len(candidates["node"]):
    raise ToolError("The lane reaches no walkable floor a player's height clear and no water open to the air, so no eye stands on it")


def keptTour(lane, far):
  """The tour kept beside the lane while its inputs hold, else None; and its path."""
  path = tourPath(lane)
  if path.is_file():
    kept = json.loads(path.read_text(encoding="utf-8"))
    if kept["inputs"] == tourInputs(far):
      return kept, path
  return None, path


def tourPicks(nodes, description):
  """What a tour's eyes are spread over: the roomiest eyeCandidates (openest; one per eyeBlock block) and each one's distance from the
  lane's seeds (the safe point and the stored entries, where players arrive) and from worldEnd. Returns {candidates, roomy (indices
  into them), nearest, worldEnd, lowest}."""
  candidates = eyeCandidates(nodes, description, eyeBlock)
  requireCandidates(candidates)
  lowest = lowestReached(nodes)
  ends = worldEnd(nodes, candidates["open"], lowest)
  arrivals = numpy.array([seed["footing"] for seed in description["seeds"]], dtype=numpy.float64).reshape(-1, 3)
  roomy = numpy.flatnonzero(openest(candidates))
  return {
    "candidates": candidates, "roomy": roomy, "nearest": distancesFrom(candidates["standing"][roomy], numpy.concatenate([arrivals, ends])),
    "worldEnd": ends, "lowest": lowest,
  }


def tourSpread(picks, leftOut):
  """tourEyes of tourPicks' roomiest candidates spread away from the arrivals, the world's end, and each other, those in leftOut (a mask
  over the candidates: where no eye stands) left out: (candidate indices, spacings)."""
  roomy = picks["roomy"]
  picked, spacing = spread(picks["candidates"]["standing"][roomy], numpy.where(leftOut[roomy], -numpy.inf, picks["nearest"]), tourEyes)
  return roomy[numpy.array(picked, dtype=numpy.int64)], spacing


def finishTour(nodes, description, far, picks, chosen, spacing, placed):
  """The tour of the chosen candidates (tourSpread) standing where placed (their standing points and eyes), each facing viewHeading;
  written beside the lane, the old tours of lanes no longer kept removed. Returns (tour, its path)."""
  lane = nodes.lane
  candidates = picks["candidates"]
  eyes = []
  for number, (index, gap, stand, eye) in enumerate(zip(chosen, spacing, *placed), 1):
    heading, run, viewRun = viewHeading(picks["lowest"], lane, eye, far)
    ahead = eye + lookAhead * numpy.array([math.sin(math.radians(heading)), math.cos(math.radians(heading)), 0.0])
    eyes.append({
      "number": number, "label": playerReach.labelNames[candidates["label"][index]], "swimming": bool(candidates["swimming"][index]),
      "standing": roundedPoint(stand), "eye": roundedPoint(eye), "headingDegrees": heading, "direction": directionName(heading),
      "run": round(run, 1), "viewRun": round(viewRun, 1), "spacing": round(gap, 2), "view": {"eye": roundedPoint(eye), "target": roundedPoint(ahead)},
    })
  kept = {
    "inputs": tourInputs(far), "candidates": len(candidates["node"]), "openCandidates": len(picks["roomy"]),
    "arrivals": [{"name": seed["name"], "at": roundedPoint(seed["footing"])} for seed in description["seeds"]],
    "worldEnd": [roundedPoint(point) for point in picks["worldEnd"]], "eyes": eyes,
  }
  path = tourPath(lane)
  partial = path.with_suffix(".json.partial")
  partial.write_text(json.dumps(kept), encoding="utf-8")
  os.replace(partial, path)
  for stale in path.parent.glob("tour-*.json"):
    if not (path.parent / f"lane-{stale.stem.removeprefix('tour-')}.bin").is_file():
      stale.unlink()
  return kept, path


def standingTried(plan, stands):
  """A sight plan keeping only the eyes tried that stand (placedEyes)."""
  return {key: value if key == "sectorHasEyes" else value[stands] for key, value in plan.items()}


def sideOfNormals(normals):
  """The side each normal faces most: top or underside where it is most nearly vertical, else north (+X), south, west (+Y), or east."""
  size = numpy.abs(normals)
  vertical = size[:, 2] >= size[:, :2].max(axis=1)
  alongX = size[:, 0] >= size[:, 1]
  return numpy.where(
    vertical, numpy.where(normals[:, 2] > 0, top, underside),
    numpy.where(alongX, numpy.where(normals[:, 0] > 0, north, south), numpy.where(normals[:, 1] > 0, west, east)),
  )


def subjectOf(kind, name, points, normals, low, high, outline=None):
  low, high = numpy.asarray(low, dtype=numpy.float64), numpy.asarray(high, dtype=numpy.float64)
  normals = numpy.asarray(normals, dtype=numpy.float64)
  faced = numpy.linalg.norm(normals, axis=1) > 0
  return {
    "kind": kind, "name": name, "low": low, "high": high, "center": (low + high) / 2, "points": numpy.asarray(points, dtype=numpy.float64),
    "normals": normals, "side": numpy.where(faced, sideOfNormals(normals), -1), "outline": outline,
    "radius": float(numpy.linalg.norm(high - low)) / 2,
  }


def objectSubject(name, surface):
  """An object judged by points spread over its surface (bridgeLane.surfacePoints), each on the side its face's normal faces most."""
  return subjectOf("object", name, surface["points"], surface["normals"], surface["minimum"], surface["maximum"])


def playerSubject(floor):
  """A player standing at a floor point, playerHeight tall and no wider than a point: sideShares up each of its four sides, each facing
  out, and its top."""
  low = numpy.asarray(floor, dtype=numpy.float64)
  high = low + [0.0, 0.0, playerScale.playerHeight]
  points, normals = [], []
  for normal in ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0)):
    for share in sideShares:
      points.append(low + [0.0, 0.0, share * playerScale.playerHeight])
      normals.append(normal)
  points.append(high)
  normals.append((0.0, 0.0, 1.0))
  return subjectOf("point", None, points, normals, low, high)


def pointSubject(nodes, point):
  """A player standing on the lane's floor at [x, y] (its highest) or [x, y, z] (as an entry's footing is found)."""
  numbers = isinstance(point, list) and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in point)
  if not numbers or len(point) not in (2, 3):
    raise ToolError(f"A target point is [x, y] or [x, y, z], got {point!r}")
  if nodes.lane.columnAt(point) is None:
    raise ToolError(f"The target point {point} lies off the lane's grid, outside what the zone ships")
  node = nodes.floorNodeAt(point)
  if node is None:
    raise ToolError(f"The lane holds no floor under the target point {point}")
  return playerSubject([point[0], point[1], float(nodes.height[node])])


def regionSubject(nodes, region):
  """A region judged by its floor: in a grid of regionSamples by regionSamples over it, the column nearest each cell's middle of those
  whose middle its outline holds, at its highest floor in the region's band, floorSightLift over it; each point's side is the side of
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
  points = numpy.stack([planX[first], planY[first], height[first] + buildTolerances.floorSightLift], axis=1)
  subject = subjectOf("region", region["name"], points, numpy.zeros_like(points), points.min(axis=0), points.max(axis=0), region["outline"])
  subject["band"] = (region["bottom"], region["top"])
  toward = headingOf(points[:, 0] - subject["center"][0], points[:, 1] - subject["center"][1])
  subject["side"] = numpy.array([west, north, east, south])[numpy.round(toward / 90).astype(numpy.int64) % 4]
  return subject


def outlineOnFloor(nodes, subject):
  """A region subject's outline as players see it on the ground: points along it a cell apart, each on the highest floor in the region's
  band in its column, floorSightLift over it; in runs, broken where a column holds no such floor."""
  lane = nodes.lane
  bottom, top = subject["band"]
  corners = numpy.asarray(list(subject["outline"]) + list(subject["outline"][:1]), dtype=numpy.float64)
  runs, current = [], []
  for start, end in zip(corners[:-1], corners[1:]):
    count = max(1, math.ceil(float(numpy.linalg.norm(end - start)) / lane.cellSize))
    for share in numpy.arange(count) / count:
      point = start + share * (end - start)
      column = lane.columnAt(point)
      found = None
      if column is not None:
        inColumn = numpy.arange(nodes.columnStart[column], nodes.columnStart[column + 1])
        heights = nodes.height[inColumn[~nodes.wet[inColumn]]]
        heights = heights[(heights >= bottom) & (heights <= top)]
        found = float(heights.max()) if len(heights) else None
      if found is None:
        if len(current) > 1:
          runs.append(current)
        current = []
      else:
        current.append([float(point[0]), float(point[1]), found + buildTolerances.floorSightLift])
  if len(current) > 1:
    runs.append(current)
  return runs


def sightPlan(subject, candidates, far):
  """Which eyes to try: in each bearing sector round the subject's middle, of the candidates within far of its bounds, sightsPerSector at
  distances from them spread evenly in proportion from the nearest (at least targetEyeBlock) to the farthest, each the nearest at or
  past its distance. Returns the eyes tried (candidate indices), each one's bearing from the subject's middle, sector, and distance to
  it, and which sectors hold any candidate within far."""
  eyes = candidates["eye"]
  center = subject["center"]
  reach = numpy.linalg.norm(eyes - numpy.clip(eyes, subject["low"], subject["high"]), axis=1)
  bearing = headingOf(eyes[:, 0] - center[0], eyes[:, 1] - center[1])
  sector = numpy.round(bearing / sectorDegrees).astype(numpy.int64) % bearings
  within = numpy.flatnonzero(reach <= far)
  sectorHasEyes = numpy.zeros(bearings, dtype=bool)
  sectorHasEyes[sector[within]] = True
  tried = []
  for number in numpy.flatnonzero(sectorHasEyes):
    members = within[sector[within] == number]
    order = members[numpy.argsort(reach[members], kind="stable")]
    distances = reach[order]
    least = max(float(distances[0]), targetEyeBlock)
    most = max(float(distances[-1]), least)
    wanted = least * (most / least) ** numpy.linspace(0.0, 1.0, sightsPerSector)
    tried.append(order[numpy.unique(numpy.minimum(numpy.searchsorted(distances, wanted), len(order) - 1))])
  tried = numpy.sort(numpy.concatenate(tried)) if tried else numpy.zeros(0, dtype=numpy.int64)
  return {
    "tried": tried, "bearing": bearing[tried], "sector": sector[tried], "distance": numpy.linalg.norm(eyes[tried] - center, axis=1),
    "sectorHasEyes": sectorHasEyes,
  }


def facing(subject, eyes):
  """Which of the subject's points each eye stands in front of, (eyes, points): a point with a normal faces the eyes on its side of its
  face, one without faces every eye."""
  toward = ((eyes[:, None, :] - subject["points"][None, :, :]) * subject["normals"][None, :, :]).sum(axis=2) > 0
  return toward | (numpy.linalg.norm(subject["normals"], axis=1) == 0)[None, :]


def sights(subject, eyes, front):
  """The sights to cast, as [eye, point] pairs for the bridge's clearSights: from each eye to each point in front of it."""
  eyeIndex, pointIndex = numpy.nonzero(front)
  return [[roundedPoint(eyes[eye]), roundedPoint(subject["points"][point])] for eye, point in zip(eyeIndex, pointIndex)]


def unseenRuns(sectorSeen, sectorHasEyes):
  """The bearing sectors no eye sees from, joined where they touch and share why: [{fromDegrees, toDegrees, why}], why "blocked" (eyes
  stand there but none sees it) or "noReachedFloor" (no reached floor an eye stands on within the far sight)."""
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
    {
      "fromDegrees": (run["first"] * sectorDegrees - sectorDegrees / 2) % 360, "toDegrees": (run["last"] * sectorDegrees + sectorDegrees / 2) % 360,
      "why": run["why"],
    }
    for run in runs
  ]


def inView(eye, points):
  """How many of points fall inside the frame a view renders at (previewFrame) from an eye looking at their middle."""
  middle = points.mean(axis=0)
  if not numpy.linalg.norm(middle - eye) > 0:
    return 0
  pixels, ahead = projector({"eye": eye, "target": middle}, (previewFrame.renderWidth, previewFrame.renderHeight))(points)
  inside = (pixels[:, 0] >= 0) & (pixels[:, 0] <= previewFrame.renderWidth) & (pixels[:, 1] >= 0) & (pixels[:, 1] <= previewFrame.renderHeight)
  return int((ahead & inside).sum())


def sectorsApart(sector, chosen):
  """How many sectors round a sector lies from the nearest chosen one, the whole circle when none is."""
  return min((min((sector - other) % bearings, (other - sector) % bearings) for other in chosen), default=bearings)


def judge(subject, candidates, plan, placed, front, clear, count):
  """What the sights found (clear, in sights' order over front): each side's seen share, the unseen bearing runs, and up to count eyes
  that see the subject, the best eye of each sector it is seen from (the most points it sees inside its view, aimed at their middle,
  then the nearest framingRadii of its radii from its middle), spread round it (each next the sector farthest round from those
  chosen), in bearing order, each standing where placed (standing points and eyes of the eyes tried) and looking at the middle of the
  points it sees, with the share it sees of the points facing it. Returns (the judgment, which points any eye sees, which points each
  chosen eye sees)."""
  standing, eyePoints = placed
  seen = numpy.zeros(front.shape, dtype=bool)
  seen[front] = numpy.asarray(clear, dtype=bool)
  pointSeen = seen.any(axis=0)
  sides = {}
  for side, name in enumerate(sideNames):
    held = subject["side"] == side
    sides[name] = round(float(pointSeen[held].mean()), 3) if held.any() else None
  sees, faced = seen.sum(axis=1), front.sum(axis=1)
  sectorSeen = numpy.zeros(bearings, dtype=bool)
  sectorSeen[plan["sector"][sees > 0]] = True
  best = {}
  framing = framingRadii * subject["radius"]
  for index in numpy.flatnonzero(sees > 0):
    sector = int(plan["sector"][index])
    rank = (-inView(eyePoints[index], subject["points"][seen[index]]), abs(float(plan["distance"][index]) - framing), int(index))
    if sector not in best or rank < best[sector][0]:
      best[sector] = (rank, int(index))
  chosen = []
  while len(chosen) < min(count, len(best)):
    chosen.append(max(
      (sector for sector in best if sector not in chosen),
      key=lambda sector: (sectorsApart(sector, chosen), -best[sector][0][0], -best[sector][0][1], -sector),
    ))
  eyes, eyeSeen = [], []
  for number, sector in enumerate(sorted(chosen, key=lambda sector: plan["bearing"][best[sector][1]]), 1):
    index = best[sector][1]
    candidate = plan["tried"][index]
    bearing = float(plan["bearing"][index])
    eyeSeen.append(seen[index])
    eyes.append({
      "number": number, "label": playerReach.labelNames[candidates["label"][candidate]], "swimming": bool(candidates["swimming"][candidate]),
      "standing": roundedPoint(standing[index]), "eye": roundedPoint(eyePoints[index]), "bearingDegrees": round(bearing, 1),
      "direction": directionName(bearing), "distance": round(float(plan["distance"][index]), 1),
      "seenShare": round(float(sees[index]) / float(faced[index]), 3),
      "view": {"eye": roundedPoint(eyePoints[index]), "target": roundedPoint(subject["points"][seen[index]].mean(axis=0))},
    })
  judged = {
    "sides": sides, "seenShare": round(float(pointSeen.mean()), 3), "unseenSectors": unseenRuns(sectorSeen, plan["sectorHasEyes"]),
    "eyesTried": len(plan["tried"]), "eyesSeeing": int((sees > 0).sum()), "eyes": eyes,
  }
  return judged, pointSeen, eyeSeen


def describeSubject(subject):
  return {
    "kind": subject["kind"], "name": subject["name"], "center": roundedPoint(subject["center"]),
    "bounds": {"minimum": roundedPoint(subject["low"]), "maximum": roundedPoint(subject["high"])}, "radius": round(subject["radius"], 2),
    "points": len(subject["points"]),
  }


def postureOf(eye):
  return "swim" if eye["swimming"] else "wade" if eye["label"] == "swim" else eye["label"]


def tourLabel(eye):
  return f"{eye['number']}: {postureOf(eye)}, facing {eye['direction']}, reached ground {eye['run']:g} ahead"


def targetLabel(eye):
  return f"{eye['number']}: {postureOf(eye)}, from the {eye['direction']}, {eye['distance']:g} away, sees {eye['seenShare']:.0%} of what faces it"


def projector(view, size):
  """Where zone points fall in a view rendered at size with previewFrame's field: (pixels, in front of the eye) for [n, 3] points."""
  eye, target = numpy.asarray(view["eye"], dtype=numpy.float64), numpy.asarray(view["target"], dtype=numpy.float64)
  forward = (target - eye) / numpy.linalg.norm(target - eye)
  right = numpy.cross(forward, [0.0, 0.0, 1.0])
  right /= numpy.linalg.norm(right)
  up = numpy.cross(right, forward)
  focal = size[1] / 2 / math.tan(math.radians(previewFrame.verticalFieldOfViewDegrees) / 2)

  def project(points):
    relative = numpy.asarray(points, dtype=numpy.float64).reshape(-1, 3) - eye
    depth = relative @ forward
    ahead = depth > 0
    safe = numpy.where(ahead, depth, 1.0)
    return numpy.stack([size[0] / 2 + focal * (relative @ right) / safe, size[1] / 2 - focal * (relative @ up) / safe], axis=1), ahead

  return project


def markedLine(draw, points):
  draw.line(points, fill=markShadow, width=markWidth + 4, joint="curve")
  draw.line(points, fill=markColor, width=markWidth, joint="curve")


def markTarget(image, view, subject, seen, outlineRuns):
  """Mark on a target's view what it looks at: a region's outline along its floor; a point's player as a line from his feet to his head,
  ticked across at both; brackets round the points of an object the eye sees."""
  draw = ImageDraw.Draw(image)
  project = projector(view, image.size)
  if subject["kind"] == "region":
    for run in outlineRuns:
      pixels, ahead = project(run)
      for first, second, both in zip(pixels[:-1], pixels[1:], ahead[:-1] & ahead[1:]):
        if both:
          markedLine(draw, [tuple(first), tuple(second)])
    return
  if subject["kind"] == "point":
    (feet, head), ahead = project([subject["low"], subject["high"]])
    if ahead.all():
      markedLine(draw, [tuple(feet), tuple(head)])
      for end in (feet, head):
        markedLine(draw, [(end[0] - markArm / 2, end[1]), (end[0] + markArm / 2, end[1])])
    return
  pixels, ahead = project(subject["points"][seen])
  if not ahead.any():
    return
  low = numpy.maximum(pixels[ahead].min(axis=0) - markMargin, 2)
  high = numpy.minimum(pixels[ahead].max(axis=0) + markMargin, numpy.array(image.size) - 3)
  arm = min(markArm, float((high - low).min()) / 2)
  for cornerX, signX in ((low[0], 1), (high[0], -1)):
    for cornerY, signY in ((low[1], 1), (high[1], -1)):
      markedLine(draw, [(cornerX + signX * arm, cornerY), (cornerX, cornerY), (cornerX, cornerY + signY * arm)])


def planFraming(points, margin, least):
  """A plan's center and the extents it must hold across (along y) and up (along x): the points' bounds, grown by margin each side,
  and never under least."""
  points = numpy.asarray(points, dtype=numpy.float64)
  low, high = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
  center = (low + high) / 2
  return [float(center[0]), float(center[1])], max(float(high[1] - low[1]) * (1 + 2 * margin), least), max(float(high[0] - low[0]) * (1 + 2 * margin), least)


def arcBand(center, inner, outer, fromDegrees, toDegrees):
  """The pixels outlining a band round a plan point between two radii (pixels) over a run of headings."""
  end = toDegrees if toDegrees > fromDegrees else toDegrees + 360
  headings = numpy.linspace(fromDegrees, end, max(2, round((end - fromDegrees) / 3)))
  outward = [planDrawing.pixelDirection(heading) for heading in headings]
  return [(center[0] + x * outer, center[1] + y * outer) for x, y in outward] + [(center[0] + x * inner, center[1] + y * inner) for x, y in outward[::-1]]


class EyePlan(laneDrawing.LanePlan):
  """A lane plan (renderSketch's lane layer) with the eyes marked: each a numbered yellow disc with an arrow the way its view looks; for
  a target, its outline (a region's), bounds (an object's), or a ring (a point's player), its points (filled where a player sees them,
  hollow where none does), and the bearings no player sees it from as a band round it, red where players stand but none sees it and dark where no reached
  floor lies within the far sight. The grid's labels give way to the eyes."""

  def __init__(self, analysis, description, eyes, subject=None, unseen=(), pointSeen=None):
    super().__init__(analysis, description, None, False)
    self.eyes, self.subject, self.unseen, self.pointSeen = eyes, subject, unseen, pointSeen

  def eyeMarks(self, frame):
    """Each eye's pixel, the pixel direction it looks, and its arrow's tip."""
    marks = []
    for eye in self.eyes:
      x, y = frame.pixel(eye["eye"])
      if self.subject is None:
        heading = eye["headingDegrees"]
      else:
        heading = float(headingOf(eye["view"]["target"][0] - eye["eye"][0], eye["view"]["target"][1] - eye["eye"][1]))
      ahead = planDrawing.pixelDirection(heading)
      marks.append(((x, y), ahead, (x + ahead[0] * eyeArrowLength, y + ahead[1] * eyeArrowLength)))
    return marks

  def strokes(self, draw, frame):
    super().strokes(draw, frame)
    if self.subject is not None:
      self.drawSubject(draw, frame)
    for (x, y), ahead, tip in self.eyeMarks(frame):
      side = (-ahead[1], ahead[0])
      draw.line([(x, y), tip], fill=(*eyeOutline, 255), width=4)
      back = (tip[0] - ahead[0] * eyeArrowHead, tip[1] - ahead[1] * eyeArrowHead)
      wing = (side[0] * eyeArrowHead / 2, side[1] * eyeArrowHead / 2)
      draw.polygon([tip, (back[0] + wing[0], back[1] + wing[1]), (back[0] - wing[0], back[1] - wing[1])], fill=(*eyeOutline, 255))
      draw.ellipse([x - eyeRadius, y - eyeRadius, x + eyeRadius, y + eyeRadius], fill=(*eyeFill, 255), outline=(*eyeOutline, 255), width=3)

  def drawSubject(self, draw, frame):
    subject = self.subject
    center = frame.pixel(subject["center"])
    reach = frame.length(float(numpy.hypot(*(subject["high"][:2] - subject["low"][:2]))) / 2)
    inner = max(reach + arcGap, arcLeast)
    for run in self.unseen:
      color = blockedColor if run["why"] == "blocked" else noFloorColor
      draw.polygon(arcBand(center, inner, inner + arcWidth, run["fromDegrees"], run["toDegrees"]), fill=(*color, arcAlpha), outline=(*color, 255))
    if subject["kind"] == "point":
      laneDrawing.drawRing(draw, center, laneDrawing.problemRing)
    else:
      if subject["outline"] is not None:
        corners = [frame.pixel(point) for point in subject["outline"]]
      else:
        low, high = subject["low"], subject["high"]
        corners = [frame.pixel(point) for point in ((low[0], low[1]), (high[0], low[1]), (high[0], high[1]), (low[0], high[1]))]
      draw.polygon(corners, outline=(*targetHalo, 255), width=5)
      draw.polygon(corners, outline=(*targetColor, 255), width=2)
    for point, isSeen in sorted(zip(subject["points"], self.pointSeen), key=lambda pair: bool(pair[1])):
      x, y = frame.pixel(point)
      box = [x - targetPointRadius, y - targetPointRadius, x + targetPointRadius, y + targetPointRadius]
      draw.ellipse(box, fill=(*(targetColor if isSeen else targetHalo), 255), outline=(*(targetHalo if isSeen else targetColor), 255), width=1)

  def reserveMarks(self, board, frame):
    for (x, y), _, tip in self.eyeMarks(frame):
      board.reserve([x - eyeRadius - 2, y - eyeRadius - 2, x + eyeRadius + 2, y + eyeRadius + 2])
      board.reserve([min(x, tip[0]) - 4, min(y, tip[1]) - 4, max(x, tip[0]) + 4, max(y, tip[1]) + 4])

  def labels(self, board, frame):
    for eye in self.eyes:
      x, y = frame.pixel(eye["eye"])
      board.draw.text((x, y), str(eye["number"]), fill=eyeOutline, font=planDrawing.fontOf(eyeNumberSize), anchor="mm")
    super().labels(board, frame)

  def legend(self, frame=None):
    items = [("ring", eyeOutline, "eye, numbered as its view, its arrow the way it looks")]
    if self.subject is not None:
      kind = self.subject["kind"]
      outlined = {"point": "the player at the target point", "region": "the target region's outline"}.get(kind, "the target's bounds")
      items += [
        ("ring" if kind == "point" else "box", targetColor, outlined), ("dot", targetColor, "a point of the target a player sees"),
        ("hollowDot", targetColor, "a point of the target no player sees"),
        ("fill", blockedColor, "bearings no player sees it from, though players stand there"),
        ("fill", noFloorColor, "bearings with no reached floor within the far sight"),
      ]
    return items + super().legend(frame)
