import json
import math
import sys
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqgFiles
import playerReach
import serverMapFiles
from playerScale import steepestWalkableDegrees, stepHeight
from testHousing import decision
from testWalkRoute import rampArguments
from testModelsAndDressing import freshScene
from testWater import environment, liquidMaterials
from conftest import writePNG

cellSize, cellHeight = playerReach.reachGrid["cellSize"], playerReach.reachGrid["cellHeight"]
# Sizes sit this far either side of what the lane must tell apart, so they hold after a re-measurement moves the constants.
heightMargin = 2 * cellHeight
gapMargin = 2 * cellSize
shortName = "reachcourse"
wallHeight = 12.0
# The edge walls stand just inside the ground's edge at 240, on ground, in its last column.
edge = 239.9
otherZone = {"zone": "qeynos2", "x": 0, "y": 0, "z": 0, "headingDegrees": 0}
gentleDegrees = steepestWalkableDegrees - 5
steepDegrees = steepestWalkableDegrees + 5
# The lane's locations hold to one cell; a problem's center is the member nearest its middle.
withinCell = cellSize * math.sqrt(2)


async def cube(session, name, minimum, maximum):
  await session.expectSuccess("createPrimitive", {
    "kind": "cube", "name": name, "size": [high - low for low, high in zip(minimum, maximum)],
    "location": [(minimum[0] + maximum[0]) / 2, (minimum[1] + maximum[1]) / 2, minimum[2]],
  })


async def rampAlongY(session, name, x, footY, rise, degrees, width=10.0):
  """A plane rising along +Y from the ground at footY to rise, `degrees` from level: its run is rise / tan(degrees)."""
  run = rise / math.tan(math.radians(degrees))
  await session.expectSuccess("createPrimitive", {
    "kind": "plane", "name": name, "size": [width, rise / math.sin(math.radians(degrees)), 0], "location": [x, footY + run / 2, rise / 2],
    "rotationDegrees": [degrees, 0, 0],
  })
  return footY + run


async def wall(session, name, path):
  await session.expectSuccess("placeBoundaryWall", {"name": name, "path": path, "height": wallHeight})


async def region(session, name, outline, access, bottom=-30, top=60):
  await session.expectSuccess("createRegion", {"name": name, "outline": outline, "bottom": bottom, "top": top, "intent": f"the course's {name}", "access": access})


def rectangle(low, high):
  return [[low[0], low[1]], [high[0], low[1]], [high[0], high[1]], [low[0], high[1]]]


async def reachCourse(session, folder):
  """The reach course: flat ground 480 across at 0, its edge walled but for an open stretch, a steep face sliding off it, and a zone line
  catching falls, with a station for each rule the lane models, sized from playerScale and reachGrid. Saved as reachCourse.blend."""
  await freshScene(session)
  await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [480, 480], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
  await session.expectSuccess("createMaterial", {"name": "grass", "diffuseTexture": str(writePNG(folder / "grass.png", 4, 4, (90, 120, 60, 255)))})
  await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
  await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 64, "direction": [0, 0, 1]})
  await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, 0, 0, 0], "shortName": shortName})
  # Steps half a margin either side of the step players climb.
  await cube(session, "stepUnder", [35, 35, 0], [45, 45, stepHeight - heightMargin])
  await cube(session, "stepOver", [35, 65, 0], [45, 75, stepHeight + heightMargin])
  # Ramps five degrees either side of the steepest face players walk, each up to a platform.
  top = await rampAlongY(session, "gentleRamp", 80, 35, 10, gentleDegrees)
  await cube(session, "gentlePlatform", [75, top, 0], [85, top + 10, 10])
  top = await rampAlongY(session, "steepRamp", 110, 35, 10, steepDegrees)
  await cube(session, "steepPlatform", [105, top, 0], [115, top + 10, 10])
  # A ledge reached by a ramp, dropping into a walled pit on its north and onto open ground on its east.
  await rampAlongY(session, "ledgeRamp", 160, 30 - 10 / math.tan(math.radians(30)), 10, 30)
  await cube(session, "ledge", [150, 30, 0], [170, 50, 10])
  await cube(session, "pitWest", [153, 50, 0], [155, 62, 10])
  await cube(session, "pitEast", [165, 50, 0], [167, 62, 10])
  await cube(session, "pitNorth", [153, 60, 0], [167, 62, 10])
  # A platform whose north side is a face steeper than players walk, down to the ground.
  await rampAlongY(session, "slopeRamp", -60, 30 - 10 / math.tan(math.radians(30)), 10, 30)
  await cube(session, "slopePlatform", [-70, 30, 0], [-50, 50, 10])
  run = 10 / math.tan(math.radians(80))
  await session.expectSuccess("createPrimitive", {
    "kind": "plane", "name": "slideFace", "size": [10, 10 / math.sin(math.radians(80)), 0], "location": [-60, 50 + run / 2, 5], "rotationDegrees": [-80, 0, 0],
  })
  # A pool's swim box on the ground, banked by blocks a margin under and over its top plus the step.
  await session.expectSuccess("placeSwimVolume", {"name": "pool", "liquid": "water", "minimum": [-130, 30, -1], "maximum": [-110, 50, 4]})
  await cube(session, "bankLow", [-110, 30, 0], [-100, 38, 4 + stepHeight - heightMargin])
  await cube(session, "bankHigh", [-110, 42, 0], [-100, 50, 4 + stepHeight + heightMargin])
  # A swim box floating higher over the ground than players reach into, beside a platform they walk off.
  await rampAlongY(session, "floatRamp", -180, 21, 14, 45)
  await cube(session, "floatPlatform", [-185, 35, 0], [-175, 45, 14])
  await session.expectSuccess("placeSwimVolume", {"name": "floating", "liquid": "water", "minimum": [-175, 35, 8], "maximum": [-165, 45, 12]})
  # A lake without swim boxes.
  await liquidMaterials(session, folder)
  await session.expectSuccess("sculptAlongPath", {"objectName": "ground", "mode": "carve", "path": [[-180, -100, -10]], "radius": 30, "strength": 1, "profile": [[0, 0], [1, 10]], "conformRim": False})
  await session.expectSuccess("floodWater", {"name": "lake", "seed": [-180, -100], "level": -2, "material": "water"})
  # A walled yard whose only way in is a gap of twice the cell size and a margin, off the cell lines.
  await wall(session, "yardWall", [[38.3, -70], [30, -70], [30, -50], [50, -50], [50, -70], [38.3 + 2 * cellSize + gapMargin, -70]])
  # A wall across a corner at 45 degrees, with ground on both sides.
  await wall(session, "diagonal", [[edge, -200], [200, -edge]])
  # A room under a wall raised less than a player's height (only a smaller player passes), one under a wall raised less than a cell,
  # and one walled all round.
  await cube(session, "squeezeLintel", [-60, 158, 3], [-40, 160, wallHeight])
  await wall(session, "squeezeRoomWall", [[-60, 158], [-60, 180], [-40, 180], [-40, 158]])
  await cube(session, "sealedLintel", [-20, 158, 0.1], [0, 160, wallHeight])
  await wall(session, "sealedRoomWall", [[-20, 158], [-20, 180], [0, 180], [0, 158]])
  await wall(session, "closedRoomWall", [[20, 160], [20, 180], [40, 180], [40, 160], [20, 160]])
  # The ground's edge, walled but for an open stretch, the foot of a face sliding off the edge, and a stretch over a zone line.
  await wall(session, "edgeLowA", [[-edge, -edge], [-edge, -40]])
  await wall(session, "edgeLowB", [[-edge, -10], [-edge, 60]])
  await wall(session, "edgeLowC", [[-edge, 72], [-edge, edge]])
  await wall(session, "edgeYHigh", [[-edge, edge], [edge, edge]])
  await wall(session, "edgeHighA", [[edge, edge], [edge, -80]])
  await wall(session, "edgeHighB", [[edge, -100], [edge, -200]])
  await wall(session, "edgeYLow", [[200, -edge], [-edge, -edge]])
  run = 20 / math.tan(math.radians(80))
  await session.expectSuccess("createPrimitive", {
    "kind": "plane", "name": "slideOff", "size": [20 / math.sin(math.radians(80)), 12, 0], "location": [-240 - run / 2, 66, -10], "rotationDegrees": [0, -80, 0],
  })
  await session.expectSuccess("placeZoneLine", {"number": 5, "label": "catch", "minimum": [240, -105, -60], "maximum": [250, -75, -20], "target": otherZone})
  # A corridor closed by a zone line, a zone line leading back into the zone onto a tower, and a deck seen but not entered.
  await wall(session, "corridorWall", [[100, 100], [140, 100], [140, 90], [170, 90], [170, 116], [140, 116], [140, 106], [100, 106]])
  await session.expectSuccess("placeZoneLine", {"number": 3, "label": "corridor", "minimum": [118, 99, -5], "maximum": [122, 107, 20], "target": otherZone})
  await cube(session, "tower", [85, -125, 0], [95, -115, 30])
  await session.expectSuccess("placeZoneLine", {"number": 4, "label": "home", "minimum": [60, -123, -5], "maximum": [66, -117, 20], "target": {"zone": shortName, "x": 90, "y": -120, "z": 30, "headingDegrees": 0}})
  # Two plots, one entered from the open ground and one inside the walled room; an entry on a block of its own, and one stranded.
  await session.expectSuccess("setZoneHousing", decision)
  await session.expectSuccess("placePlot", {"address": "1 Open Street", "center": [-40, -120], "facingDegrees": 0, "size": [20, 20]})
  await session.expectSuccess("placePlot", {"address": "2 Closed Street", "center": [30, 164], "facingDegrees": 0, "size": [8, 6]})
  await cube(session, "hermitBlock", [-185, 115, 0], [-175, 125, 20])
  await cube(session, "strandedBlock", [-125, 115, 0], [-115, 125, 20])
  await session.expectImage("placeEntry", {"name": "hermit", "at": [-180, 120], "headingDegrees": 0, "kind": "landing", "isolated": True})
  await session.expectImage("placeEntry", {"name": "stranded", "at": [-120, 120], "headingDegrees": 0, "kind": "landing"})
  # The course is played, but for its rooms, the corridor past the zone line, the deck, and a strip along its far side.
  await region(session, "course", [[-250, -240], [200, -240], [240, -200], [240, 200], [-250, 200]], "play")
  await region(session, "viewDeck", rectangle([100, -40], [140, -20]), "view")
  await region(session, "squeezeRoom", rectangle([-60, 158], [-40, 180]), "none")
  await region(session, "sealedRoom", rectangle([-20, 158], [0, 180]), "none")
  await region(session, "closedRoom", rectangle([20, 160], [40, 180]), "none")
  await region(session, "beyondLine", [[122, 100], [140, 100], [140, 90], [170, 90], [170, 116], [140, 116], [140, 106], [122, 106]], "none")
  await session.expectSuccess("saveFile", {"path": str(folder / "reachCourse.blend")})


# Where the course's rules show, and what the lane must call each place: its height (the floor, or for "wet" the middle of the water).
probes = {
  "safe point": ([0, 0, 0], "walk"),
  "step under": ([40, 40, stepHeight - heightMargin], "walk"),
  "step over": ([40, 70, stepHeight + heightMargin], "unreached"),
  "gentle platform": ([80, 45, 10], "walk"),
  "steep platform": ([110, 43, 10], "unreached"),
  "ledge": ([160, 40, 10], "walk"),
  "pit": ([160, 55, 0], "walk"),
  "below the ledge": ([175, 40, 0], "walk"),
  "slide face": ([-60, 50.5, 10], "slide"),
  "slide foot": ([-60, 56, 0], "walk"),
  "pool": ([-120, 40, 0], "swim"),
  "low bank": ([-105, 34, 4 + stepHeight - heightMargin], "walk"),
  "high bank": ([-105, 46, 4 + stepHeight + heightMargin], "unreached"),
  "float platform": ([-180, 40, 14], "walk"),
  "lake bed": ([-180, -100, -10], "walk"),
  "yard": ([40, -60, 0], "walk"),
  "near the diagonal": ([205, -205, 0], "walk"),
  "past the diagonal": ([234, -234, 0], "unreached"),
  "under the lintel": ([-50, 159, 0], "squeeze"),
  "squeeze room": ([-50, 170, 0], "squeeze"),
  "sealed room": ([-10, 170, 0], "unreached"),
  "closed room": ([30, 170, 0], "unreached"),
  "corridor": ([110, 103, 0], "walk"),
  "in the zone line": ([118.5, 103, 0], "zoneLine"),
  "past the zone line": ([155, 103, 0], "unreached"),
  "tower": ([90, -120, 30], "walk"),
  "slide off": ([-241.5, 66, -3], "slide"),
  "hermit": ([-180, 120, 20], "walk"),
  "stranded": ([-120, 120, 20], "walk"),
  "far strip": ([0, 220, 0], "walk"),
  "deck": ([120, -30, 0], "walk"),
}
wetProbes = {"floating box": ([-170, 40, 10], "swim")}


def nodeAt(nodes, point, wet=False):
  """The lane's node at a probe: the floor found as an entry's footing is, or the wet node holding its height."""
  if not wet:
    return nodes.floorNodeAt(point)
  column = nodes.lane.columnAt(point)
  candidates = numpy.arange(nodes.columnStart[column], nodes.columnStart[column + 1])
  low, high = nodes.lane.height(nodes.low[candidates]), nodes.lane.height(nodes.high[candidates])
  holding = candidates[nodes.wet[candidates] & (low <= point[2]) & (high >= point[2])]
  return int(holding[0]) if len(holding) else None


def labelsAt(nodes):
  found = {}
  for name, (point, _) in probes.items():
    node = nodeAt(nodes, point)
    found[name] = None if node is None else playerReach.labelNames[nodes.label[node]]
  for name, (point, _) in wetProbes.items():
    node = nodeAt(nodes, point, wet=True)
    found[name] = None if node is None else playerReach.labelNames[nodes.label[node]]
  return found


def near(point, expected, tolerance=withinCell):
  return math.dist(point[:2], expected[:2]) <= tolerance


def testLaneLabelsTheCourse(stageBlenderServer, tmp_path):
  async def steps(session):
    await reachCourse(session, tmp_path)
    result, _ = await session.call("mapReach", {"problemViews": False})
    return result

  result = stageBlenderServer.session(steps)
  assert result.is_error is False, result.content[0].text
  assert [content.type for content in result.content] == ["image", "text"]
  reached = json.loads(result.content[1].text)
  lane = playerReach.Lane(reached["laneFile"])
  nodes = playerReach.Nodes(lane)
  assert labelsAt(nodes) == {name: label for name, (_, label) in (probes | wetProbes).items()}
  # The pit is walked into off the ledge and never left; the ground the ledge drops onto to its east is walked back from.
  pit, below = nodes.floorNodeAt(probes["pit"][0]), nodes.floorNodeAt(probes["below the ledge"][0])
  assert (bool(nodes.trap[pit]), bool(nodes.trap[below])) == (True, False)
  moves = lane.read("move")
  pitFloor = numpy.flatnonzero((nodes.x >= nodes.x[pit] - 4) & (nodes.x <= nodes.x[pit] + 4) & (nodes.z >= nodes.z[pit] - 4) & (nodes.z <= nodes.z[pit] + 4) & (nodes.height < 1))
  assert {playerReach.moveNames[move] for move in moves[pitFloor]} == {"drop", "walk"}
  # The floating box is entered only by dropping into it off the platform; the tower only by the zone line leading home.
  floating = nodeAt(nodes, wetProbes["floating box"][0], wet=True)
  tower = nodes.floorNodeAt(probes["tower"][0])
  moves = lane.read("move")
  assert playerReach.moveNames[moves[floating]] in ("drop", "swim")
  assert playerReach.moveNames[moves[tower]] == "teleport"
  # The hermit's block is its own lane, started from its entry; the stranded entry is a seed the safe point never reaches.
  origins = lane.read("originSeed")
  seedNames = [entry["name"] for entry in reached["entries"]]
  assert seedNames == ["safe point", "hermit", "stranded"]
  assert origins[nodes.floorNodeAt(probes["hermit"][0])] == seedNames.index("hermit")
  assert [(entry["name"], entry["connected"], entry["isolated"]) for entry in reached["entries"]] == [
    ("safe point", True, False), ("hermit", False, True), ("stranded", False, False),
  ]
  assert reached["zoneLines"] == [
    {"name": "ATP_3_corridor", "reached": True},
    {"name": "ATP_4_home", "reached": True, "teleport": {"landing": [90.0, -120.0, 30.0], "followed": True}},
    {"name": "ATP_5_catch", "reached": True},
  ]
  assert reached["swimBoxes"] == [{"name": "AWT_floating", "reached": True}, {"name": "AWT_pool", "reached": True}]
  assert [(plot["name"], plot["reached"]) for plot in reached["plotEntrances"]] == [("1 Open Street", True), ("2 Closed Street", False)]
  assert reached["reachGrid"] == playerReach.reachGrid
  assert set(reached["constants"]) == {"playerHeight", "walkableNormalZ", "steepestWalkableDegrees", "stepHeight", "smallestPlayerHeight"}
  assert reached["constants"]["stepHeight"]["value"] == stepHeight
  assert any(entry.startswith("doors: taken as open") for entry in reached["notModeled"])
  assert any("2 x cellSize" in limit for limit in reached["limits"])
  problems = [(problem["kind"], problem.get("region")) for problem in reached["problems"]]
  assert problems == [
    ("voidEdge", "course"), ("voidEdge", "course"),
    ("noneRegion", "squeezeRoom"),
    ("outsideRegions", None),
    ("disconnectedEntry", "course"),
    ("unreachedPlot", "closedRoom"),
    ("trap", "course"), ("trap", "course"),
    ("viewRegion", "viewDeck"),
    ("unreachedPlayFloor", "course"), ("unreachedPlayFloor", "course"), ("unreachedPlayFloor", "course"),
    ("undecidedWater", "course"),
  ]
  byKind = {}
  for problem in reached["problems"]:
    byKind.setdefault(problem["kind"], []).append(problem)
  # The open stretch of edge, thirty units, falls onto nothing beside it; the face sliding off the edge ends over nothing.
  openEdge, slideOff = sorted(byKind["voidEdge"], key=lambda problem: problem["center"][1])
  assert near(openEdge["center"], [-240.5, -25]) and openEdge["length"] == 30.0
  assert openEdge["extent"]["minimum"][1] == -40.0 and openEdge["extent"]["maximum"][1] == -10.0
  assert slideOff["extent"]["minimum"][0] < -241 and 60 <= slideOff["center"][1] <= 72
  assert near(byKind["noneRegion"][0]["center"], [-50, 169.5], 2 * withinCell)
  assert 200 <= byKind["outsideRegions"][0]["center"][1] <= 240
  assert byKind["disconnectedEntry"][0]["entry"] == "stranded" and byKind["unreachedPlot"][0]["plot"] == "2 Closed Street"
  pitTrap, slideTrap = sorted(byKind["trap"], key=lambda problem: problem["center"][0])[::-1]
  # The pit's floor is 10 x 10; the faces of the ledge and the west wall stand on its column lines and fill the row and column beside
  # them, leaving 9 x 9.
  assert near(pitTrap["center"], [160, 55], 2 * withinCell) and pitTrap["area"] == 81.0
  assert slideTrap["center"][0] < -240
  assert near(byKind["viewRegion"][0]["center"], [120, -30], 2 * withinCell) and byKind["viewRegion"][0]["area"] == 800.0
  # Each block top players never reach, with the rim its faces leave a column wide on two sides.
  unreachedFloors = sorted((problem["center"][:2], problem["area"]) for problem in byKind["unreachedPlayFloor"])
  for (found, area), (expected, top) in zip(unreachedFloors, sorted([((-105, 46), 80), ((40, 70), 100), ((110, 43), 100)])):
    assert near(found, expected, 2 * withinCell) and top <= area <= top + 2 * math.sqrt(top) + 1
  assert reached["moreProblems"] == {"unreachedPlayFloorSlivers": {"count": 2, "area": 4.0, "under": (2 * cellSize) ** 2}}
  assert byKind["undecidedWater"][0]["body"] == "lake" and near(byKind["undecidedWater"][0]["center"], [-180, -100], 25)
  # Everything reached is labelled; the area of each label sums to the reached nodes.
  area = cellSize * cellSize
  assert sum(value for name, value in reached["reachedArea"].items() if name != "trap") == float(nodes.reached.sum()) * area


def testMapReachRefusesWhatNoLaneStartsFrom(stageBlenderServer, tmp_path):
  async def steps(session):
    await freshScene(session)
    refusals = {"noTriangles": await session.expectError("mapReach", {})}
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [96, 96], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
    await session.expectSuccess("setZoneProperties", environment)
    refusals["noSeeds"] = await session.expectError("mapReach", {})
    await session.expectImage("placeEntry", {"name": "gate", "at": [10, 10], "headingDegrees": 0, "kind": "landing"})
    await session.expectSuccess("transformObjects", {"names": ["gate"], "translate": [0, 0, 2]})
    refusals["offFooting"] = await session.expectError("mapReach", {})
    await session.expectImage("placeEntry", {"name": "gate", "at": [10, 10], "headingDegrees": 0, "kind": "landing"})
    refusals["level"] = await session.expectError("mapReach", {"level": {"bottom": 5, "top": 1}})
    await session.expectSuccess("createTerrainGrid", {"name": "plain", "size": [30000, 30000], "spacing": 3000, "location": [0, 0, -50], "collection": "terrain"})
    refusals["tooWide"] = await session.expectError("mapReach", {})
    return refusals

  refusals = stageBlenderServer.session(steps)
  assert "The zone ships no collision triangles" in refusals["noTriangles"]
  assert "The lane has no seeds: set the safe point (setZoneProperties safePoint) or placeEntry" in refusals["noSeeds"]
  assert "Entry 'gate' stands off its footing (2.0 from [10.0, 10.0, 0.0])" in refusals["offFooting"]
  assert "level's top must lie above its bottom" in refusals["level"]
  assert "columns, more than the helper's 600000000" in refusals["tooWide"]


def testLaneAgreesWithWalkRoute(stageBlenderServer):
  async def steps(session):
    await freshScene(session)
    await session.expectSuccess("createTerrainGrid", {"name": "course", "size": [200, 120], "spacing": 4, "location": [0, 0, 0], "collection": "terrain"})
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [-90, 0, 0, 90]})
    await cube(session, "lowRiser", [-70, -10, 0], [-50, 10, stepHeight - heightMargin])
    await cube(session, "highRiser", [-70, 30, 0], [-50, 50, stepHeight + heightMargin])
    await session.expectSuccess("createPrimitive", rampArguments("climbable", steepestWalkableDegrees - 2, 0))
    await session.expectSuccess("createPrimitive", rampArguments("tooSteep", steepestWalkableDegrees + 5, 40))
    walks = {}
    for name, y in (("lowRiser", 0), ("highRiser", 40)):
      walks[name] = await session.expectSuccess("walkRoute", {"path": [[-90, y, 0], [-30, y, 0]]})
    for name, y in (("climbable", 0), ("tooSteep", 40)):
      walks[name] = await session.expectSuccess("walkRoute", {"path": [[0, y, 0], [21, y, 6]]})
    _, reached = await session.expectImage("mapReach", {"problemViews": False})
    return walks, reached

  walks, reached = stageBlenderServer.session(steps)
  nodes = playerReach.Nodes(playerReach.Lane(reached["laneFile"]))

  def label(point):
    return playerReach.labelNames[nodes.label[nodes.floorNodeAt(point)]]

  # What walkRoute walks, the lane reaches; where walkRoute stops at a rise or a steep face, the lane does not go past.
  assert walks["lowRiser"]["walkable"] and label([-60, 0, stepHeight - heightMargin]) == "walk"
  assert [problem["kind"] for problem in walks["highRiser"]["problems"]] == ["rise"] and label([-60, 40, stepHeight + heightMargin]) == "unreached"
  rise = 8 * math.sin(math.radians(steepestWalkableDegrees - 2))
  assert walks["climbable"]["walkable"] and label([20 + 8 * math.cos(math.radians(steepestWalkableDegrees - 2)) - cellSize / 2, 0, rise]) == "walk"
  rise = 8 * math.sin(math.radians(steepestWalkableDegrees + 5))
  assert [problem["kind"] for problem in walks["tooSteep"]["problems"]] == ["steep"]
  assert label([20 + 8 * math.cos(math.radians(steepestWalkableDegrees + 5)) - cellSize / 2, 40, rise]) == "unreached"


def testSceneAndServerFilesGiveTheSameLane(stageBlenderServer, tmp_path):
  archivePath = tmp_path / "samelane.eqg"

  async def steps(session):
    await freshScene(session)
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [96, 96], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
    await session.expectSuccess("createMaterial", {"name": "grass", "diffuseTexture": str(writePNG(tmp_path / "grass.png", 4, 4, (90, 120, 60, 255)))})
    await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
    await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 64, "direction": [0, 0, 1]})
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "raise", "center": [20, 20, 0], "radius": 12, "strength": 30, "falloff": "sharp"})
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "lower", "center": [-24, -24, 0], "radius": 12, "strength": 8, "falloff": "smooth"})
    await session.expectSuccess("placeSwimVolume", {"name": "pond", "liquid": "water", "minimum": [-32, -32, -8], "maximum": [-16, -16, -2]})
    await session.expectSuccess("placeZoneLine", {"number": 1, "label": "gate", "minimum": [40, -8, -4], "maximum": [47, 8, 20], "target": otherZone})
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, 0, 0, 0]})
    await session.expectSuccess("saveFile", {"path": str(tmp_path / "samelane.blend")})
    await session.expectSuccess("exportZone", {"path": str(archivePath), "purpose": "test"})
    _, reached = await session.expectImage("mapReach", {"problemViews": False})
    return reached

  reached = stageBlenderServer.session(steps)
  zoneFiles = serverMapFiles.zoneFilesOf(archivePath.read_bytes())
  regions = eqgFiles.parseZone(zoneFiles["zon"], "samelane.zon")["regions"]
  lane, _, status, _ = playerReach.laneFromServerFiles(
    serverMapFiles.mapBytes(zoneFiles), serverMapFiles.waterBytes(regions), [{"name": "safe point", "point": [0, 0, 0], "kind": "safePoint"}],
    stageBlenderServer.toolingRoot, lambda done, of, message: None,
  )
  scene = playerReach.Lane(reached["laneFile"])
  assert status == "computed" and reached["lane"] == "computed"

  def reachedNodes(found):
    nodes = playerReach.Nodes(found)
    chosen = numpy.flatnonzero(nodes.reached)
    planX, planY = nodes.plan(chosen)
    return numpy.stack([planX, planY, nodes.height[chosen], nodes.wet[chosen], nodes.label[chosen]], axis=1)

  # The archive's collision, rebuilt as the server rebuilds it, and its .wtr boxes give the scene's lane node for node.
  fromScene, fromFiles = reachedNodes(scene), reachedNodes(lane)
  assert len(fromScene) > 0 and numpy.array_equal(fromScene, fromFiles)
  assert set(numpy.unique(fromScene[:, 4]).astype(int)) == {playerReach.walkLabel, playerReach.swimLabel, playerReach.zoneLineLabel}


def testLaneKeySurvivesSaveReopenAndRestart(freshBlenderServer, tmp_path):
  workFile = tmp_path / "keptlane.blend"

  async def build(session):
    await freshScene(session)
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [64, 64], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, 0, 0, 0]})
    await session.expectSuccess("saveFile", {"path": str(workFile)})
    _, first = await session.expectImage("mapReach", {"problemViews": False})
    await session.expectSuccess("openFile", {"path": str(workFile)})
    _, reopened = await session.expectImage("mapReach", {"problemViews": False})
    return first, reopened

  async def again(session):
    await session.expectSuccess("openFile", {"path": str(workFile)})
    _, restarted = await session.expectImage("mapReach", {"problemViews": False})
    return restarted

  first, reopened = freshBlenderServer.session(build)
  restarted = freshBlenderServer.session(again)
  assert (first["lane"], reopened["lane"], restarted["lane"]) == ("computed", "reused", "reused")
  assert first["laneFile"] == reopened["laneFile"] == restarted["laneFile"]
  assert Path(first["laneFile"]).parent.name.startswith("keptlane-")
