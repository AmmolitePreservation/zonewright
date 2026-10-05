import base64
import io
import json
import math
import sys
from pathlib import Path

import numpy
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import laneViews
import playerReach
import viewSheets
from playerScale import eyeHeight, playerHeight, swimEyeAboveSurface, walkableNormalZ
from testModelsAndDressing import freshScene
from testReach import cellHeight, cube, reachCourse
from testWater import environment
from conftest import writePNG


def sheetOf(result, tmp_path, name):
  """A viewFromLane result's sheet (saved under name) and its report."""
  assert result.is_error is False, result.content[0].text
  assert [content.type for content in result.content] == ["image", "text"] and result.content[0].mime_type == "image/jpeg"
  sheet = Image.open(io.BytesIO(base64.b64decode(result.content[0].data)))
  sheet.save(tmp_path / name)
  report = json.loads(result.content[1].text)
  assert sheet.size == (report["sheet"]["width"], report["sheet"]["height"])
  return sheet, report


def standingNode(nodes, eye):
  """The lane node an eye stands on: the floor whose ground it stands on (within the lane's tolerance of it, as a seed's footing is),
  or the reached water in its column."""
  if eye["label"] == "walk":
    return nodes.floorNodeAt(eye["standing"], tolerance=playerReach.seedTolerance(walkableNormalZ))
  column = nodes.lane.columnAt(eye["standing"])
  inColumn = numpy.arange(nodes.columnStart[column], nodes.columnStart[column + 1])
  water = inColumn[nodes.wet[inColumn] & nodes.reached[inColumn]]
  return int(water[0]) if len(water) else None


def worldEndHolds(nodes, point):
  """Whether a world's end point lies on reached floor beside a column where the lane holds no node, or on the grid's edge."""
  lane = nodes.lane
  column = lane.columnAt(point)
  inColumn = numpy.arange(nodes.columnStart[column], nodes.columnStart[column + 1])
  if not nodes.reached[inColumn].any():
    return False
  for dx in (-lane.cellSize, 0.0, lane.cellSize):
    for dy in (-lane.cellSize, 0.0, lane.cellSize):
      beside = lane.columnAt([point[0] + dx, point[1] + dy])
      if beside is None or nodes.columnStart[beside] == nodes.columnStart[beside + 1]:
        return True
  return False


def markedPixels(sheet, cell):
  """How many pixels of a sheet's view cell (cells of the first row only, whose places do not hang on the labels' height) are drawn in
  the color laneViews marks a target with."""
  left = viewSheets.gap + cell * (viewSheets.cellSize[0] + viewSheets.gap)
  pixels = numpy.asarray(sheet.crop((left, viewSheets.gap, left + viewSheets.cellSize[0], viewSheets.gap + viewSheets.cellSize[1]))).astype(int)
  return int((numpy.abs(pixels - numpy.array(laneViews.markColor)).max(axis=2) <= 60).sum())


def testLaneTourSpreadsAwayFromTheEntries(stageBlenderServer, tmp_path):
  async def steps(session):
    await reachCourse(session, tmp_path)
    toured, _ = await session.call("viewFromLane", {})
    plot, _ = await session.call("viewFromLane", {"target": "1 Open Street"})
    return toured, plot

  toured, plot = stageBlenderServer.session(steps)
  sheet, report = sheetOf(toured, tmp_path, "courseTour.jpg")
  eyes = report["eyes"]
  assert report["tour"] == "computed" and [eye["number"] for eye in eyes] == list(range(1, laneViews.tourEyes + 1))
  # Eight views three to a row, then the plan with its legend under them at its own size.
  assert sheet.width == viewSheets.gap + 3 * (viewSheets.cellSize[0] + viewSheets.gap)
  assert sheet.height > 3 * viewSheets.cellSize[1] + report["plan"]["frameSize"][1]
  assert (report["constants"]["eyeHeight"]["value"], report["constants"]["swimEyeAboveSurface"]["value"]) == (eyeHeight, swimEyeAboveSurface)
  assert report["farSight"] == min(environment["maxClip"], environment["fogEnd"])
  assert [arrival["name"] for arrival in report["arrivals"]] == ["safe point", "hermit", "stranded"]
  kept = json.loads(Path(report["tourFile"]).read_text(encoding="utf-8"))
  arrivals = numpy.array([arrival["at"] for arrival in report["arrivals"]])
  ends = numpy.array(kept["worldEnd"]).reshape(-1, 3)
  nodes = playerReach.Nodes(playerReach.Lane(report["laneFile"]))
  # The course's ground runs off into nothing along its open stretch: the world ends there, and every point said to is such a place.
  assert report["worldEndPoints"] == len(ends) > 0 and all(worldEndHolds(nodes, point) for point in ends)
  # Every eye stands on a node the lane reaches, where a player stands (never only a smaller one), labelled as the eye says, on the
  # ground under that floor, its eye a player's eye over it.
  for eye in eyes:
    node = standingNode(nodes, eye)
    assert eye["label"] in ("walk", "swim") and node is not None and bool(nodes.reached[node])
    assert playerReach.labelNames[nodes.label[node]] == eye["label"]
    lift = swimEyeAboveSurface if eye["swimming"] else eyeHeight
    assert eye["eye"] == pytest.approx([*eye["standing"][:2], eye["standing"][2] + lift], abs=0.011)
    assert eye["headingDegrees"] % laneViews.sectorDegrees == 0 and eye["run"] > 0 and eye["direction"] == laneViews.directionName(eye["headingDegrees"])
    assert eye["view"]["eye"] == eye["eye"] and eye["view"]["target"][2] == eye["eye"][2]
  # Each eye is as far as it lies from the arrivals, the world's end, and the eyes before it (its spacing, measured on the lane's floor,
  # which the ground under it lies within the lane's tolerance of), so the spacings never grow; the first is, to within two blocks (one
  # per block, a block's member kept off a cramped strip beside a roomier one), the farthest of all the floor the lane reaches from the
  # arrivals and the world's end.
  tolerance = playerReach.seedTolerance(walkableNormalZ)
  standing = numpy.array([eye["standing"] for eye in eyes])
  for index, eye in enumerate(eyes):
    before = numpy.concatenate([arrivals, ends, standing[:index]])
    assert eye["spacing"] == pytest.approx(float(numpy.linalg.norm(before - standing[index], axis=1).min()), abs=2 * tolerance)
  spacings = [eye["spacing"] for eye in eyes]
  assert spacings == sorted(spacings, reverse=True)
  walked = numpy.flatnonzero(nodes.label == playerReach.walkLabel)
  planX, planY = nodes.plan(walked)
  places = numpy.stack([planX, planY, nodes.height[walked]], axis=1)
  chosenAlready = numpy.concatenate([arrivals, ends])
  farthest = float(numpy.min([numpy.linalg.norm(places - point, axis=1) for point in chosenAlready], axis=0).max())
  assert farthest - 2 * laneViews.eyeBlock * math.sqrt(3) <= spacings[0] <= farthest + tolerance

  _, plotReport = sheetOf(plot, tmp_path, "plotTarget.jpg")
  target = plotReport["target"]
  assert (target["kind"], target["name"], target["points"]) == ("object", "1 Open Street", laneViews.surfaceSamples)
  # The plot is judged by points on its outline's band, lying flat over the ground: only its top has points, and players see it.
  sides = plotReport["sides"]
  assert [sides[name] for name in ("north", "east", "south", "west")] == [None] * 4 and sides["top"] >= 0.5
  plotEyes = plotReport["eyes"]
  assert len(plotEyes) == laneViews.targetEyes and all(eye["seenShare"] > 0 for eye in plotEyes)
  # Each eye looks at the middle of what it sees of the plot, inside its bounds, and stands about where its view holds the plot whole.
  bearings = [eye["bearingDegrees"] for eye in plotEyes]
  assert bearings == sorted(bearings)
  low, high = numpy.array(target["bounds"]["minimum"]), numpy.array(target["bounds"]["maximum"])
  assert all(((low - 0.01 <= eye["view"]["target"]) & (eye["view"]["target"] <= high + 0.01)).all() for eye in plotEyes)
  framing = laneViews.framingRadii * target["radius"]
  assert all(abs(eye["distance"] - framing) <= target["radius"] for eye in plotEyes), [eye["distance"] for eye in plotEyes]
  assert "tour" not in plotReport and set(plotReport["sides"]) == set(laneViews.sideNames)


def inRun(headingDegrees, run):
  """Whether a heading lies in a run of bearings, which may run on past 360."""
  return (headingDegrees - run["fromDegrees"]) % 360 <= (run["toDegrees"] - run["fromDegrees"]) % 360


def centerPatch(path):
  """The mean color of the middle of a render, where a view draws what it looks at."""
  image = numpy.asarray(viewSheets.openRender(path)).astype(float)
  height, width = image.shape[:2]
  return image[height // 2 - 3:height // 2 + 4, width // 2 - 3:width // 2 + 4].reshape(-1, 3).mean(axis=0)


def isBrick(color):
  red, green, blue = color
  return red > green + 40 and red > blue + 40


async def screenedShed(session, folder):
  """Open ground with the safe point west of a shed whose whole east side a tall screen covers, past the step and out past its ends; a
  long low fence further west, a hedge hiding its southern half and middle from the west."""
  await freshScene(session)
  await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [200, 200], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
  for name, color in (("grass", (90, 120, 60, 255)), ("brick", (190, 30, 25, 255)), ("stone", (120, 120, 125, 255))):
    await session.expectSuccess("createMaterial", {"name": name, "diffuseTexture": str(writePNG(folder / f"{name}.png", 4, 4, color))})
  await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
  await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 64, "direction": [0, 0, 1]})
  await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, 60, 0, 0]})
  await cube(session, "shed", [-5, -5, 0], [5, 5, 8])
  await cube(session, "screen", [-40, -11, 0], [40, -5, 20])
  await cube(session, "fence", [-45, 85, 0], [45, 87, 5])
  await cube(session, "hedge", [-45, 88, 0], [5, 96, 12])
  for name, material in (("shed", "brick"), ("screen", "stone"), ("fence", "brick"), ("hedge", "stone")):
    await session.expectSuccess("assignMaterial", {"objectName": name, "materialName": material})
    await session.expectSuccess("projectUVs", {"objectName": name, "method": "box", "worldUnitsPerRepeat": 8})
  await session.expectSuccess("createRegion", {
    "name": "lawn", "outline": [[-30, 20], [30, 20], [30, 80], [-30, 80]], "bottom": -10, "top": 30, "intent": "open lawn west of the shed", "access": "play",
  })


def testViewFromLaneReportsTheUnseenSide(stageBlenderServer, tmp_path):
  async def steps(session):
    await screenedShed(session, tmp_path)
    calls = {}
    for name, arguments in (
      ("shed", {"target": "shed"}), ("fence", {"target": "fence"}), ("lawn", {"target": "lawn", "count": 1}), ("point", {"target": [0, 50], "count": 1}),
      ("tour", {"count": 1}),
    ):
      calls[name], _ = await session.call("viewFromLane", arguments)
    return calls

  calls = stageBlenderServer.session(steps)
  shedSheet, report = sheetOf(calls["shed"], tmp_path, "shedTarget.jpg")
  # The screen covers the shed's east (-Y) side from every eye, no eye stands high enough to see its roof, and its underside lies on the
  # ground; its other sides are seen whole.
  assert report["target"]["points"] == laneViews.surfaceSamples
  assert report["sides"] == {"north": 1.0, "east": 0.0, "south": 1.0, "west": 1.0, "top": 0.0, "underside": 0.0}
  # From the east no player sees it: the bearings round 180 (toward -Y) are blocked, though players stand there; the west's are seen.
  unseen = report["unseenSectors"]
  assert len(unseen) == 1 and unseen[0]["why"] == "blocked" and inRun(180, unseen[0]) and not inRun(0, unseen[0])
  eyes = report["eyes"]
  assert len(eyes) == laneViews.targetEyes and all(eye["seenShare"] > 0 for eye in eyes)
  assert not any(inRun(eye["bearingDegrees"], unseen[0]) for eye in eyes)
  # One eye per sector, spread round the sectors it is seen from: every one of them lies within a sector of a chosen one.
  sectors = [round(eye["bearingDegrees"] / laneViews.sectorDegrees) % laneViews.bearings for eye in eyes]
  assert len(set(sectors)) == len(sectors)
  seenSectors = [sector for sector in range(laneViews.bearings) if not inRun(sector * laneViews.sectorDegrees, unseen[0])]
  assert max(min(laneViews.sectorsApart(sector, [other]) for other in sectors) for sector in seenSectors) <= 1
  # Each stands about where its view holds the shed whole, looks at the middle of what its eye sees of it, and draws its brick there.
  framing = laneViews.framingRadii * report["target"]["radius"]
  assert all(abs(eye["distance"] - framing) <= report["target"]["radius"] for eye in eyes), [eye["distance"] for eye in eyes]
  for eye in eyes:
    assert isBrick(centerPatch(eye["outputPath"])), eye["number"]
  assert all(markedPixels(shedSheet, cell) > 50 for cell in range(3))

  _, fenceReport = sheetOf(calls["fence"], tmp_path, "fenceTarget.jpg")
  # The hedge hides the fence's middle from the west: an eye there looks at the part of the fence it sees, not at its middle, and every
  # view draws the fence's brick in its middle.
  middle = numpy.array(fenceReport["target"]["center"])
  assert any(numpy.linalg.norm(numpy.array(eye["view"]["target"][:2]) - middle[:2]) > 10 for eye in fenceReport["eyes"])
  for eye in fenceReport["eyes"]:
    assert isBrick(centerPatch(eye["outputPath"])), eye["number"]

  lawnSheet, lawnReport = sheetOf(calls["lawn"], tmp_path, "lawnTarget.jpg")
  # The lawn's floor lies open round the safe point: every part of it is seen, and its view draws its outline along the ground.
  assert lawnReport["target"]["kind"] == "region" and lawnReport["target"]["points"] == laneViews.regionSamples ** 2
  assert lawnReport["sides"] == {"north": 1.0, "east": 1.0, "south": 1.0, "west": 1.0, "top": None, "underside": None}
  assert lawnReport["unseenSectors"] == [] and lawnReport["eyes"][0]["seenShare"] == 1.0 and markedPixels(lawnSheet, 0) > 200
  pointSheet, pointReport = sheetOf(calls["point"], tmp_path, "pointTarget.jpg")
  # A point is a player standing on the lane's floor there (the ground at 0, within a cell's height): seen from every side on open
  # ground, never from above, and its eye sees all of him that faces it; its view brackets him.
  low, high = pointReport["target"]["bounds"]["minimum"], pointReport["target"]["bounds"]["maximum"]
  assert low[:2] == high[:2] == [0.0, 50.0] and 0 <= low[2] <= cellHeight and high[2] == pytest.approx(low[2] + playerHeight, abs=0.011)
  assert pointReport["sides"] == {"north": 1.0, "east": 1.0, "south": 1.0, "west": 1.0, "top": 0.0, "underside": None}
  assert pointReport["eyes"][0]["seenShare"] == 1.0 and markedPixels(pointSheet, 0) > 50

  _, tourReport = sheetOf(calls["tour"], tmp_path, "shedTour.jpg")
  # The ground runs off into nothing all round: the tour's first eye stands well inside it, away from the world's end and the safe
  # point alike, never on the edge farthest from the safe point.
  lead = tourReport["eyes"][0]
  assert tourReport["worldEndPoints"] > 0 and 100 - max(abs(lead["standing"][0]), abs(lead["standing"][1])) >= 50, lead["standing"]


async def corridor(session, folder, wallHeight=12):
  """A corridor 220 long and 20 wide, walled wallHeight high, on grass."""
  await freshScene(session)
  await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [240, 40], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
  await session.expectSuccess("createMaterial", {"name": "grass", "diffuseTexture": str(writePNG(folder / "grass.png", 4, 4, (90, 120, 60, 255)))})
  await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
  await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 64, "direction": [0, 0, 1]})
  await session.expectSuccess("placeBoundaryWall", {"name": "corridor", "path": [[-110, -10], [110, -10], [110, 10], [-110, 10], [-110, -10]], "height": wallHeight})


def testTheTourIsReusedForAnUnchangedLane(stageBlenderServer, tmp_path):
  async def steps(session):
    await corridor(session, tmp_path)
    await session.expectSuccess("setZoneProperties", {"safePoint": [-100, 0, 0, 90]})
    unlit = await session.expectError("viewFromLane", {})
    await session.expectSuccess("setZoneProperties", environment)
    calls = {}
    calls["first"] = await session.expectImage("viewFromLane", {"count": 3}, "image/jpeg")
    calls["again"] = await session.expectImage("viewFromLane", {}, "image/jpeg")
    await session.expectSuccess("setZoneProperties", {"fogEnd": 60})
    calls["foggy"] = await session.expectImage("viewFromLane", {"count": 1}, "image/jpeg")
    await cube(session, "crate", [0, -4, 0], [8, 4, 8])
    calls["changed"] = await session.expectImage("viewFromLane", {"count": 1}, "image/jpeg")
    calls["crate"] = await session.expectImage("viewFromLane", {"target": "crate", "count": 1}, "image/jpeg")
    calls["ground"] = await session.expectImage("viewFromLane", {"target": "ground", "count": 1}, "image/jpeg")
    await cube(session, "floater", [298, -2, 50], [302, 2, 52])
    calls["floaterFogged"] = await session.expectImage("viewFromLane", {"target": "floater", "count": 1}, "image/jpeg")
    await session.expectSuccess("setZoneProperties", {"fogOn": False})
    calls["floaterClear"] = await session.expectImage("viewFromLane", {"target": "floater", "count": 1}, "image/jpeg")
    refusals = {
      "tooMany": await session.expectError("viewFromLane", {"count": laneViews.tourEyes + 1}),
      "none": await session.expectError("viewFromLane", {"count": 0}),
      "tooManyForATarget": await session.expectError("viewFromLane", {"target": "crate", "count": laneViews.targetEyes + 1}),
      "unknown": await session.expectError("viewFromLane", {"target": "nothing"}),
      "offTheGrid": await session.expectError("viewFromLane", {"target": [5000, 0]}),
    }
    return unlit, calls, refusals

  unlit, calls, refusals = stageBlenderServer.session(steps)
  # A zone without the properties a view needs is refused by name before its lane is mapped.
  assert "Zone properties missing" in unlit and "'fogOn'" in unlit and "'maxClip'" in unlit
  (tmp_path / "corridorTour.jpg").write_bytes(calls["first"][0])
  first, again, foggy, changed = (calls[name][1] for name in ("first", "again", "foggy", "changed"))
  # The first eye stands near the corridor's far end from the safe point, on its middle line, on the ground (at 0, not the top of the
  # lane's column over it), facing back down its length, the longest reached ground.
  lead = first["eyes"][0]
  assert lead["standing"][0] > 110 - 2 * laneViews.eyeBlock and abs(lead["standing"][1]) <= 4 and lead["standing"][2] == pytest.approx(0.0, abs=0.01)
  assert (lead["label"], lead["swimming"], lead["eye"][2], lead["headingDegrees"], lead["direction"]) == ("walk", False, eyeHeight, 270, "south")
  assert lead["run"] >= lead["standing"][0] + 110 - 2
  # The same lane gives the same tour, kept beside it and read again whatever the count; the fog's end shortening the far sight chooses
  # it again on the same lane, and changing the zone makes a new lane and a new tour.
  tourFile = Path(first["tourFile"])
  laneFile = Path(first["laneFile"])
  assert tourFile.parent == laneFile.parent and tourFile.name == "tour-" + laneFile.stem.removeprefix("lane-") + ".json"
  assert (first["lane"], first["tour"], again["lane"], again["tour"]) == ("computed", "computed", "reused", "reused")
  assert again["tourFile"] == first["tourFile"] and len(again["eyes"]) == laneViews.tourEyes

  def kept(eyes):
    return [{key: eye[key] for key in eye if key != "outputPath"} for eye in eyes]

  assert kept(again["eyes"][:3]) == kept(first["eyes"])
  assert (foggy["lane"], foggy["tour"], foggy["farSight"]) == ("reused", "computed", 60.0) and foggy["tourFile"] == first["tourFile"]
  assert foggy["eyes"][0]["run"] <= 60
  assert (changed["lane"], changed["tour"]) == ("computed", "computed") and changed["tourFile"] != first["tourFile"]
  assert json.loads(Path(changed["tourFile"]).read_text())["eyes"][0]["view"] == changed["eyes"][0]["view"]
  # The crate stands in the corridor's middle, the floor beside each of its sides narrower than a tour's block: players see every side.
  crate = calls["crate"][1]
  assert crate["sides"] == {"north": 1.0, "east": 1.0, "south": 1.0, "west": 1.0, "top": 0.0, "underside": 0.0} and crate["unseenSectors"] == []
  # The ground players stand on is judged by points on it, from the eyes on it: seen inside the walls from every bearing, never outside
  # them; it has only a top.
  ground = calls["ground"][1]
  assert ground["eyesTried"] > 0 and ground["unseenSectors"] == [] and 0 < ground["sides"]["top"] < 1
  assert [ground["sides"][name] for name in ("north", "east", "south", "west", "underside")] == [None] * 5
  # The floater hangs far past the corridor's end: within the fog's end no reached floor lies on any bearing, so no eye is tried; with
  # the fog off, players see it over the end wall.
  fogged, clear = calls["floaterFogged"][1], calls["floaterClear"][1]
  assert fogged["unseenSectors"] == [{"fromDegrees": 0.0, "toDegrees": 360.0, "why": "noReachedFloor"}] and fogged["eyesTried"] == 0 and fogged["eyes"] == []
  assert clear["farSight"] == environment["maxClip"] and clear["eyesSeeing"] > 0 and len(clear["eyes"]) == 1
  assert "count is a whole number of eyes from 1 to 8 for a tour, got 9" in refusals["tooMany"]
  assert "got 0" in refusals["none"]
  assert "from 1 to 6 for a target, got 7" in refusals["tooManyForATarget"]
  assert "The target 'nothing' is neither a region ([]) nor an object" in refusals["unknown"]
  assert "The target point [5000.0, 0.0] lies off the lane's grid" in refusals["offTheGrid"]


def testTourEyesStandWherePlayersStand(stageBlenderServer, tmp_path):
  """The corridor's far end changed in turn: deep water, the same under a lid, shallow water, a lintel only a smaller player passes
  under, and a block higher than a player's reach over the eye, climbed by a ramp and dropped off; walled higher than the block, so no
  one climbs out."""
  async def steps(session):
    await corridor(session, tmp_path, 20)
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [-100, 0, 0, 90]})
    leads = {}

    async def lead(name):
      leads[name] = (await session.expectImage("viewFromLane", {"count": 1}, "image/jpeg"))[1]["eyes"][0]

    await session.expectSuccess("placeSwimVolume", {"name": "pool", "liquid": "water", "minimum": [70, -10, 0], "maximum": [110, 10, 10]})
    await lead("deep")
    await cube(session, "lid", [70, -10, 10.5], [110, 10, 11.5])
    await lead("lidded")
    await session.expectSuccess("deleteObjects", {"names": ["lid", "AWT_pool"]})
    await session.expectSuccess("placeSwimVolume", {"name": "shallows", "liquid": "water", "minimum": [70, -10, 0], "maximum": [110, 10, 3]})
    await lead("shallow")
    await session.expectSuccess("deleteObjects", {"names": ["AWT_shallows"]})
    await cube(session, "lintel", [70, -10, 3], [110, 10, 12])
    await lead("squeeze")
    await session.expectSuccess("deleteObjects", {"names": ["lintel"]})
    await cube(session, "block", [60, -10, 0], [70, 10, 14])
    rise, degrees = 14.0, 35.0
    run = rise / math.tan(math.radians(degrees))
    await session.expectSuccess("createPrimitive", {
      "kind": "plane", "name": "ramp", "size": [rise / math.sin(math.radians(degrees)), 20, 0], "location": [60 - run / 2, 0, rise / 2],
      "rotationDegrees": [0, -degrees, 0],
    })
    await lead("rise")
    return leads

  leads = stageBlenderServer.session(steps)
  # Deep water at the far end: the farthest eye swims there, swimEyeAboveSurface over the box's top.
  deep = leads["deep"]
  assert (deep["label"], deep["swimming"]) == ("swim", True) and deep["standing"][0] > 70
  assert (deep["standing"][2], deep["eye"][2]) == (10.0, 10.0 + swimEyeAboveSurface)
  # Under a lid the swimmer's eye would lie in it: no eye stands in that water, so the farthest stands on the floor before it.
  assert (leads["lidded"]["label"], leads["lidded"]["swimming"]) == ("walk", False) and leads["lidded"]["standing"][0] < 70
  # Water shallower than eyeHeight - swimEyeAboveSurface is waded: the eye stands eyeHeight over the ground under it.
  shallow = leads["shallow"]
  assert (shallow["label"], shallow["swimming"]) == ("swim", False) and shallow["standing"][0] > 70
  assert shallow["standing"][2] == pytest.approx(0.0, abs=0.01) and shallow["eye"][2] == pytest.approx(eyeHeight, abs=0.01)
  # Under the lintel only a smaller player fits: no eye stands there.
  assert leads["squeeze"]["label"] == "walk" and leads["squeeze"]["standing"][0] < 70
  # The block's top, reached by the ramp, lies more than playerHeight over the eye beyond it: the run back down the corridor ends at it.
  beyond = leads["rise"]
  assert beyond["standing"][0] > 70 and beyond["headingDegrees"] == 270 and beyond["run"] <= beyond["standing"][0] - 70 + 1
