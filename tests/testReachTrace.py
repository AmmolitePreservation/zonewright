import base64
import io
import json
import math
import sys
from pathlib import Path

import numpy
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import laneDrawing
import planDrawing
import playerReach
from playerScale import playerHeight, stepHeight
from testCaves import caveCanyon, hall
from testModelsAndDressing import freshScene
from testReach import cellHeight, containmentHeadroom, cube, heightMargin
from testReachPictures import colorShare, greenShare, pixelsAround, planFrame
from testWater import environment

# The trace course: a corridor along +y between boundary walls, its ground slabs 20 thick. Players arrive at its south end, step up two
# risers onto a block whose far side drops more than a step, drop into a trench's water and climb out over its far bank, pass a cliff,
# a slab hung lower over the ground than the containment headroom, and a canopy they walk under, and drop into a pit whose far bank
# stands higher over its water than they climb out.
corridorHalf = 8.0
groundEdge = 12.0
wallHeight = 20.0
slabBottom = -20.0
riser = stepHeight - heightMargin
# The second riser stands back from the first by less than a player's height of tread: the two are one climb run.
riserB = -10 + playerHeight / 2
bed = -(stepHeight + playerHeight)
waterTop = -heightMargin
trench = (40.0, 52.0)
pit = (100.0, 112.0)
farEnd = 130.0
highBank = waterTop + stepHeight + 2 * heightMargin
cliffHeight = 2 * stepHeight + 2 * heightMargin
lidGap = containmentHeadroom / 2
canopyBottom = playerHeight + 2 * heightMargin
safePoint = [0, -30, 0, 0]
gate = [0, 90]
routeTarget = [0, 70]


async def traceCourse(session):
  await freshScene(session)
  await cube(session, "groundA", [-groundEdge, -40, slabBottom], [groundEdge, trench[0], 0])
  await cube(session, "trenchBed", [-groundEdge, trench[0], slabBottom], [groundEdge, trench[1], bed])
  await cube(session, "groundB", [-groundEdge, trench[1], slabBottom], [groundEdge, pit[0], 0])
  await cube(session, "pitBed", [-groundEdge, pit[0], slabBottom], [groundEdge, pit[1], bed])
  await cube(session, "groundC", [-groundEdge, pit[1], slabBottom], [groundEdge, farEnd, highBank])
  await cube(session, "riserA", [-groundEdge, -10, 0], [groundEdge, 10, riser])
  await cube(session, "riserB", [-groundEdge, riserB, riser], [groundEdge, 10, 2 * riser])
  # Blocks stand a column inside the walls: a wall follows the highest ground it meets, so over a block's top its foot would leave a gap.
  await cube(session, "cliff", [2, 75, 0], [corridorHalf - 1, 85, cliffHeight])
  await cube(session, "lowLid", [-corridorHalf + 1, 85, lidGap], [-2, 95, wallHeight])
  await cube(session, "canopy", [-corridorHalf + 1, 60, canopyBottom], [-3, 70, canopyBottom + 2])
  await session.expectSuccess("placeSwimVolume", {"name": "trench", "liquid": "water", "minimum": [-groundEdge, trench[0], bed], "maximum": [groundEdge, trench[1], waterTop]})
  await session.expectSuccess("placeSwimVolume", {"name": "pit", "liquid": "water", "minimum": [-groundEdge, pit[0], bed], "maximum": [groundEdge, pit[1], waterTop]})
  # The wall follows the ground between samples, so a sample stands just inside each bank: one on the ground beside a bank would carry
  # the wall's foot over the water's bed, and the lane would find the gap under it.
  banks = [trench[0] + 0.5, trench[1] - 0.5, pit[0] + 0.5, pit[1] - 0.5]
  side = [[corridorHalf, y] for y in [-38, *banks, farEnd - 2]]
  ring = side + [[-x, y] for x, y in side[::-1]] + [side[0]]
  await session.expectSuccess("placeBoundaryWall", {"name": "corridorWall", "path": ring, "height": wallHeight})
  await session.expectSuccess("setZoneProperties", environment | {"safePoint": safePoint})
  await session.expectImage("placeEntry", {"name": "gate", "at": gate, "headingDegrees": 180, "kind": "landing"})


def near(point, expected, tolerance=math.sqrt(2) * playerReach.reachGrid["cellSize"]):
  return math.dist(point[:2], expected[:2]) <= tolerance


def testTraceReachRoutesOverTheDropAndTheSwim(stageBlenderServer, tmp_path):
  async def steps(session):
    await traceCourse(session)
    routed, _ = await session.call("traceReach", {"to": routeTarget, "fromEntry": "safe point"})
    fromGate, _ = await session.call("traceReach", {"to": routeTarget})
    unknown = await session.expectError("traceReach", {"to": routeTarget, "fromEntry": "harbor"})
    outside = await session.expectError("traceReach", {"to": [0, farEnd + 10]})
    return routed, fromGate, unknown, outside

  routed, fromGate, unknown, outside = stageBlenderServer.session(steps)
  assert routed.is_error is False, routed.content[-1].text
  assert [content.type for content in routed.content] == ["image", "image", "text"]
  result = json.loads(routed.content[2].text)
  # A riser is a step only where no walkable face rises as far across a column.
  assert playerReach.stepRise(playerReach.Lane(result["laneFile"])) < riser
  assert (result["reached"], result["from"], result["target"]["label"]) == (True, "safe point", "walk")
  legs = result["legs"]
  assert [leg["move"] for leg in legs] == ["walk", "step", "walk", "drop", "walk", "drop", "swim", "climbOut", "walk"]
  assert [leg["label"] for leg in legs] == ["walk"] * 5 + ["swim", "swim", "walk", "walk"]
  # The two risers are one climb run of two steps, up both.
  assert legs[1]["steps"] == 2 and all("steps" not in leg for leg in legs[2:])
  rises = [leg["rise"] for leg in legs]
  for index, expected in ((1, 2 * riser), (3, -2 * riser), (5, waterTop), (7, -waterTop)):
    assert abs(rises[index] - expected) <= cellHeight, (index, rises[index], expected)
  # The first riser's face, the riser block's far edge, and the trench's banks are where the moves start, to a column.
  for index, y in ((1, -10), (3, 10), (5, trench[0]), (7, trench[1])):
    assert abs(legs[index]["start"][1] + 0.5 - y) <= 1, (index, legs[index]["start"], y)
  assert abs(legs[6]["length"] - (trench[1] - trench[0] - 2)) <= 2 and near(legs[-1]["end"], routeTarget)
  assert result["length"] == round(sum(leg["length"] for leg in legs), 1)
  assert [(move["number"], move["move"]) for move in result["decisive"]] == [(1, "step"), (2, "drop"), (3, "drop"), (4, "climbOut")]
  # Four decisive moves, four frames: each stands on the route 12 before its move, the climb out's on the bank it dropped from, and looks
  # up the corridor at where the move ends.
  frames = result["frames"]
  assert [frame["number"] for frame in frames] == [1, 2, 3, 4]
  for frame, move in zip(frames, result["decisive"]):
    assert set(frame["view"]) == {"standAt", "headingDegrees", "pitchDegrees"} and abs(frame["view"]["headingDegrees"]) < 1e-6
    assert abs(frame["view"]["standAt"][1] - (legs[move["leg"]]["start"][1] - playerReach.frameStandBack)) <= 1, (frame, move)
  # The climb's frame looks up at the top of its run; the drop's looks down at where it lands.
  assert frames[1]["view"]["pitchDegrees"] < 0 < frames[0]["view"]["pitchDegrees"]
  sheet = Image.open(io.BytesIO(base64.b64decode(routed.content[1].data)))
  assert sheet.size == (result["frameSheet"]["width"], result["frameSheet"]["height"])
  # In plan the ribbon runs along y, across the drawing, in walk green over a dark casing through the riser block, and the decisive
  # moves are ringed white.
  plan = Image.open(io.BytesIO(base64.b64decode(routed.content[0].data)))
  plan.save(tmp_path / "routePlan.png")
  sheet.save(tmp_path / "routeFrames.jpg")
  frame = planFrame(result["plan"])
  x, y = (round(value) for value in frame.pixel([0, 0]))
  across = numpy.asarray(plan.convert("RGB"))[y - laneDrawing.ribbonCasingWidth:y + laneDrawing.ribbonCasingWidth + 1, x].astype(int)
  assert colorShare(across, laneDrawing.labelColors[playerReach.walkLabel]) > 0.2 and colorShare(across, laneDrawing.ribbonCasing) > 0.1
  assert colorShare(pixelsAround(plan, result["plan"], legs[3]["start"], 6), laneDrawing.decisiveFill) > 0.2
  # From every arrival, the gate's landing lies nearer: one walk south to the point.
  gated = json.loads(fromGate.content[-1].text)
  assert (gated["from"], [leg["move"] for leg in gated["legs"]], gated["decisive"]) == ("gate", ["walk"], [])
  assert [content.type for content in fromGate.content] == ["image", "text"]
  assert "No arrival named 'harbor'" in unknown and "'gate'" in unknown and "'safe point'" in unknown
  assert "lies outside what the zone ships" in outside


def testTraceReachNamesWhatSeparates(stageBlenderServer, tmp_path):
  targets = {"cliff": {"to": [5, 80]}, "boundary": {"to": [10, 60]}, "headroom": {"to": [-5, 90, 0]}, "water": {"to": [0, 120]}, "fromGate": {"to": [0, 0], "fromEntry": "gate"}}

  async def steps(session):
    await traceCourse(session)
    found = {}
    for name, arguments in targets.items():
      result, _ = await session.call("traceReach", arguments)
      found[name] = result
    return found

  found = stageBlenderServer.session(steps)
  results = {}
  for name, result in found.items():
    assert result.is_error is False, (name, result.content[-1].text)
    assert [content.type for content in result.content] == ["image", "image", "text"], name
    results[name] = json.loads(result.content[2].text)
    assert results[name]["reached"] is False, name
    Image.open(io.BytesIO(base64.b64decode(result.content[0].data))).save(tmp_path / f"{name}Section.png")
    Image.open(io.BytesIO(base64.b64decode(result.content[1].data))).save(tmp_path / f"{name}View.png")
  # The cliff's top rises more than the step from the ground beside it, and the cast across meets the cliff's face.
  cliff = results["cliff"]
  assert near(cliff["nearest"]["at"], [1, 80]) and cliff["nearest"]["label"] == "walk"
  assert [stop["kind"] for stop in cliff["separations"]] == ["rise"]
  rise = cliff["separations"][0]
  assert abs(rise["height"] - cliffHeight) <= cellHeight and rise["limit"] == stepHeight and abs(rise["over"] - (cliffHeight - stepHeight)) <= cellHeight
  assert rise["face"] == "cliff"
  # Outside the corridor, the wall is named.
  boundary = results["boundary"]
  assert near(boundary["nearest"]["at"], [corridorHalf - 1, 60])
  assert [(stop["kind"], stop.get("name")) for stop in boundary["separations"]] == [("boundary", "corridorWall")]
  assert abs(boundary["separations"][0]["at"][0] - corridorHalf) <= 0.01
  # Under the low slab the lane's node is the slab's inside, which no one reaches, and what keeps players out is the gap's clearance, not
  # the slab's height.
  headroom = results["headroom"]
  assert headroom["target"]["label"] == "unreached" and near(headroom["nearest"]["at"], [-1, 90])
  assert [stop["kind"] for stop in headroom["separations"]] == ["headroom"]
  assert abs(headroom["separations"][0]["clearance"] - lidGap) <= 0.02 and headroom["separations"][0]["needs"] == containmentHeadroom
  # From the pit's water the far bank stands higher over the box's top than players climb out.
  water = results["water"]
  assert water["nearest"]["label"] == "swim" and near(water["nearest"]["at"], [0, pit[1] - 1])
  assert [stop["kind"] for stop in water["separations"]] == ["water"]
  stop = water["separations"][0]
  assert (stop["swimBox"], stop["boxTop"]) == ("AWT_pit", waterTop)
  assert abs(stop["bank"] - highBank) <= cellHeight and abs(stop["overClimb"] - (highBank - waterTop - stepHeight)) <= cellHeight
  # Each section runs from the reached node to the point, run on past both, with the lane: the water trace's shows the pit swum.
  section = water["section"]
  cut = json.loads(Path(section["laneCut"]).read_text())
  assert any(band["label"] == "swim" and abs(band["z"][1] - waterTop) <= cellHeight for band in cut["bands"])
  assert any(abs(mark["z"] - highBank) <= cellHeight for mark in cut["unreached"])
  # Each view looks at the point from up to 12 back on the lane: across the pit from the bank the pit was dropped into, and at the cliff
  # from the corridor's far wall.
  assert near(water["view"]["view"]["standAt"], [0, pit[1] - 1 - 12]) and abs(water["view"]["view"]["headingDegrees"]) < 1e-6
  assert near(cliff["view"]["view"]["standAt"], [-corridorHalf + 1, 80]) and abs(cliff["view"]["view"]["headingDegrees"] - 90) < 1e-6
  assert any(entry.startswith("doors: taken as open") for entry in cliff["notModeled"])
  # From the gate alone, players swim the trench back and climb out, but the riser block's far face rises more than a step; its section
  # draws the gate's lane, in which the block's top is walkable floor never reached.
  gated = results["fromGate"]
  assert gated["nearest"]["from"] == "gate" and near(gated["nearest"]["at"], [0, 11])
  assert [stop["kind"] for stop in gated["separations"]] == ["rise"] and abs(gated["separations"][0]["height"] - 2 * riser) <= cellHeight
  gatedCut = json.loads(Path(gated["section"]["laneCut"]).read_text())
  assert any(abs(mark["z"] - 2 * riser) <= cellHeight for mark in gatedCut["unreached"]) and not any(abs(mark["z"] - 2 * riser) <= cellHeight for mark in gatedCut["bars"])


def testSeparationsAlongOneFaceAreOne():
  # A steep face crossed over three columns, a rise at its top, then a boundary met twice side by side and another boundary.
  stated = [
    {"kind": "steep", "along": 3.0, "at": [3, 0, 2], "degrees": 75.0, "steepestWalkable": 71.9},
    {"kind": "steep", "along": 4.0, "at": [4, 0, 6], "degrees": None, "why": "no face crosses the column's middle", "steepestWalkable": 71.9},
    {"kind": "steep", "along": 5.0, "at": [5, 0, 10], "degrees": 80.0, "steepestWalkable": 71.9},
    {"kind": "rise", "along": 6.0, "at": [6, 0, 16], "from": [5, 0, 10], "height": 6.0, "limit": 4.5, "over": 1.5},
    {"kind": "rise", "along": 7.0, "at": [7, 0, 23], "from": [6, 0, 16], "height": 7.0, "limit": 4.5, "over": 2.5},
    {"kind": "boundary", "along": 9.0, "at": [9, 0, 26], "name": "rim"},
    {"kind": "boundary", "along": 10.0, "at": [10, 0, 26], "name": "rim"},
    {"kind": "boundary", "along": 11.0, "at": [11, 0, 26], "name": "fence"},
  ]
  merged = playerReach.mergedSeparations(stated, 1.0)
  assert [(stop["kind"], stop["columns"], stop.get("name")) for stop in merged] == [("steep", 3, None), ("rise", 2, None), ("boundary", 2, "rim"), ("boundary", 1, "fence")]
  steep, rise = merged[0], merged[1]
  assert (steep["degrees"], "why" in steep, steep["at"], steep["through"]) == (80.0, False, [3, 0, 2], [5, 0, 10])
  assert (rise["height"], rise["over"], rise["through"]) == (13.0, 8.5, [7, 0, 23])


def mapPixel(description, point):
  """Where a zone point falls in a map view's render, from its center, width, height, and units per pixel."""
  center, size = description["mapCenter"], description["unitsPerPixel"]
  return round((center[1] + description["mapWidth"] / 2 - point[1]) / size - 0.5), round((center[0] + description["mapHeight"] / 2 - point[0]) / size - 0.5)


def testRenderViewTintsReachedFloor(stageBlenderServer, tmp_path):
  overhead = {"map": {"center": [0, 92], "width": 70}}
  eyeLevel = {"standAt": [0, 4, 2 * riser], "headingDegrees": 0, "pitchDegrees": -12}

  async def steps(session):
    await traceCourse(session)
    plain = await session.expectImage("renderView", {"view": overhead})
    laned = await session.expectImage("renderView", {"view": overhead, "lane": True})
    looking = await session.expectImage("renderView", {"view": eyeLevel, "lane": True})
    lookingPlain = await session.expectImage("renderView", {"view": eyeLevel})
    return plain, laned, looking, lookingPlain

  (plainImage, plain), (lanedImage, laned), (lookingImage, looking), (lookingPlainImage, _) = stageBlenderServer.session(steps)
  Image.open(io.BytesIO(lookingImage)).save(tmp_path / "laneTint.png")
  Image.open(io.BytesIO(lanedImage)).save(tmp_path / "laneTintMap.png")
  assert "laneState" not in plain and laned["laneState"]["lane"] in ("computed", "reused")
  bare = numpy.asarray(Image.open(io.BytesIO(plainImage)).convert("RGB")).astype(float)
  tinted = numpy.asarray(Image.open(io.BytesIO(lanedImage)).convert("RGB")).astype(float)

  def share(point, label):
    """How far, channel by channel, a pixel moved from the plain render toward a label's color."""
    x, y = mapPixel(laned, point)
    color = numpy.array(laneDrawing.labelColors[label], dtype=float)
    return (tinted[y, x] - bare[y, x]) / (color - bare[y, x])

  def unchanged(point):
    x, y = mapPixel(laned, point)
    return numpy.abs(tinted[y, x] - bare[y, x]).max()

  # Open ground walked and the pit's bed swum are tinted toward their labels' colors by one share in every channel; the canopy's top
  # over walked ground hides it, and the cliff's top and the high bank, never reached, are as they were.
  walked, swum = share([0, 65], playerReach.walkLabel), share([0, 106], playerReach.swimLabel)
  assert 0.2 < walked.mean() < 0.6 and numpy.abs(numpy.concatenate([walked, swum]) - walked.mean()).max() < 0.05
  assert unchanged([-6, 65]) <= 1 and unchanged([5, 80]) <= 1 and unchanged([0, 120]) <= 1
  # At eye level only what lies within the far clip and the field is drawn, merged into fewer rectangles than columns.
  tint = looking["laneTint"]
  assert 0 < tint["rectangles"] < tint["columns"] and tint["farClip"] == environment["maxClip"]
  # The riser block's top just ahead, walked, is greener than it was.
  ahead = [numpy.asarray(Image.open(io.BytesIO(image)).convert("RGB")).astype(int)[500:530, 440:520].reshape(-1, 3) for image in (lookingImage, lookingPlainImage)]
  assert greenShare(ahead[0]) == 1.0 and greenShare(ahead[1]) == 0


def sectionPixel(summary, s, z):
  return planDrawing.sectionPlace(summary, planDrawing.sectionSize, planDrawing.sectionFit(summary, planDrawing.sectionSize))(s, z)


def testSectionDrawsTheLane(stageBlenderServer, tmp_path):
  start, end = [0, -35], [0, 125]

  async def steps(session):
    await traceCourse(session)
    laned = await session.expectImage("renderSection", {"start": start, "end": end, "layers": ["ground", "swim", "boundaries", "lane"]})
    plain = await session.expectImage("renderSection", {"start": start, "end": end, "layers": ["ground", "swim", "boundaries"]})
    return laned, plain

  (lanedImage, laned), (plainImage, plain) = stageBlenderServer.session(steps)
  section = Image.open(io.BytesIO(lanedImage))
  section.save(tmp_path / "courseSection.png")
  assert "laneState" not in plain and Image.open(io.BytesIO(plainImage)).height == planDrawing.sectionSize[1] and section.height > planDrawing.sectionSize[1]
  cut = json.loads(Path(laned["laneState"]["laneCut"]).read_text())
  assert laned["laneState"]["counts"] == {kind: len(marks) for kind, marks in cut.items()}

  def bar(z, label, s0, s1):
    """The bar at height z of a label spanning s0 to s1 along the line, columns centered on whole units."""
    matches = [mark for mark in cut["bars"] if mark["label"] == label and abs(mark["z"] - z) <= cellHeight and abs(mark["s"][0] - s0) <= 1 and abs(mark["s"][1] - s1) <= 1]
    return matches

  along = lambda y: y - start[1]
  assert bar(0, "walk", 0, along(-10.5))
  assert bar(riser, "walk", along(-10.5), along(riserB - 0.5)) and bar(2 * riser, "walk", along(riserB - 0.5), along(10.5))
  assert bar(0, "walk", along(10.5), along(trench[0] + 0.5)) and bar(0, "walk", along(trench[1] - 0.5), along(pit[0] + 0.5))
  # The trench and the pit are swum to their beds, which a sink reaches, so they are floor in a swim box.
  assert bar(bed, "swim", along(trench[0] + 0.5), along(trench[1] - 0.5)) and bar(bed, "swim", along(pit[0] + 0.5), along(pit[1] - 0.5))
  assert [band["label"] for band in cut["bands"]] == ["swim", "swim"]
  for band, (first, last) in zip(cut["bands"], (trench, pit)):
    assert abs(band["z"][0] - bed) <= cellHeight and abs(band["z"][1] - waterTop) <= cellHeight
    assert abs(band["s"][0] - along(first + 0.5)) <= 1 and abs(band["s"][1] - along(last - 0.5)) <= 1
  # Past the pit the high bank is walkable floor never reached, and the walled corridor leaks nowhere.
  highFloors = [mark for mark in cut["unreached"] if abs(mark["z"] - highBank) <= cellHeight]
  assert len(highFloors) == 1 and abs(highFloors[0]["s"][0] - along(pit[1] - 0.5)) <= 1 and abs(highFloors[0]["s"][1] - along(end[1])) <= 1
  assert cut["leaks"] == []
  # The bars stand on their floors in walk green, where the section without the lane draws none; the pit's water is a blue band.
  pixels = numpy.asarray(section.convert("RGB")).astype(int)
  plainPixels = numpy.asarray(Image.open(io.BytesIO(plainImage)).convert("RGB")).astype(int)
  for y, z in ((0, bar(2 * riser, "walk", along(riserB - 0.5), along(10.5))[0]["z"]), (-20, bar(0, "walk", 0, along(-10.5))[0]["z"])):
    x, top = (round(value) for value in sectionPixel(laned, along(y), z))
    above = (slice(top - laneDrawing.sectionBarHeight + 1, top - 1), slice(x - 3, x + 4))
    assert colorShare(pixels[above].reshape(-1, 3), laneDrawing.labelColors[playerReach.walkLabel]) == 1.0
    assert greenShare(plainPixels[above].reshape(-1, 3)) == 0
  x, y = (round(value) for value in sectionPixel(laned, along((pit[0] + pit[1]) / 2), (bed + waterTop) / 2))
  blue = pixels[y, x]
  assert blue[2] > blue[0] + 40 and blue[2] > blue[1]


def testSectionAlongACaveDrawsItsWalkedFloor(stageBlenderServer, tmp_path):
  async def steps(session):
    await caveCanyon(session, tmp_path)
    await session.expectSuccess("cutCave", hall)
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, -100, 2, 0]})
    return await session.expectImage("renderSection", {"cave": {"objectName": "ground", "name": "hall"}, "layers": ["ground", "caves", "lane"]})

  image, summary = stageBlenderServer.session(steps)
  Image.open(io.BytesIO(image)).save(tmp_path / "caveSection.png")
  cut = json.loads(Path(summary["laneState"]["laneCut"]).read_text())
  # Along the run's middle the floor is walked from the mouth to the room's end, each bar within the floor's breakup of its designed height.
  floors = [entry for entry in summary["caves"] if entry["run"] == "main"][0]["floor"]
  walked = [mark for mark in cut["bars"] if mark["label"] == "walk"]
  for s0, z0, s1, z1 in floors:
    middle = (s0 + s1) / 2
    designed = z0 + (z1 - z0) * (middle - s0) / (s1 - s0)
    holding = [mark for mark in walked if mark["s"][0] <= middle <= mark["s"][1]]
    assert any(abs(mark["z"] - designed) <= hall["breakup"]["amplitude"] + cellHeight for mark in holding), (middle, designed, holding)
