"""The player's lane: every place in the zone players can reach from where they arrive, by the moves the Recast helper's reach mode
models (docs/zoneWorkflow.md, Player space: reach), read here with numpy, classified against the regions, and turned into numbered
problems. A lane is computed from what the zone ships (bridgeLane.laneInput) or from a zone's server files (laneFromServerFiles), and
kept under the tooling root by a key of everything it depends on, so an unchanged zone's lane is reused; the newest three are kept.
Reach never runs in Blender: the bridge only collects and draws.

Frames: the helper works in Recast axes (zone y, zone z, zone x); its columns run along Recast x (zone y) and z (zone x), and every
point given or returned here is in zone axes."""
import hashlib
import json
import math
import os
import struct
import time
from pathlib import Path

import numpy
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image, ImageDraw

import buildTolerances
import checkpoints
import eqAxes
import machineProfile
import playerScale
import recastHelper
import serverMapFiles
import toolingManifest

# Chosen by measurement (README, Recast helper): at cellSize 1 the canyonhousing copy (8,448 x 4,224) takes 14 s and 1.1 GB
# in the helper and Highpass Hold 1.4 s; 0.5 reaches the same area within 1% and takes the canyon to 56 s and 4.4 GB; 4 and 8 cut
# every face steeper than about 48 and 29 degrees, whose rise per column passes the step, and reach 77% and 9% of Highpass Hold. The
# helper centers its columns on multiples of cellSize.
reachGrid = {"cellSize": 1.0, "cellHeight": 0.25, "tileCells": 256}
keptLanes = 3
untitledFolder = "untitled"
serverFilesFolder = "serverFiles"
footingSearch = {"above": buildTolerances.groundSearchAbove, "below": buildTolerances.groundSearchDistance}
# Problems gather the columns they hold into blocks this wide, joined where blocks touch, so one leak is one problem.
problemBlock = 8.0
problemsListed = 20
# A problem is viewed from an eye problemViewFrame times its radius back from it, which frames it in the middle of a problem view's
# field, and never nearer than problemViewDistance; the eye is raised by each elevation in turn, steeper only where a lower one is
# blocked, and turned to each bearing (degrees from the side of the reached floor nearest it).
problemViewFrame = 3.0
problemViewDistance = 4 * playerScale.playerHeight
problemViewElevations = (30.0, 50.0, 70.0, 85.0)
problemViewBearings = (0, 45, -45, 90, -90, 135, -135, 180)
problemViewLift = 1.0

laneHeader = struct.Struct("<8s3f2f4iI")
laneMagic = b"ZWLANE01"
moveNames = ("unreached", "seed", "walk", "drop", "slide", "enterWater", "swim", "climbOut", "sink", "teleport")
labelNames = ("unreached", "walk", "swim", "slide", "squeeze", "zoneLine")
unreachedLabel, walkLabel, swimLabel, slideLabel, squeezeLabel, zoneLineLabel = range(6)
wetShape, walkableShape, absorbedShape, touchesLineShape, boundaryShape = 1, 2, 4, 8, 16
reachedFullState, reachedState, trapState, fromSafeState, toSafeState, openState = 1, 2, 4, 8, 16, 128
voidEvent, belowUnderworldEvent, absorbedEvent = 1, 2, 3
# The entries a lane starts from.
seedKinds = ("safePoint", "zoneIn", "landing")
problemKinds = (
  "voidEdge", "belowUnderworld", "noneRegion", "outsideRegions", "disconnectedEntry", "unreachedPlot", "trap", "viewRegion",
  "unreachedZoneLine", "unreachedSwim", "unreachedPlayFloor", "undecidedWater",
)
notModeled = [
  "jumps", "levitation", "the camera",
  "doors: taken as open, though the RoF2 client collides with a door in its closed position, so a closed door can block a way the lane shows",
  "NPCs", "players wider than a point (playerRadius is unmeasured): a gap narrower than a player reads as passable",
  "swimming depth: in the RoF2 walk the client swam a player only where the water over the floor was deeper than his avatarHeight (3.75"
  " for the walked human, playerScale.sources) and then floated him 0.10 under the box's top; avatarHeight is not in playerScale, so the"
  " lane swims in a box at any depth: shallow water's floor is labelled swim, and a swimmer there climbs out onto floor up to the box's"
  " top + stepHeight where a wading player climbs stepHeight over the floor, which errs toward reach",
]


def smallestPlayerHeight():
  return vars(playerScale).get("smallestPlayerHeight")


def movement():
  """The movement the lane floods by, from playerScale: the step, the design player's height, the walkable normal z, and the
  containment headroom (smallestPlayerHeight, or one cell height while it is unmeasured)."""
  smallest = smallestPlayerHeight()
  return {
    "stepHeight": playerScale.stepHeight, "playerHeight": playerScale.playerHeight, "walkableNormalZ": playerScale.walkableNormalZ,
    "containmentHeadroom": reachGrid["cellHeight"] if smallest is None else smallest,
  }


def constantsReport():
  """Each constant the lane used, with its value and its playerScale source."""
  used = {name: {"value": getattr(playerScale, name), "source": playerScale.sources[name]} for name in ("playerHeight", "walkableNormalZ", "steepestWalkableDegrees", "stepHeight")}
  smallest = smallestPlayerHeight()
  used["smallestPlayerHeight"] = (
    {"value": None, "source": f"absent: containment floods with one cell height ({reachGrid['cellHeight']:g}) of headroom, which only adds reach"}
    if smallest is None else {"value": smallest, "source": playerScale.sources["smallestPlayerHeight"]}
  )
  return used


def limits(lane, underworld, boundariesKnown):
  """What can make a lane wrong in either direction besides notModeled; boundariesKnown false for a lane from server files."""
  smallest = smallestPlayerHeight()
  headroom = movement()["containmentHeadroom"]
  stated = [
    f"a gap narrower than 2 x cellSize ({2 * lane.cellSize:g}) may rasterize closed and read as no way through: containment holds only for gaps at least that wide",
    f"containment floods with {headroom:g} of headroom ({'smallestPlayerHeight' if smallest is not None else 'one cell height, smallestPlayerHeight being unmeasured'}),"
    f" the design player with playerHeight {playerScale.playerHeight:g}; what only the smaller fits is labelled squeeze",
    f"players enter a swim box whose bottom lies within playerHeight ({playerScale.playerHeight:g}) of the floor, so a box floating higher is"
    " entered only from above: an assumption, as a taller race may reach a box set higher",
    f"swimming between columns takes {headroom:g} of open overlap, an assumption until the bot measures it",
    "a zone line takes a player whose floor point (the column's center, one cell height over the floor) lies in its box: the client's own"
    " test point is unmeasured, so the lane errs toward reach continuing",
    "unreached play floor is walkable floor in a play region that open space joins to the lane, at any rise: the lane is built from"
    " surfaces, so the inside of a closed solid holds floor no one reaches, and floor sealed off as it is (a sealed room) is left out"
    " with it; a boundary's top and floor under the underworld are never play floor",
  ]
  if not boundariesKnown:
    stated.append("server files do not say which collision is a boundary, so a lid's top that open space joins to the lane reads as unreached play floor")
  if underworld is None:
    stated.append("no underworld is set (setZoneProperties): only a fall onto nothing reads as a leak, never one below the underworld")
  return stated


def seedTolerance(normalZ):
  """How far a seed's floor node may lie from its footing: one cell height, and the rise of its face across a column's diagonal, since
  a column's floor is the highest point of the ground over it and the footing may lie in any corner."""
  slope = math.tan(math.acos(min(1.0, max(normalZ, 1e-6))))
  return reachGrid["cellHeight"] + reachGrid["cellSize"] * math.sqrt(2) * slope


def zoneBox(center, halfExtents):
  center, half = numpy.asarray(center, dtype=numpy.float64), numpy.abs(numpy.asarray(halfExtents, dtype=numpy.float64))
  return (center - half).tolist(), (center + half).tolist()


def recastBox(low, high):
  corners = eqAxes.recastFromZone(numpy.array([low, high], dtype=numpy.float64))
  return {"low": corners.min(axis=0).tolist(), "high": corners.max(axis=0).tolist()}


def quantized(values):
  return numpy.rint(numpy.asarray(values, dtype=numpy.float64) * 1000).astype(numpy.int64)


def laneKey(fingerprint, triangles, boundary, description):
  """SHA-256 of the helper's build, the grid, the movement and footing search, the triangles (rounded to 1e-3, each with whether it is a
  boundary's, rows sorted), and the boxes, zone lines and their landings, seeds, and underworld."""
  rows = numpy.concatenate([quantized(triangles).reshape(-1, 9), numpy.asarray(boundary, dtype=numpy.int64).reshape(-1, 1)], axis=1)
  rows = rows[numpy.lexsort(rows.T[::-1])] if len(rows) else rows
  digest = hashlib.sha256()
  digest.update(fingerprint.encode("ascii"))
  digest.update(json.dumps([reachGrid, movement(), footingSearch], sort_keys=True).encode("ascii"))
  digest.update(rows.tobytes())
  rounded = {
    "swimBoxes": [quantized([box["low"], box["high"]]).tolist() for box in description["swimBoxes"]],
    "zoneLines": [[quantized([line["low"], line["high"]]).tolist(), None if line["landing"] is None else quantized(line["landing"]).tolist()] for line in description["zoneLines"]],
    "seeds": [[seed["name"], quantized(seed["footing"]).tolist(), round(seed["tolerance"], 6)] for seed in description["seeds"]],
    "underworld": description["underworld"],
  }
  digest.update(json.dumps(rounded, sort_keys=True).encode("utf-8"))
  return digest.hexdigest()


def helperFingerprint(toolingRoot):
  status = recastHelper.helperStatus(toolingRoot, toolingManifest.loadManifest()["recast"])
  if status["state"] != "built":
    problem = f": {status['problem']}" if "problem" in status else ""
    raise ToolError(f"The Recast helper is {status['state']}{problem}; run syncTooling to build it")
  return status["fingerprint"]


def reachRoot(toolingRoot):
  return toolingRoot / "reach"


def laneFolder(toolingRoot, workFile):
  """Where a work file's lanes are kept: reach/<name>-<hash of its full path>, as checkpoints are, or reach/untitled."""
  return reachRoot(toolingRoot) / (untitledFolder if workFile is None else checkpoints.workFileKey(workFile))


def keepNewest(folder):
  lanes = sorted(folder.glob("lane-*.bin"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
  for stale in lanes[keptLanes:]:
    stale.unlink()


def requireTriangles(triangles):
  if len(triangles) == 0:
    raise ToolError("The zone ships no collision triangles, so players stand on nothing: there is no lane")


def computeLane(toolingRoot, folder, triangles, boundary, description, reportProgress):
  """The lane for collision triangles (zone axes, [n, 3, 3]), which of them are a boundary's ([n] booleans), and a description
  (swimBoxes, zoneLines, seeds, underworld), reused when one of its key is kept: (Lane, "computed" or "reused", seconds)."""
  started = time.perf_counter()
  requireTriangles(triangles)
  if not description["seeds"]:
    raise ToolError("The lane has no seeds: set the safe point (setZoneProperties safePoint) or placeEntry where players arrive")
  key = laneKey(helperFingerprint(toolingRoot), triangles, boundary, description)
  folder.mkdir(parents=True, exist_ok=True)
  lanePath = folder / f"lane-{key}.bin"
  if lanePath.is_file():
    os.utime(lanePath)
    return Lane(lanePath), "reused", round(time.perf_counter() - started, 2)
  safeSeeds = [index for index, seed in enumerate(description["seeds"]) if seed["kind"] == "safePoint"]
  inputBytes = recastHelper.reachInput(
    eqAxes.recastFromZone(numpy.asarray(triangles, dtype=numpy.float32)), numpy.asarray(boundary, dtype=bool), reachGrid, movement(), footingSearch,
    description["underworld"],
    machineProfile.workerCount(),
    [recastBox(box["low"], box["high"]) for box in description["swimBoxes"]],
    [recastBox(line["low"], line["high"]) | {"target": None if line["landing"] is None else eqAxes.recastFromZone(numpy.array(line["landing"])).tolist()} for line in description["zoneLines"]],
    [{"name": seed["name"], "point": eqAxes.recastFromZone(numpy.array(seed["footing"])).tolist(), "tolerance": seed["tolerance"]} for seed in description["seeds"]],
    safeSeeds[0] if safeSeeds else None,
  )
  partialPath = folder / f"lane-{key}.bin.partial"
  try:
    _, report = recastHelper.runHelper(toolingRoot, "reach", inputBytes, reportProgress, partialPath)
    os.replace(partialPath, lanePath)
  finally:
    partialPath.unlink(missing_ok=True)
  keepNewest(folder)
  lane = Lane(lanePath)
  lane.report = report
  return lane, "computed", round(time.perf_counter() - started, 2)


class Lane:
  """A lane file (ZWLANE01): the grid, each column's nodes, and each node's floor and ceiling (cells), swim box, shape, flood state,
  origin seed, predecessor, and move; then the void, underworld, and zone line events, each zone line's state, and each seed's node.
  Arrays are read on demand; the file is read to its last byte or refused."""

  def __init__(self, path):
    self.path = Path(path)
    self.report = None
    size = self.path.stat().st_size
    with self.path.open("rb") as source:
      header = source.read(laneHeader.size)
    if len(header) < laneHeader.size or header[:8] != laneMagic:
      raise ValueError(f"{self.path} is not a lane file")
    _, *origin, self.cellSize, self.cellHeight, self.tileCells, self.tilesX, self.tilesZ, self.verticalCells, self.nodeCount = laneHeader.unpack(header)
    self.origin = numpy.array(origin, dtype=numpy.float64)
    self.columnsX, self.columnsZ = self.tilesX * self.tileCells, self.tilesZ * self.tileCells
    self.columns = self.columnsX * self.columnsZ
    count = self.nodeCount
    offset = laneHeader.size
    self.layout = {}
    for name, dtype, length in (
      ("columnStart", "<u4", self.columns + 1), ("low", "<u2", count), ("high", "<u2", count), ("box", "<i2", count), ("shape", "u1", count),
      ("state", "u1", count), ("originSeed", "<u2", count), ("predecessor", "<i4", count), ("move", "u1", count),
    ):
      self.layout[name] = (offset, numpy.dtype(dtype), length)
      offset += numpy.dtype(dtype).itemsize * length
    eventCount = self.tail(offset, "<u4", 1, size)[0]
    self.events = self.tail(offset + 4, "<i4", eventCount * 5, size).reshape(-1, 5)
    offset += 4 + eventCount * 20
    lineCount = self.tail(offset, "<u4", 1, size)[0]
    lines = self.tail(offset + 4, "<i4", lineCount * 2, size).reshape(-1, 2)
    self.lineReached, self.lineLanding = lines[:, 0].astype(bool), lines[:, 1]
    offset += 4 + lineCount * 8
    seedCount = self.tail(offset, "<u4", 1, size)[0]
    self.seedNodes = self.tail(offset + 4, "<i4", seedCount, size)
    offset += 4 + seedCount * 4
    if offset != size:
      raise ValueError(f"{self.path}: the lane ends at byte {offset} but the file holds {size}")

  def tail(self, offset, dtype, count, size):
    width = numpy.dtype(dtype).itemsize * count
    if offset + width > size:
      raise ValueError(f"{self.path}: {count} values of {dtype} at byte {offset} run past its end at {size}")
    return numpy.fromfile(self.path, dtype=dtype, count=count, offset=offset)

  def read(self, name):
    offset, dtype, length = self.layout[name]
    return numpy.fromfile(self.path, dtype=dtype, count=length, offset=offset)

  def columnPlaces(self, columns):
    """Columns' (x, z) indices along Recast x (zone y) and z (zone x), from their tile-major numbers."""
    perTile = self.tileCells * self.tileCells
    tile, within = numpy.divmod(columns, perTile)
    x = (tile % self.tilesX) * self.tileCells + within % self.tileCells
    z = (tile // self.tilesX) * self.tileCells + within // self.tileCells
    return x.astype(numpy.int32), z.astype(numpy.int32)

  def columnAt(self, point):
    """The column under a zone point, or None outside the grid."""
    x = int(math.floor((point[1] - self.origin[0]) / self.cellSize))
    z = int(math.floor((point[0] - self.origin[2]) / self.cellSize))
    if not (0 <= x < self.columnsX and 0 <= z < self.columnsZ):
      return None
    perTile = self.tileCells * self.tileCells
    return (z // self.tileCells * self.tilesX + x // self.tileCells) * perTile + (z % self.tileCells) * self.tileCells + x % self.tileCells

  def height(self, cells):
    return self.origin[1] + numpy.asarray(cells, dtype=numpy.float64) * self.cellHeight


def sceneDescription(inputPath):
  """The lane's triangles, which of them are a boundary's, and its description from bridgeLane.laneInput's file, refusing what no lane
  can start from: a stored entry off its footing, a seed without footing."""
  with numpy.load(inputPath, allow_pickle=False) as loaded:
    triangles, boundary = loaded["collision"], loaded["boundary"]
    requireTriangles(triangles)
    scene = json.loads(str(loaded["description"]))
    bodies = [{"name": name, "triangles": loaded[f"undecided{index}"]} for index, name in enumerate(scene["undecidedBodies"])]
  seeds, plots = [], []
  for entry in scene["entries"]:
    if entry["kind"] in ("zoneIn", "landing"):
      if entry["state"] != "onFooting":
        raise ToolError(f"Entry '{entry['name']}' stands off its footing ({entry['distance']} from {entry['footing']}): place it again (placeEntry) before mapping reach")
    if entry["kind"] in seedKinds:
      if entry["seedFooting"] is None:
        raise ToolError(f"The {'safe point' if entry['kind'] == 'safePoint' else 'entry ' + repr(entry['name'])} at {entry['at']} has no footing on what the zone ships, so the lane has no node there to start from")
      seeds.append({
        "name": entry["name"], "kind": entry["kind"], "point": entry["at"], "footing": entry["seedFooting"]["point"],
        "tolerance": seedTolerance(entry["seedFooting"]["normalZ"]), "isolated": entry["kind"] != "safePoint" and entry["isolated"],
        "headingDegrees": entry["headingDegrees"],
      })
    elif entry["kind"] == "plotEntrance":
      plots.append({"name": entry["name"], "point": entry["at"]})
  seeds.sort(key=lambda seed: seed["kind"] != "safePoint")
  zoneLines = []
  for line in scene["zoneLines"]:
    low, high = zoneBox(line["center"], line["halfExtents"])
    zoneLines.append({"name": line["name"], "low": low, "high": high, "landing": line["landing"], "target": line["target"]})
  swimBoxes = []
  for box in scene["swimBoxes"]:
    low, high = zoneBox(box["center"], box["halfExtents"])
    swimBoxes.append({"name": box["name"], "low": low, "high": high})
  return triangles, boundary, {
    "swimBoxes": swimBoxes, "zoneLines": zoneLines, "seeds": seeds, "plotEntrances": plots, "regions": scene["regions"],
    "underworld": scene["underworld"], "undecidedBodies": bodies, "notFollowed": scene["notFollowed"], "boundariesKnown": True,
  }


def currentLane(toolingRoot, workFile, inputPath, reportProgress):
  """The lane of the open scene, from bridgeLane.laneInput's file: (Lane, description, "computed" or "reused", seconds). Every consumer
  takes the lane from here, so nothing is recomputed silently."""
  triangles, boundary, description = sceneDescription(inputPath)
  lane, status, seconds = computeLane(toolingRoot, laneFolder(toolingRoot, workFile), triangles, boundary, description, reportProgress)
  description["bounds"] = shippedBounds(triangles)
  return lane, description, status, seconds


def shippedBounds(triangles):
  points = numpy.asarray(triangles, dtype=numpy.float64).reshape(-1, 3)
  return {"minimum": [round(float(value), 2) for value in points.min(axis=0)], "maximum": [round(float(value), 2) for value in points.max(axis=0)]}


def heightsUnder(triangles, x, y):
  """Where a vertical line through (x, y) meets triangles (zone axes, [n, 3, 3]), upright ones aside: each height, and the |normal z| of
  its triangle."""
  corners = numpy.asarray(triangles, dtype=numpy.float64)
  low, high = corners[:, :, :2].min(axis=1), corners[:, :, :2].max(axis=1)
  near = (low[:, 0] <= x) & (high[:, 0] >= x) & (low[:, 1] <= y) & (high[:, 1] >= y)
  a, b, c = corners[near, 0], corners[near, 1], corners[near, 2]
  normal = numpy.cross(b - a, c - a)
  flat = numpy.abs(normal[:, 2]) > 1e-12
  a, b, c, normal = a[flat], b[flat], c[flat], normal[flat]
  denominator = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
  u = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / denominator
  v = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / denominator
  w = 1 - u - v
  hit = (u >= -1e-9) & (v >= -1e-9) & (w >= -1e-9)
  z = u * a[:, 2] + v * b[:, 2] + w * c[:, 2]
  return z[hit], numpy.abs(normal[hit, 2]) / numpy.linalg.norm(normal[hit], axis=1)


def footingOnTriangles(triangles, point):
  """The highest footing on triangles (zone axes) under a point, from footingSearch's above over it down to its below under it: (point,
  |normal z|), or None."""
  x, y = point[0], point[1]
  z, normalZ = heightsUnder(triangles, x, y)
  hit = (z <= point[2] + footingSearch["above"]) & (z >= point[2] - footingSearch["below"])
  if not hit.any():
    return None
  best = int(numpy.argmax(numpy.where(hit, z, -numpy.inf)))
  return [float(x), float(y), float(z[best])], float(normalZ[best])


def laneFromServerFiles(mapBytes, waterBytes, seeds, toolingRoot, reportProgress, regions=None, plots=None):
  """The lane a zone's server files give: the collision the server rebuilds from the .map (zone axes) and the .wtr's boxes, water and
  lava as swim boxes and zone lines as zone lines, each negative extent swapped as the server swaps it. seeds are [{name, point (zone
  axes), kind ("safePoint", "zoneIn", or "landing"), isolated, and headingDegrees or none}], each standing on the footing found under its point; regions as
  getRegions gives them and plot entrances [{name, point}] are optional. A record of another type is listed, not modelled. Returns
  (Lane, description, "computed" or "reused", seconds)."""
  triangles = eqAxes.zoneFromServer(serverMapFiles.mapCollision(mapBytes))
  swimBoxes, zoneLines, ignored = [], [], []
  for index, record in enumerate(serverMapFiles.readWater(waterBytes)):
    if any(float(value) != 0.0 for value in record["rotation"]):
      raise ToolError(f".wtr record {index} is turned {tuple(record['rotation'])} degrees; the lane takes unturned boxes")
    low, high = zoneBox(record["position"], numpy.asarray(record["halfExtents"]) * numpy.asarray(record["scale"]))
    name = f".wtr record {index} ({serverMapFiles.waterRegionTypeNames.get(record['type'], record['type'])})"
    if record["type"] in (1, 2):
      swimBoxes.append({"name": name, "low": low, "high": high})
    elif record["type"] == 3:
      zoneLines.append({"name": name, "low": low, "high": high, "landing": None, "target": None})
    else:
      ignored.append(name)
  described = []
  for seed in seeds:
    found = footingOnTriangles(triangles, seed["point"])
    if found is None:
      raise ToolError(f"Seed '{seed['name']}' at {list(seed['point'])} has no footing in the collision from {footingSearch['above']:g} over it to {footingSearch['below']:g} under it")
    described.append({
      "name": seed["name"], "kind": seed["kind"], "point": list(seed["point"]), "footing": found[0], "tolerance": seedTolerance(found[1]),
      "isolated": bool(seed.get("isolated", False)), "headingDegrees": seed.get("headingDegrees"),
    })
  described.sort(key=lambda seed: seed["kind"] != "safePoint")
  description = {
    "swimBoxes": swimBoxes, "zoneLines": zoneLines, "seeds": described, "plotEntrances": list(plots or []), "regions": list(regions or []),
    "underworld": None, "undecidedBodies": [], "notFollowed": [], "ignoredRegions": ignored, "boundariesKnown": False,
  }
  boundary = numpy.zeros(len(triangles), dtype=bool)
  lane, status, seconds = computeLane(toolingRoot, reachRoot(toolingRoot) / serverFilesFolder, triangles, boundary, description, reportProgress)
  description["bounds"] = shippedBounds(triangles)
  return lane, description, status, seconds


class Nodes:
  """A lane's nodes as arrays: each one's plan cell (x along Recast x, zone y; z along Recast z, zone x), height (a floor node's floor,
  a wet node's the middle of its water), and label, with the lane's per-node state."""

  def __init__(self, lane):
    self.lane = lane
    self.columnStart = lane.read("columnStart")
    counts = numpy.diff(self.columnStart)
    side = numpy.arange(lane.tileCells, dtype=numpy.uint16)
    tiles = numpy.arange(lane.tilesX * lane.tilesZ)
    tileX = ((tiles % lane.tilesX) * lane.tileCells).astype(numpy.uint16)
    tileZ = ((tiles // lane.tilesX) * lane.tileCells).astype(numpy.uint16)
    self.x = numpy.repeat((tileX[:, None, None] + side[None, None, :] + numpy.zeros((1, lane.tileCells, 1), dtype=numpy.uint16)).ravel(), counts)
    self.z = numpy.repeat((tileZ[:, None, None] + side[None, :, None] + numpy.zeros((1, 1, lane.tileCells), dtype=numpy.uint16)).ravel(), counts)
    del counts
    self.low, self.high = lane.read("low"), lane.read("high")
    self.box, self.shape, self.state = lane.read("box"), lane.read("shape"), lane.read("state")
    self.wet = (self.shape & wetShape) != 0
    self.walkable = (self.shape & walkableShape) != 0
    self.reached = (self.state & reachedState) != 0
    self.trap = (self.state & trapState) != 0
    middle = numpy.where(self.wet, (self.low.astype(numpy.float32) + self.high.astype(numpy.float32)) / 2, self.low.astype(numpy.float32))
    self.height = (lane.origin[1] + middle * lane.cellHeight).astype(numpy.float32)
    del middle
    label = numpy.zeros(lane.nodeCount, dtype=numpy.uint8)
    label[self.reached & ~self.walkable] = slideLabel
    label[self.reached & self.walkable] = walkLabel
    label[self.reached & (self.wet | (self.box >= 0))] = swimLabel
    label[self.reached & ((self.state & reachedFullState) == 0)] = squeezeLabel
    label[self.reached & ((self.shape & absorbedShape) != 0)] = zoneLineLabel
    self.label = label

  def plan(self, chosen):
    """Chosen nodes' column centers in zone x and y."""
    return self.lane.origin[2] + (self.z[chosen] + 0.5) * self.lane.cellSize, self.lane.origin[0] + (self.x[chosen] + 0.5) * self.lane.cellSize

  def floorNodeAt(self, point, tolerance=None):
    """The floor node under a zone point: at [x, y] the highest in its column; at [x, y, z] the one nearest z within tolerance, or, without
    one, the highest from footingSearch's above over z down to its below under it. None where there is none."""
    column = self.lane.columnAt(point)
    if column is None:
      return None
    nodes = numpy.arange(self.columnStart[column], self.columnStart[column + 1])
    nodes = nodes[~self.wet[nodes]]
    if not len(nodes):
      return None
    if len(point) == 2:
      return int(nodes[-1])
    heights = self.height[nodes]
    if tolerance is not None:
      distance = numpy.abs(heights - point[2])
      best = int(numpy.argmin(distance))
      return int(nodes[best]) if distance[best] <= tolerance else None
    inside = (heights <= point[2] + footingSearch["above"]) & (heights >= point[2] - footingSearch["below"])
    return int(nodes[inside][-1]) if inside.any() else None


def polygonMask(lane, outline):
  """Which columns' centers lie inside an outline ([x, y] zone points) or on it, as a (columnsZ, columnsX) array: along each row of
  columns, every edge crossing the row's center line turns the columns past it in or out; and a column whose center lies on an edge is
  in, so an outline drawn on whole coordinates, through the columns' centers, holds the columns along both its sides alike."""
  start = numpy.asarray(outline, dtype=numpy.float64)
  end = numpy.roll(start, -1, axis=0)
  rows = lane.origin[2] + (numpy.arange(lane.columnsZ) + 0.5) * lane.cellSize
  low, high = numpy.minimum(start[:, 0], end[:, 0]), numpy.maximum(start[:, 0], end[:, 0])
  edge, row = numpy.nonzero((low[:, None] <= rows[None, :]) & (rows[None, :] < high[:, None]))
  share = (rows[row] - start[edge, 0]) / (end[edge, 0] - start[edge, 0])
  crossing = start[edge, 1] + share * (end[edge, 1] - start[edge, 1])
  first = numpy.clip(numpy.ceil((crossing - lane.origin[0]) / lane.cellSize - 0.5), 0, lane.columnsX).astype(numpy.int64)
  turns = numpy.zeros((lane.columnsZ, lane.columnsX + 1), dtype=bool)
  numpy.bitwise_xor.at(turns, (row, first), True)
  mask = numpy.logical_xor.accumulate(turns, axis=1)[:, :lane.columnsX]
  for (x1, y1), (x2, y2) in zip(start, end):
    onRows = numpy.arange(math.ceil((min(x1, x2) - lane.origin[2]) / lane.cellSize - 0.5 - 1e-6), math.floor((max(x1, x2) - lane.origin[2]) / lane.cellSize - 0.5 + 1e-6) + 1)
    onRows = onRows[(onRows >= 0) & (onRows < lane.columnsZ)]
    if x1 == x2:
      across = numpy.arange(math.ceil((min(y1, y2) - lane.origin[0]) / lane.cellSize - 0.5 - 1e-6), math.floor((max(y1, y2) - lane.origin[0]) / lane.cellSize - 0.5 + 1e-6) + 1)
      across = across[(across >= 0) & (across < lane.columnsX)]
      mask[numpy.ix_(onRows, across)] = True
      continue
    column = (y1 + (rows[onRows] - x1) * (y2 - y1) / (x2 - x1) - lane.origin[0]) / lane.cellSize - 0.5
    nearest = numpy.rint(column).astype(numpy.int64)
    centered = (numpy.abs(column - nearest) < 1e-6) & (nearest >= 0) & (nearest < lane.columnsX)
    mask[onRows[centered], nearest[centered]] = True
  return mask


def outlineArea(outline):
  points = numpy.asarray(outline, dtype=numpy.float64)
  return abs(float(numpy.dot(points[:, 0], numpy.roll(points[:, 1], -1)) - numpy.dot(points[:, 1], numpy.roll(points[:, 0], -1)))) / 2


def precedence(regions):
  """Region indices in the order a point is judged against them: the smallest in plan first, so a region drawn inside a broader one
  (a none room in a play area) holds what lies in it."""
  return sorted(range(len(regions)), key=lambda index: (outlineArea(regions[index]["outline"]), index))


def nodeRegions(nodes, regions):
  """Each node's region (its index in regions, -1 for none): the first, in precedence, whose outline holds its column's center and
  whose height band holds its height. Each column keeps the first region over it; nodes whose column more than one region covers are
  judged region by region."""
  found = numpy.full(nodes.lane.nodeCount, -1, dtype=numpy.int16)
  if not regions:
    return found
  first = numpy.full((nodes.lane.columnsZ, nodes.lane.columnsX), -1, dtype=numpy.int16)
  shared = numpy.zeros(first.shape, dtype=bool)
  for index in precedence(regions):
    mask = polygonMask(nodes.lane, regions[index]["outline"])
    shared |= mask & (first >= 0)
    first[mask & (first < 0)] = index
  candidate = first[nodes.z, nodes.x]
  del first
  bottoms = numpy.array([region["bottom"] for region in regions] + [numpy.inf], dtype=numpy.float32)
  tops = numpy.array([region["top"] for region in regions] + [-numpy.inf], dtype=numpy.float32)
  inside = (nodes.height >= bottoms[candidate]) & (nodes.height <= tops[candidate])
  found[inside] = candidate[inside]
  del candidate, inside
  crowded = numpy.flatnonzero(shared[nodes.z, nodes.x])
  del shared
  if len(crowded):
    found[crowded] = -1
    for index in precedence(regions):
      region = regions[index]
      pending = crowded[found[crowded] < 0]
      holds = polygonMask(nodes.lane, region["outline"])[nodes.z[pending], nodes.x[pending]]
      holds &= (nodes.height[pending] >= region["bottom"]) & (nodes.height[pending] <= region["top"])
      found[pending[holds]] = index
  return found


def groups(lane, x, z):
  """Group columns (x, z indices) into problems: blocks of problemBlock units holding any, joined where blocks touch by a side or a
  corner. Returns each column's group number (0 up)."""
  block = max(1, round(problemBlock / lane.cellSize))
  blockX, blockZ = x // block + 1, z // block + 1
  width = int(blockX.max()) + 2
  keys, member = numpy.unique(blockZ * width + blockX, return_inverse=True)
  firsts, seconds = [], []
  for step in (1, width - 1, width, width + 1):
    found = numpy.searchsorted(keys, keys + step)
    hit = (found < len(keys)) & (keys[numpy.minimum(found, len(keys) - 1)] == keys + step)
    firsts.append(numpy.flatnonzero(hit))
    seconds.append(found[hit])
  first, second = numpy.concatenate(firsts), numpy.concatenate(seconds)
  labels = numpy.arange(len(keys))
  while True:
    lower = numpy.minimum(labels[first], labels[second])
    before = labels.copy()
    numpy.minimum.at(labels, labels[first], lower)
    numpy.minimum.at(labels, labels[second], lower)
    while True:
      jumped = labels[labels]
      if numpy.array_equal(jumped, labels):
        break
      labels = jumped
    if numpy.array_equal(labels, before):
      break
  _, numbered = numpy.unique(labels[member.ravel()], return_inverse=True)
  return numbered.ravel()


def describeGroup(kind, lane, planX, planY, height, regionIndex, regions, length=False):
  """A problem: its kind, center (the member nearest the members' middle, so it lies on the problem) and extent (zone axes), its area
  (nodes) or length (edge runs), and the region most of it lies in."""
  nearest = int(numpy.argmin(numpy.hypot(planX - planX.mean(), planY - planY.mean())))
  center = [round(float(planX[nearest]), 1), round(float(planY[nearest]), 1), round(float(height[nearest]), 1)]
  extent = {
    "minimum": [round(float(planX.min()) - lane.cellSize / 2, 1), round(float(planY.min()) - lane.cellSize / 2, 1), round(float(height.min()), 1)],
    "maximum": [round(float(planX.max()) + lane.cellSize / 2, 1), round(float(planY.max()) + lane.cellSize / 2, 1), round(float(height.max()), 1)],
  }
  inside = regionIndex[regionIndex >= 0]
  region = regions[int(numpy.bincount(inside).argmax())]["name"] if len(inside) else None
  problem = {"kind": kind, "center": center, "extent": extent, "region": region}
  measure = len(planX) * lane.cellSize
  return problem | ({"length": round(measure, 1)} if length else {"area": round(measure * lane.cellSize, 1)})


def groupedProblems(kind, lane, selected, nodes, regionOfNode, regions):
  """One problem per group of the selected nodes' columns."""
  chosen = numpy.flatnonzero(selected)
  if not len(chosen):
    return []
  numbered = groups(lane, nodes.x[chosen].astype(numpy.int64), nodes.z[chosen].astype(numpy.int64))
  order = numpy.argsort(numbered, kind="stable")
  bounds = numpy.flatnonzero(numpy.diff(numbered[order])) + 1
  problems = []
  for part in (chosen[indices] for indices in numpy.split(order, bounds)):
    planX, planY = nodes.plan(part)
    problems.append(describeGroup(kind, lane, planX, planY, nodes.height[part], regionOfNode[part], regions))
  return problems


def eventProblems(kind, eventKind, lane, nodes, regionOfNode, regions):
  """One problem per run of the lane's events of a kind, grouped by the columns they fall into: a void edge, or a fall under the
  underworld, each in the region the falls start from."""
  events = lane.events[lane.events[:, 0] == eventKind]
  if not len(events):
    return []
  x, z = lane.columnPlaces(events[:, 2].astype(numpy.int64))
  planX = (lane.origin[2] + (z + 0.5) * lane.cellSize).astype(numpy.float32)
  planY = (lane.origin[0] + (x + 0.5) * lane.cellSize).astype(numpy.float32)
  height = lane.height(events[:, 3]).astype(numpy.float32)
  sources = regionOfNode[events[:, 1]]
  numbered = groups(lane, x, z)
  problems = []
  for group in numpy.unique(numbered):
    part = numbered == group
    problems.append(describeGroup(kind, lane, planX[part], planY[part], height[part], sources[part], regions, length=True))
  return problems


def bodyFootprint(lane, triangles):
  """Which columns' centers a body's triangles (zone axes) cover in plan, and the highest point of its surface."""
  image = Image.new("1", (lane.columnsX, lane.columnsZ), 0)
  draw = ImageDraw.Draw(image)
  for triangle in numpy.asarray(triangles, dtype=numpy.float64):
    draw.polygon([((point[1] - lane.origin[0]) / lane.cellSize - 0.5, (point[0] - lane.origin[2]) / lane.cellSize - 0.5) for point in triangle], fill=1)
  return numpy.asarray(image, dtype=bool), float(numpy.asarray(triangles)[..., 2].max())


def playFloorOf(nodes, underworld):
  """Which nodes are floor a player could be meant to stand on: walkable, dry, not taken by a zone line, not a boundary's top, not under
  the underworld, and joined to the lane by open space (reached, or open: the inside of a closed solid and a sealed room are not)."""
  floor = nodes.walkable & ~nodes.wet & ((nodes.shape & (absorbedShape | boundaryShape)) == 0) & ((nodes.state & openState) != 0)
  if underworld is not None:
    lowest = math.ceil((underworld - nodes.lane.origin[1]) / nodes.lane.cellHeight)
    if lowest > 0:
      floor &= nodes.low >= numpy.uint16(min(lowest, numpy.iinfo(numpy.uint16).max))
  return floor


def analyse(lane, description):
  """Everything mapReach reports of a lane: entries connected or not, reached area by label, per-region reach, zone lines, plot entrances,
  and swim boxes reached or not, and the numbered problems; with the node arrays the plan draws from and each node's region."""
  nodes = Nodes(lane)
  regions = description["regions"]
  regionOfNode = nodeRegions(nodes, regions)
  access = numpy.array([{"play": 0, "view": 1, "none": 2, None: 3}[region["access"]] for region in regions] + [4], dtype=numpy.int8)
  nodeAccess = access[regionOfNode]
  hasSafe = any(seed["kind"] == "safePoint" for seed in description["seeds"])
  entries = []
  for seed, node in zip(description["seeds"], lane.seedNodes):
    state = int(nodes.state[node])
    connected = True if seed["kind"] == "safePoint" else (bool(state & fromSafeState and state & toSafeState) if hasSafe else None)
    entries.append({"name": seed["name"], "kind": seed["kind"], "at": seed["footing"], "isolated": seed["isolated"], "connected": connected})
  plots = []
  for plot in description["plotEntrances"]:
    node = nodes.floorNodeAt(plot["point"])
    plots.append({"name": plot["name"], "at": plot["point"], "reached": node is not None and bool(nodes.reached[node])} | ({} if node is not None else {"why": "no floor under it"}))
  swimReached = numpy.zeros(len(description["swimBoxes"]), dtype=bool)
  wetReached = nodes.reached & nodes.wet
  swimReached[numpy.unique(nodes.box[wetReached])] = True
  swimBoxes = [{"name": box["name"], "reached": bool(swimReached[index])} for index, box in enumerate(description["swimBoxes"])]
  zoneLines = []
  for index, line in enumerate(description["zoneLines"]):
    reported = {"name": line["name"], "reached": bool(lane.lineReached[index])}
    if line["landing"] is not None:
      reported["teleport"] = {"landing": line["landing"], "followed": bool(lane.lineLanding[index] >= 0)} | ({} if lane.lineLanding[index] >= 0 else {"why": "no floor under its target"})
    zoneLines.append(reported)
  area = lane.cellSize * lane.cellSize
  reachedArea = {name: round(float((nodes.label == index).sum()) * area, 1) for index, name in enumerate(labelNames) if index != unreachedLabel}
  reachedArea["trap"] = round(float(nodes.trap.sum()) * area, 1)
  playFloor = playFloorOf(nodes, description["underworld"])
  perRegion = []
  for index, region in enumerate(regions):
    inside = playFloor & (regionOfNode == index)
    total, reached = int(inside.sum()), int((inside & nodes.reached).sum())
    perRegion.append({
      "name": region["name"], "access": region["access"], "reachedShare": None if total == 0 else round(reached / total, 4),
      "unreachedPlayFloor": round((total - reached) * area, 1) if region["access"] == "play" else None,
    })

  problems = {kind: [] for kind in problemKinds}
  problems["voidEdge"] = eventProblems("voidEdge", voidEvent, lane, nodes, regionOfNode, regions)
  problems["belowUnderworld"] = eventProblems("belowUnderworld", belowUnderworldEvent, lane, nodes, regionOfNode, regions)
  problems["noneRegion"] = groupedProblems("noneRegion", lane, nodes.reached & (nodeAccess == 2), nodes, regionOfNode, regions)
  if regions:
    problems["outsideRegions"] = groupedProblems("outsideRegions", lane, nodes.reached & (regionOfNode < 0), nodes, regionOfNode, regions)
  for entry in entries:
    if entry["connected"] is False and not entry["isolated"]:
      problems["disconnectedEntry"].append({"kind": "disconnectedEntry", "entry": entry["name"], "center": entry["at"], "region": None})
  for plot in plots:
    if not plot["reached"]:
      problems["unreachedPlot"].append({"kind": "unreachedPlot", "plot": plot["name"], "center": plot["at"], "region": None})
  problems["trap"] = groupedProblems("trap", lane, nodes.trap, nodes, regionOfNode, regions)
  problems["viewRegion"] = groupedProblems("viewRegion", lane, nodes.reached & (nodeAccess == 1), nodes, regionOfNode, regions)
  for line in zoneLines:
    if not line["reached"]:
      box = description["zoneLines"][[candidate["name"] for candidate in description["zoneLines"]].index(line["name"])]
      problems["unreachedZoneLine"].append({"kind": "unreachedZoneLine", "zoneLine": line["name"], "center": [round((a + b) / 2, 1) for a, b in zip(box["low"], box["high"])], "region": None})
  for box, reported in zip(description["swimBoxes"], swimBoxes):
    if not reported["reached"]:
      problems["unreachedSwim"].append({"kind": "unreachedSwim", "swimBox": box["name"], "center": [round((a + b) / 2, 1) for a, b in zip(box["low"], box["high"])], "region": None})
  problems["unreachedPlayFloor"] = groupedProblems("unreachedPlayFloor", lane, playFloor & ~nodes.reached & (nodeAccess == 0), nodes, regionOfNode, regions)
  for body in description["undecidedBodies"]:
    footprint, surface = bodyFootprint(lane, body["triangles"])
    walked = nodes.reached & ~nodes.wet & footprint[nodes.z, nodes.x] & (nodes.height < surface)
    for problem in groupedProblems("undecidedWater", lane, walked, nodes, regionOfNode, regions):
      problems["undecidedWater"].append(problem | {"body": body["name"]})
  for kind in ("disconnectedEntry", "unreachedPlot", "unreachedZoneLine", "unreachedSwim"):
    for problem in problems[kind]:
      problem["region"] = regionAt(regions, problem["center"])
  # Floor smaller than the gap the grid resolves is a sliver of rasterization (a thin wall's top, a face's rim), not a place.
  smallest = (2 * lane.cellSize) ** 2
  slivers = [problem for problem in problems["unreachedPlayFloor"] if problem["area"] < smallest]
  problems["unreachedPlayFloor"] = [problem for problem in problems["unreachedPlayFloor"] if problem["area"] >= smallest]
  listed, more = [], {}
  if slivers:
    more["unreachedPlayFloorSlivers"] = {"count": len(slivers), "area": round(sum(problem["area"] for problem in slivers), 1), "under": smallest}
  for kind in problemKinds:
    found = sorted(problems[kind], key=lambda problem: -problem.get("area", problem.get("length", 0)))
    listed += found[:problemsListed]
    if len(found) > problemsListed:
      rest = found[problemsListed:]
      more[kind] = {"count": len(rest), "area": round(sum(problem.get("area", 0) for problem in rest), 1), "length": round(sum(problem.get("length", 0) for problem in rest), 1)}
  for number, problem in enumerate(listed, 1):
    problem["number"] = number
  return {
    "nodes": nodes, "regionOfNode": regionOfNode,
    "entries": entries, "reachedArea": reachedArea, "regions": perRegion, "zoneLines": zoneLines, "plotEntrances": plots, "swimBoxes": swimBoxes,
    "problems": listed, "moreProblems": more,
  }


def regionAt(regions, point):
  for index in precedence(regions):
    region = regions[index]
    if region["bottom"] <= point[2] <= region["top"] and insideOutline(point, region["outline"]):
      return region["name"]
  return None


def insideOutline(point, outline):
  inside = False
  for (x1, y1), (x2, y2) in zip(outline, outline[1:] + outline[:1]):
    if (y1 > point[1]) != (y2 > point[1]) and point[0] < x1 + (point[1] - y1) * (x2 - x1) / (y2 - y1):
      inside = not inside
  return inside


def viewedProblems(problems, count):
  """Which problems the problem sheet views, up to count: the first of each kind in turn, then the second of each, so every kind found
  is seen; in their numbered order."""
  byKind = {}
  for problem in problems:
    byKind.setdefault(problem["kind"], []).append(problem)
  chosen, rank = [], 0
  while len(chosen) < count and any(rank < len(found) for found in byKind.values()):
    chosen += [found[rank] for found in byKind.values() if rank < len(found)][:count - len(chosen)]
    rank += 1
  return sorted(chosen, key=lambda problem: problem["number"])


def walkPlaces(nodes):
  """The plan places (zone x, y) of every reached walkable floor node, which problem views turn from."""
  standing = numpy.flatnonzero(nodes.label == walkLabel)
  return numpy.stack(nodes.plan(standing), axis=1)


def problemRadius(problem, description):
  """How far a problem reaches from its center: half its extent's diagonal in plan or half its height, its box's for a zone line or swim
  box, and never under playerHeight."""
  if "extent" in problem:
    low, high = numpy.asarray(problem["extent"]["minimum"]), numpy.asarray(problem["extent"]["maximum"])
  elif "zoneLine" in problem or "swimBox" in problem:
    boxes = description["zoneLines"] if "zoneLine" in problem else description["swimBoxes"]
    box = next(box for box in boxes if box["name"] == problem.get("zoneLine", problem.get("swimBox")))
    low, high = numpy.asarray(box["low"]), numpy.asarray(box["high"])
  else:
    return playerScale.playerHeight
  return max(float(numpy.hypot(*(high[:2] - low[:2]))) / 2, float(high[2] - low[2]) / 2, playerScale.playerHeight)


def problemSights(standing, problem, description, farthest):
  """The eyes a problem may be viewed from, in the order they are tried: problemViewFrame times its radius back from it (at least
  problemViewDistance, at most farthest, where the view still draws it), raised by each of problemViewElevations in turn and turned to
  each of problemViewBearings from the reached floor nearest it beyond its radius; each {eye, target}, the target problemViewLift over
  its center. Returns (candidates, radius, distance)."""
  radius = problemRadius(problem, description)
  distance = min(max(problemViewFrame * radius, problemViewDistance), farthest)
  center = numpy.asarray(problem["center"], dtype=numpy.float64)
  away = numpy.hypot(standing[:, 0] - center[0], standing[:, 1] - center[1]) if len(standing) else numpy.zeros(0)
  beyond = away >= radius
  start = 0.0
  if beyond.any():
    nearest = standing[int(numpy.argmin(numpy.where(beyond, away, numpy.inf)))]
    start = math.atan2(nearest[1] - center[1], nearest[0] - center[0])
  target = center + [0.0, 0.0, problemViewLift]
  candidates = []
  for elevation in problemViewElevations:
    rise, across = math.sin(math.radians(elevation)) * distance, math.cos(math.radians(elevation)) * distance
    for turn in problemViewBearings:
      bearing = start + math.radians(turn)
      eye = target + [math.cos(bearing) * across, math.sin(bearing) * across, rise]
      candidates.append({"eye": [round(float(value), 2) for value in eye], "target": [round(float(value), 2) for value in target]})
  return candidates, radius, distance


def laneTerms(lane, description):
  """What every reach result states: each constant the lane used (value and source), what it does not model, and its limits."""
  stated = limits(lane, description["underworld"], description["boundariesKnown"])
  if not description["regions"]:
    stated.append("the zone has no regions: reach outside every region (outsideRegions) is not judged")
  if description.get("ignoredRegions"):
    stated.append(f".wtr records the lane does not model (only water, lava, and zone lines move players): {description['ignoredRegions']}")
  return {"constants": constantsReport(), "notModeled": notModeled, "limits": stated}


def report(lane, description, analysis, status, seconds):
  """mapReach's JSON: what the lane used and cannot see, and what it found."""
  return laneTerms(lane, description) | {
    "reachGrid": reachGrid, "lane": status, "seconds": seconds, "laneFile": str(lane.path), "nodes": lane.nodeCount,
    "entries": analysis["entries"], "notFollowed": description["notFollowed"], "reachedArea": analysis["reachedArea"], "regions": analysis["regions"],
    "zoneLines": analysis["zoneLines"], "plotEntrances": analysis["plotEntrances"], "swimBoxes": analysis["swimBoxes"],
    "problems": analysis["problems"], "moreProblems": analysis["moreProblems"],
  }
