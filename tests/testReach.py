import json
import math
import sys
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqgFiles
import playerReach
import serverMapFiles
import structurePlots
import playerScale

# The medium body group, which holds the walked human male, is the one these tests size their courses by.
medium = playerScale.groups["medium"]
minimumSwimDepth, playerHeight, steepestWalkableDegrees, stepHeight = medium.minimumSwimDepth, medium.playerHeight, medium.steepestWalkableDegrees, medium.stepHeight
from testHousing import decision
from testWalkRoute import rampArguments
from testModelsAndDressing import freshScene
from testStructures import bridge, kitAndPlot
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
containmentHeadroom = playerReach.movement()["containmentHeadroom"]
# One lintel leaves a gap between the containment headroom and playerHeight, which only a smaller player passes; the other leaves less
# than the containment headroom.
squeezeLintel = (containmentHeadroom + playerHeight) / 2
sealedLintel = containmentHeadroom / 2
# A swim box whose bottom lies higher over the ground than players reach into from it, half a player deep, under a platform they drop
# into it from.
floatBottom = playerHeight + 2 * heightMargin
floatTop = floatBottom + playerHeight / 2
floatPlatform = floatTop + 2 * heightMargin
# Swim boxes on the ground stand a margin deeper than players swim in; in one, a shelf leaves the water over it a margin shallower.
poolTop = minimumSwimDepth + heightMargin
fordShelf = 2 * heightMargin
# A box on the ground a margin shallower than players swim in, which the design player wades and a smaller one may float in; and two boxes
# stacked on the ground, the lower one shallow alone and the water they make together deep.
shallowTop = minimumSwimDepth - heightMargin
stackJoin = minimumSwimDepth / 2
# The diagonal wall runs along x - y = diagonalLine through grid corners between its ends, so it rasterizes as cells meeting only at their
# corners, which a move between diagonal neighbours slips between.
diagonalLine = 440.0
diagonalEnds = (math.floor(edge / cellSize) * cellSize, math.ceil((diagonalLine - edge) / cellSize) * cellSize)
# The lane's locations hold to one cell; a problem's center is the member nearest its middle.
withinCell = cellSize * math.sqrt(2)


def columnsOver(low, high):
  """How many of the lane's columns, centered on multiples of cellSize, hold some of a span from low to high."""
  return math.floor(high / cellSize + 0.5) - math.ceil(low / cellSize - 0.5) + 1


def columnsWithin(low, high):
  """How many of the lane's columns lie wholly between low and high."""
  return math.floor(high / cellSize - 0.5) - math.ceil(low / cellSize + 0.5) + 1


def centersWithin(low, high):
  """How many of the lane's columns have their centers from low to high, ends included, as a region holds them."""
  return math.floor(high / cellSize) - math.ceil(low / cellSize) + 1


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
  run = 10 / math.tan(math.radians(steepDegrees))
  await session.expectSuccess("createPrimitive", {
    "kind": "plane", "name": "slideFace", "size": [10, 10 / math.sin(math.radians(steepDegrees)), 0], "location": [-60, 50 + run / 2, 5],
    "rotationDegrees": [-steepDegrees, 0, 0],
  })
  # A pool's swim box on the ground, banked by blocks a margin under and over its top plus the step.
  await session.expectSuccess("placeSwimVolume", {"name": "pool", "liquid": "water", "minimum": [-130, 30, -1], "maximum": [-110, 50, poolTop]})
  await cube(session, "bankLow", [-110, 30, 0], [-100, 38, poolTop + stepHeight - heightMargin])
  await cube(session, "bankHigh", [-110, 42, 0], [-100, 50, poolTop + stepHeight + heightMargin])
  # One swim box over a ford, a shelf players wade, and the pool beside it.
  await session.expectSuccess("placeSwimVolume", {"name": "ford", "liquid": "water", "minimum": [-130, -50, -1], "maximum": [-90, -30, poolTop]})
  await cube(session, "fordShelf", [-130, -50, 0], [-110, -30, fordShelf])
  # A shallow box banked by a block a margin under its top plus the step, which only a swimmer climbs onto; and two stacked boxes.
  await session.expectSuccess("placeSwimVolume", {"name": "shallow", "liquid": "water", "minimum": [-130, 5, -1], "maximum": [-110, 15, shallowTop]})
  await cube(session, "shallowBank", [-110, 5, 0], [-100, 15, shallowTop + stepHeight - heightMargin])
  await session.expectSuccess("placeSwimVolume", {"name": "stackLow", "liquid": "water", "minimum": [-160, 5, -1], "maximum": [-140, 15, stackJoin]})
  await session.expectSuccess("placeSwimVolume", {"name": "stackHigh", "liquid": "water", "minimum": [-160, 5, stackJoin], "maximum": [-140, 15, poolTop]})
  # A swim box floating higher over the ground than players reach into, beside a platform they walk off.
  await rampAlongY(session, "floatRamp", -180, 35 - floatPlatform, floatPlatform, 45)
  await cube(session, "floatPlatform", [-185, 35, 0], [-175, 45, floatPlatform])
  await session.expectSuccess("placeSwimVolume", {"name": "floating", "liquid": "water", "minimum": [-175, 35, floatBottom], "maximum": [-165, 45, floatTop]})
  # A lake without swim boxes.
  await liquidMaterials(session, folder)
  await session.expectSuccess("sculptAlongPath", {"objectName": "ground", "mode": "carve", "path": [[-180, -100, -10]], "radius": 30, "strength": 1, "profile": [[0, 0], [1, 10]], "conformRim": False})
  await session.expectSuccess("floodWater", {"name": "lake", "seed": [-180, -100], "level": -2, "material": "water"})
  # A walled yard whose only way in is a gap of twice the cell size and a margin, off the cell lines.
  await wall(session, "yardWall", [[38.3, -70], [30, -70], [30, -50], [50, -50], [50, -70], [38.3 + 2 * cellSize + gapMargin, -70]])
  # A wall across a corner at 45 degrees, with ground on both sides.
  first, last = diagonalEnds
  await wall(session, "diagonal", [[edge, -200], [first, first - diagonalLine], [last, last - diagonalLine], [200, -edge]])
  # A room under a wall raised less than a player's height (only a smaller player passes), one under a wall raised less than the
  # containment headroom, and one walled all round.
  await cube(session, "squeezeLintel", [-60, 158, squeezeLintel], [-40, 160, wallHeight])
  await wall(session, "squeezeRoomWall", [[-60, 158], [-60, 180], [-40, 180], [-40, 158]])
  await cube(session, "sealedLintel", [-20, 158, sealedLintel], [0, 160, wallHeight])
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
  run = 20 / math.tan(math.radians(steepDegrees))
  await session.expectSuccess("createPrimitive", {
    "kind": "plane", "name": "slideOff", "size": [20 / math.sin(math.radians(steepDegrees)), 12, 0], "location": [-240 - run / 2, 66, -10],
    "rotationDegrees": [0, -steepDegrees, 0],
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
  "low bank": ([-105, 34, poolTop + stepHeight - heightMargin], "walk"),
  "high bank": ([-105, 46, poolTop + stepHeight + heightMargin], "unreached"),
  "ford": ([-120, -40, fordShelf], "walk"),
  "beside the ford": ([-100, -40, 0], "swim"),
  "shallow pool": ([-120, 10, 0], "walk"),
  "shallow bank": ([-105, 10, shallowTop + stepHeight - heightMargin], "squeeze"),
  "under the stacked boxes": ([-150, 10, 0], "swim"),
  "float platform": ([-180, 40, floatPlatform], "walk"),
  "lake bed": ([-180, -100, -10], "walk"),
  "yard": ([40, -60, 0], "walk"),
  "near the diagonal": ([205, -205, 0], "walk"),
  "past the diagonal": ([234, -234, 0], "unreached"),
  "under the lintel": ([-50, 159, 0], "squeeze"),
  "squeeze room": ([-50, 170, 0], "squeeze"),
  "sealed room": ([-10, 170, 0], "unreached"),
  "closed room": ([30, 170, 0], "unreached"),
  "corridor": ([110, 103, 0], "walk"),
  "in the zone line": ([118, 103, 0], "zoneLine"),
  "past the zone line": ([155, 103, 0], "unreached"),
  "tower": ([90, -120, 30], "walk"),
  "slide off": ([-241.5, 66, -3], "slide"),
  "hermit": ([-180, 120, 20], "walk"),
  "stranded": ([-120, 120, 20], "walk"),
  "far strip": ([0, 220, 0], "walk"),
  "deck": ([120, -30, 0], "walk"),
}
wetProbes = {
  "floating box": ([-170, 40, (floatBottom + floatTop) / 2], "swim"), "water beside the ford": ([-100, -40, poolTop / 2], "swim"),
  "water over the ford": ([-120, -40, (fordShelf + poolTop) / 2], "squeeze"), "shallow water": ([-120, 10, shallowTop / 2], "squeeze"),
  "lower stacked box": ([-150, 10, stackJoin / 2], "swim"), "upper stacked box": ([-150, 10, (stackJoin + poolTop) / 2], "swim"),
}


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
  # The ford's water is too shallow for the design player to swim in: he walks onto its shelf, and only containment, a smaller player
  # floating in shallower water, swims over it; the box's deeper water beside it is swum. The shallow box's water is so too, and the
  # bank beside it is reached only by climbing out of it, a swimmer's move.
  state = lane.read("state")
  for name in ("water over the ford", "shallow water"):
    node = nodeAt(nodes, wetProbes[name][0], wet=True)
    assert nodes.shallow[node] and state[node] & playerReach.reachedFullState == 0 and state[node] & playerReach.reachedState
  assert playerReach.moveNames[moves[nodes.floorNodeAt(probes["ford"][0])]] == "walk"
  assert playerReach.moveNames[moves[nodeAt(nodes, wetProbes["water beside the ford"][0], wet=True)]] in ("enterWater", "drop", "swim")
  predecessor, node = lane.read("predecessor"), nodes.floorNodeAt(probes["shallow bank"][0])
  while playerReach.moveNames[moves[node]] == "walk":
    node = predecessor[node]
  assert playerReach.moveNames[moves[node]] == "climbOut" and nodes.wet[predecessor[node]] and nodes.shallow[predecessor[node]]
  # Stacked boxes are one water, its depth taken from the upper box's top: the lower box, shallow alone, is swum over the floor.
  assert not nodes.shallow[nodeAt(nodes, wetProbes["lower stacked box"][0], wet=True)]
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
  assert reached["swimBoxes"] == [{"name": f"AWT_{name}", "reached": True} for name in ("floating", "ford", "pool", "shallow", "stackHigh", "stackLow")]
  assert [(plot["name"], plot["reached"]) for plot in reached["plotEntrances"]] == [("1 Open Street", True), ("2 Closed Street", False)]
  assert reached["reachGrid"] == playerReach.reachGrid
  assert set(reached["constants"]) == {"playerHeight", "walkableNormalZ", "steepestWalkableDegrees", "stepHeight", "minimumSwimDepth", "smallestPlayerHeight"}
  assert reached["constants"]["stepHeight"]["value"] == stepHeight and reached["constants"]["minimumSwimDepth"]["value"] == minimumSwimDepth
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
  # The open stretch of edge falls onto nothing beside it, along every column wholly between the walls' ends; the face sliding off the
  # edge ends over nothing just past its foot, and is itself a trap.
  openEdge, slideOff = sorted(byKind["voidEdge"], key=lambda problem: problem["center"][1])
  assert near(openEdge["center"], [-240 - cellSize, -25]) and openEdge["length"] == columnsWithin(-40, -10) * cellSize
  assert (openEdge["extent"]["minimum"][1], openEdge["extent"]["maximum"][1]) == (-40 + cellSize / 2, -10 - cellSize / 2)
  slideRun = 20 / math.tan(math.radians(steepDegrees))
  assert near(slideOff["center"], [-240 - slideRun - cellSize, 66])
  assert near(byKind["noneRegion"][0]["center"], [-50, 169])
  assert near(byKind["outsideRegions"][0]["center"], [0, 220])
  assert byKind["disconnectedEntry"][0]["entry"] == "stranded" and byKind["unreachedPlot"][0]["plot"] == "2 Closed Street"
  pitTrap, slideTrap = sorted(byKind["trap"], key=lambda problem: problem["center"][0])[::-1]
  # The pit is the floor wholly between its walls and the ledge's face.
  assert near(pitTrap["center"], [160, 55]) and pitTrap["area"] == columnsWithin(155, 165) * columnsWithin(50, 60) * cellSize ** 2
  assert near(slideTrap["center"], [-240 - slideRun / 2, 66])
  assert near(byKind["viewRegion"][0]["center"], [120, -30]) and byKind["viewRegion"][0]["area"] == centersWithin(100, 140) * centersWithin(-40, -20) * cellSize ** 2
  # Each block top players never reach: every column holding some of it.
  unreachedFloors = sorted((problem["center"][:2], problem["area"]) for problem in byKind["unreachedPlayFloor"])
  steepTop = 35 + 10 / math.tan(math.radians(steepDegrees))
  blocks = sorted([((-105, 46), (-110, -100, 42, 50)), ((40, 70), (35, 45, 65, 75)), ((110, steepTop + 5), (105, 115, steepTop, steepTop + 10))])
  for (found, area), (expected, (lowX, highX, lowY, highY)) in zip(unreachedFloors, blocks):
    assert near(found, expected) and area == columnsOver(lowX, highX) * columnsOver(lowY, highY) * cellSize ** 2
  # The walls are faces standing on the ground, so their tops are no floor, where the diagonal wall joins the edge walls too: there the
  # ground's faces and a wall's merge within the climb, and Recast's own merge, raising a span's top before it compares tops, carried the
  # ground's walkable area up to the wall's top by the order the faces came in. So no floor too small to be a place is left over.
  tops = numpy.flatnonzero(~nodes.wet & (nodes.height > wallHeight - 1))
  topX, topY = nodes.plan(tops)
  for corner in ([edge, -200], [200, -edge]):
    joined = tops[numpy.hypot(topX - corner[0], topY - corner[1]) <= 3]
    assert len(joined) > 2 and not nodes.walkable[joined].any()
  assert reached["moreProblems"] == {}
  assert byKind["undecidedWater"][0]["body"] == "lake" and near(byKind["undecidedWater"][0]["center"], [-180, -100])
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

  # The archive's collision, rebuilt as the server rebuilds it, and its .wtr boxes give the scene's lane node for node; the pond's water
  # over its rim, shallower than players swim in, is swum only by containment.
  fromScene, fromFiles = reachedNodes(scene), reachedNodes(lane)
  assert len(fromScene) > 0 and numpy.array_equal(fromScene, fromFiles)
  assert set(numpy.unique(fromScene[:, 4]).astype(int)) == {playerReach.walkLabel, playerReach.swimLabel, playerReach.squeezeLabel, playerReach.zoneLineLabel}
  squeezed = fromScene[fromScene[:, 4] == playerReach.squeezeLabel]
  assert squeezed[:, 3].all() and (squeezed[:, 2] > -2 - minimumSwimDepth).all()


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


# Walking down the bowl crosses the underworld a step under it; the block rises more than a step, and the lid, the zone line, and the
# swim box hang higher over the ground than any move reaches.
underworld = -(stepHeight + heightMargin)
bowlCenter, bowlRadius = (40, 40), 24
blockHeight = stepHeight + 2 * heightMargin
lidHeight = blockHeight + 2 * playerHeight
highLine = ([-60, 40, lidHeight], [-40, 60, lidHeight + playerHeight])
highPool = ([20, -60, lidHeight], [40, -40, lidHeight + playerHeight])


async def underworldAndLid(session):
  """A walled ground in one play region over an underworld, with a smooth bowl walked down under it, a block under a lid, and a zone
  line and a swim box floating out of reach."""
  await freshScene(session)
  await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [160, 160], "spacing": 4, "location": [0, 0, 0], "collection": "terrain"})
  await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, 0, 0, 0], "underworld": underworld, "shortName": "underworld"})
  await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "lower", "center": [*bowlCenter, 0], "radius": bowlRadius, "strength": 15, "falloff": "smooth"})
  await wall(session, "rim", [[-79.9, -79.9], [79.9, -79.9], [79.9, 79.9], [-79.9, 79.9], [-79.9, -79.9]])
  await cube(session, "coveredBlock", [-50, -50, 0], [-30, -30, blockHeight])
  await session.expectSuccess("placeBoundaryPlane", {"name": "lid", "kind": "lid", "outline": rectangle([-80, -80], [-10, -10]), "height": lidHeight})
  await session.expectSuccess("placeZoneLine", {"number": 1, "label": "high", "minimum": highLine[0], "maximum": highLine[1], "target": otherZone})
  await session.expectSuccess("placeSwimVolume", {"name": "highPool", "liquid": "water", "minimum": highPool[0], "maximum": highPool[1]})
  await region(session, "all", rectangle([-80, -80], [80, 80]), "play", bottom=-60, top=60)


def testLaneLeaksUnderTheUnderworldAndListsWhatNoOneReachesUnderALid(stageBlenderServer):
  async def steps(session):
    await underworldAndLid(session)
    _, reached = await session.expectImage("mapReach", {"problemViews": False})
    return reached

  reached = stageBlenderServer.session(steps)
  nodes = playerReach.Nodes(playerReach.Lane(reached["laneFile"]))
  assert [(problem["kind"], problem["region"]) for problem in reached["problems"]] == [
    ("belowUnderworld", "all"), ("unreachedZoneLine", "all"), ("unreachedSwim", "all"), ("unreachedPlayFloor", "all"),
  ]
  byKind = {problem["kind"]: problem for problem in reached["problems"]}
  # Players walk down the bowl to the underworld and no further: the floor under it is never reached, and the leak runs round the bowl.
  under = ~nodes.wet & nodes.walkable & (nodes.height < underworld)
  assert under.sum() > 0 and not (under & nodes.reached).any()
  leak = byKind["belowUnderworld"]
  assert leak["center"][2] < underworld and math.dist(leak["center"][:2], bowlCenter) < bowlRadius
  assert all(math.dist(corner, bowlCenter) < bowlRadius * math.sqrt(2) for corner in (leak["extent"]["minimum"][:2], leak["extent"]["maximum"][:2]))
  assert not any("no underworld is set" in limit for limit in reached["limits"])
  # The zone line and the swim box no move reaches are listed at their centers.
  assert byKind["unreachedZoneLine"]["zoneLine"] == "ATP_1_high" and byKind["unreachedZoneLine"]["center"] == [(low + high) / 2 for low, high in zip(*highLine)]
  assert byKind["unreachedSwim"]["swimBox"] == "AWT_highPool" and byKind["unreachedSwim"]["center"] == [(low + high) / 2 for low, high in zip(*highPool)]
  # The block's top under the lid is play floor no one reaches; the lid's own top, a boundary, is not, though it lies walkable and open
  # to the sky in the same play region.
  floor = byKind["unreachedPlayFloor"]
  assert near(floor["center"], [-40, -40]) and abs(floor["center"][2] - blockHeight) <= heightMargin and 400 <= floor["area"] <= 400 + 2 * math.sqrt(400) + 1
  lidTop = nodes.floorNodeAt([-70, -70, lidHeight])
  assert bool(nodes.walkable[lidTop]) and nodes.shape[lidTop] & playerReach.boundaryShape and not nodes.reached[lidTop]
  slivers = reached["moreProblems"].get("unreachedPlayFloorSlivers", {"area": 0.0})["area"]
  assert reached["regions"] == [{"name": "all", "access": "play", "reachedShare": reached["regions"][0]["reachedShare"], "unreachedPlayFloor": floor["area"] + slivers}]


def testLaneKeyFollowsSwimBoxesZoneLinesAndTheUnderworld(stageBlenderServer):
  async def steps(session):
    await freshScene(session)
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [64, 64], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, 0, 0, 0]})
    await session.expectSuccess("placeSwimVolume", {"name": "pond", "liquid": "water", "minimum": [-20, -20, -1], "maximum": [-10, -10, 3]})
    await session.expectSuccess("placeZoneLine", {"number": 1, "label": "gate", "minimum": [20, -5, -5], "maximum": [25, 5, 10], "target": otherZone})
    lanes = []

    async def lane():
      _, reached = await session.expectImage("mapReach", {"problemViews": False})
      lanes.append((reached["lane"], reached["laneFile"]))

    await lane()
    await lane()
    await session.expectSuccess("transformObjects", {"names": ["AWT_pond"], "translate": [0, 0, 1]})
    await lane()
    await session.expectSuccess("transformObjects", {"names": ["ATP_1_gate"], "translate": [0, 2, 0]})
    await lane()
    await session.expectSuccess("setZoneProperties", {"underworld": -20})
    await lane()
    await session.expectSuccess("setZoneProperties", {"underworld": -30})
    await lane()
    return lanes

  lanes = stageBlenderServer.session(steps)
  # The same scene reuses its lane; moving the swim box, moving the zone line, setting the underworld, and moving it each computes a new
  # one.
  assert [status for status, _ in lanes] == ["computed", "reused", "computed", "computed", "computed", "computed"]
  files = [path for _, path in lanes]
  assert files[0] == files[1] and len(set(files)) == 5


def testLaneWalksABridgeDeckOverTheGorgeItSpans(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    await session.expectSuccess("buildBridge", {"name": "gorgeBridge", "kitPath": kitPath} | bridge)
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [-110, 0, 0, 90]})
    _, reached = await session.expectImage("mapReach", {"problemViews": False})
    return reached

  reached = stageBlenderServer.session(steps)
  lane = playerReach.Lane(reached["laneFile"])
  nodes = playerReach.Nodes(lane)
  # Over the gorge's middle the highest floor is the bridge's deck, walked at the rims' height from the safe point; the gorge's floor
  # under it is walked too, its open space closed by the deck's underside.
  middle = sum(structurePlots.gorgeFloor) / 2
  deck, floor = nodes.floorNodeAt([middle, 0]), nodes.floorNodeAt([middle, 0, -structurePlots.gorgeDepth])
  assert [playerReach.labelNames[nodes.label[node]] for node in (deck, floor)] == ["walk", "walk"]
  assert int(lane.read("originSeed")[deck]) == 0 and abs(float(nodes.height[deck]) - bridge["start"][2]) <= cellHeight
  assert abs(float(nodes.height[floor]) + structurePlots.gorgeDepth) <= cellHeight
  assert float(nodes.height[floor]) < float(lane.height(nodes.high[floor])) < float(nodes.height[deck])
