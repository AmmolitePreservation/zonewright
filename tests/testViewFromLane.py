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
from playerScale import eyeHeight, playerHeight, swimEyeAboveSurface
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
  """The lane node an eye stands on: the floor at its standing point, or the reached water whose top is the swim box's it floats on."""
  if eye["label"] == "walk":
    return nodes.floorNodeAt(eye["standing"], tolerance=0.01)
  column = nodes.lane.columnAt(eye["standing"])
  inColumn = numpy.arange(nodes.columnStart[column], nodes.columnStart[column + 1])
  water = inColumn[nodes.wet[inColumn] & (numpy.abs(nodes.lane.height(nodes.high[inColumn]) - eye["standing"][2]) <= cellHeight)]
  return int(water[0]) if len(water) else None


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
  # Eight views and the plan, three to a row.
  assert sheet.width == viewSheets.gap + 3 * (viewSheets.cellSize[0] + viewSheets.gap)
  assert {name: report["constants"][name]["value"] for name in ("eyeHeight", "swimEyeAboveSurface")} == {"eyeHeight": eyeHeight, "swimEyeAboveSurface": swimEyeAboveSurface}
  assert report["farSight"] == min(environment["maxClip"], environment["fogEnd"])
  assert [arrival["name"] for arrival in report["arrivals"]] == ["safe point", "hermit", "stranded"]
  arrivals = numpy.array([arrival["at"] for arrival in report["arrivals"]])
  nodes = playerReach.Nodes(playerReach.Lane(report["laneFile"]))
  # Every eye stands on a node the lane reaches, labelled as the eye says, its eye a player's eye over the floor or over the water.
  for eye in eyes:
    node = standingNode(nodes, eye)
    assert node is not None and bool(nodes.reached[node]) and playerReach.labelNames[nodes.label[node]] == eye["label"]
    lift = eyeHeight if eye["label"] == "walk" else swimEyeAboveSurface
    assert eye["eye"] == pytest.approx([*eye["standing"][:2], eye["standing"][2] + lift], abs=0.011)
    assert eye["headingDegrees"] % laneViews.sectorDegrees == 0 and eye["run"] > 0
    assert eye["view"]["eye"] == eye["eye"] and eye["view"]["target"][2] == eye["eye"][2]
  # Each eye is as far as it lies from the arrivals and the eyes before it (its spacing), so the spacings never grow; the first is the
  # farthest from every arrival of all the floor the lane reaches, to within a block.
  standing = numpy.array([eye["standing"] for eye in eyes])
  for index, eye in enumerate(eyes):
    before = numpy.concatenate([arrivals, standing[:index]])
    assert eye["spacing"] == pytest.approx(float(numpy.linalg.norm(before - standing[index], axis=1).min()), abs=0.02)
  spacings = [eye["spacing"] for eye in eyes]
  assert spacings == sorted(spacings, reverse=True)
  walked = numpy.flatnonzero(nodes.label == playerReach.walkLabel)
  planX, planY = nodes.plan(walked)
  places = numpy.stack([planX, planY, nodes.height[walked]], axis=1)
  farthest = float(numpy.linalg.norm(places[:, None, :] - arrivals[None, :, :], axis=2).min(axis=1).max())
  assert farthest - laneViews.eyeBlock * math.sqrt(3) <= spacings[0] <= farthest + 0.01

  plotSheet, plotReport = sheetOf(plot, tmp_path, "plotTarget.jpg")
  target = plotReport["target"]
  assert (target["kind"], target["name"]) == ("object", "1 Open Street")
  # The flat plot is judged as tall as a player standing on it.
  assert target["bounds"]["maximum"][2] - target["bounds"]["minimum"][2] == pytest.approx(playerHeight, abs=0.011)
  assert 1 <= len(plotReport["eyes"]) <= laneViews.targetEyes and all(eye["seenShare"] > 0 for eye in plotReport["eyes"])
  # Each eye looks at the middle of what it sees of the plot, inside the plot's bounds.
  bearings = [eye["bearingDegrees"] for eye in plotReport["eyes"]]
  assert bearings == sorted(bearings)
  low, high = numpy.array(target["bounds"]["minimum"]), numpy.array(target["bounds"]["maximum"])
  assert all(((low - 0.01 <= eye["view"]["target"]) & (eye["view"]["target"] <= high + 0.01)).all() for eye in plotReport["eyes"])
  assert "tour" not in plotReport and set(plotReport["sides"]) == set(laneViews.sideNames)


def centerPatch(path):
  """The mean color of the middle of a render, where a view looking at a target's middle draws it."""
  image = numpy.asarray(viewSheets.openRender(path)).astype(float)
  height, width = image.shape[:2]
  return image[height // 2 - 3:height // 2 + 4, width // 2 - 3:width // 2 + 4].reshape(-1, 3).mean(axis=0)


async def screenedShed(session, folder):
  """Open ground with the safe point west of a shed whose whole east side a tall screen covers, past the step and out past its ends."""
  await freshScene(session)
  await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [200, 200], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
  for name, color in (("grass", (90, 120, 60, 255)), ("brick", (190, 30, 25, 255)), ("stone", (120, 120, 125, 255))):
    await session.expectSuccess("createMaterial", {"name": name, "diffuseTexture": str(writePNG(folder / f"{name}.png", 4, 4, color))})
  await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
  await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 64, "direction": [0, 0, 1]})
  await session.expectSuccess("setZoneProperties", environment | {"safePoint": [0, 60, 0, 0]})
  await cube(session, "shed", [-5, -5, 0], [5, 5, 8])
  await cube(session, "screen", [-40, -11, 0], [40, -5, 20])
  for name, material in (("shed", "brick"), ("screen", "stone")):
    await session.expectSuccess("assignMaterial", {"objectName": name, "materialName": material})
    await session.expectSuccess("projectUVs", {"objectName": name, "method": "box", "worldUnitsPerRepeat": 8})
  await session.expectSuccess("createRegion", {"name": "lawn", "outline": [[-30, 20], [30, 20], [30, 80], [-30, 80]], "bottom": -10, "top": 30, "intent": "open lawn west of the shed", "access": "play"})


def testViewFromLaneReportsTheUnseenSide(stageBlenderServer, tmp_path):
  async def steps(session):
    await screenedShed(session, tmp_path)
    shed, _ = await session.call("viewFromLane", {"target": "shed"})
    lawn, _ = await session.call("viewFromLane", {"target": "lawn", "count": 1})
    point, _ = await session.call("viewFromLane", {"target": [0, 50], "count": 1})
    return shed, lawn, point

  shed, lawn, point = stageBlenderServer.session(steps)
  _, report = sheetOf(shed, tmp_path, "shedTarget.jpg")
  # The screen covers the shed's east (-Y) side from every eye, and no eye stands high enough to see its roof; its other sides are seen
  # whole.
  assert report["sides"] == {"north": 1.0, "east": 0.0, "south": 1.0, "west": 1.0, "top": 0.0}
  # From the east no player sees it: the bearings round 180 (toward -Y) are blocked, though players stand there; the west's are seen.
  unseen = report["unseenSectors"]
  assert len(unseen) == 1 and unseen[0]["why"] == "blocked" and unseen[0]["fromDegrees"] < 180 < unseen[0]["toDegrees"]
  assert not unseen[0]["fromDegrees"] < 0 < unseen[0]["toDegrees"]
  eyes = report["eyes"]
  assert len(eyes) == laneViews.targetEyes and all(eye["seenShare"] > 0 for eye in eyes)
  assert not any(unseen[0]["fromDegrees"] <= eye["bearingDegrees"] <= unseen[0]["toDegrees"] for eye in eyes)
  sectors = [round(eye["bearingDegrees"] / laneViews.sectorDegrees) % laneViews.bearings for eye in eyes]
  assert len(set(sectors)) == len(sectors)
  # Each view looks at the shed's middle and draws the shed's brick there.
  for eye in eyes:
    red, green, blue = centerPatch(eye["outputPath"])
    assert red > green + 40 and red > blue + 40, (eye["number"], red, green, blue)

  _, lawnReport = sheetOf(lawn, tmp_path, "lawnTarget.jpg")
  # The lawn's floor lies open round the safe point: every part of it is seen.
  assert lawnReport["target"]["kind"] == "region" and lawnReport["target"]["points"] == laneViews.regionSamples ** 2
  assert lawnReport["sides"] == {"north": 1.0, "east": 1.0, "south": 1.0, "west": 1.0, "top": None} and lawnReport["unseenSectors"] == []
  _, pointReport = sheetOf(point, tmp_path, "pointTarget.jpg")
  # A point is a player standing on the lane's floor there (the ground at 0, within a cell's height): seen from every side on open
  # ground, but never from above.
  low, high = pointReport["target"]["bounds"]["minimum"], pointReport["target"]["bounds"]["maximum"]
  assert low[:2] == high[:2] == [0.0, 50.0] and 0 <= low[2] <= cellHeight and high[2] == pytest.approx(low[2] + playerHeight, abs=0.011)
  assert pointReport["sides"] == {"north": 1.0, "east": 1.0, "south": 1.0, "west": 1.0, "top": 0.0}


def testTheTourIsReusedForAnUnchangedLane(stageBlenderServer, tmp_path):
  async def steps(session):
    await freshScene(session)
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [240, 40], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
    await session.expectSuccess("placeBoundaryWall", {"name": "corridor", "path": [[-110, -10], [110, -10], [110, 10], [-110, 10], [-110, -10]], "height": 12})
    await session.expectSuccess("setZoneProperties", environment | {"safePoint": [-100, 0, 0, 90]})
    calls = {}
    calls["first"] = await session.expectImage("viewFromLane", {"count": 3}, "image/jpeg")
    calls["again"] = await session.expectImage("viewFromLane", {}, "image/jpeg")
    await session.expectSuccess("setZoneProperties", {"fogEnd": 60})
    calls["foggy"] = await session.expectImage("viewFromLane", {"count": 1}, "image/jpeg")
    await cube(session, "crate", [0, -4, 0], [8, 4, 8])
    calls["changed"] = await session.expectImage("viewFromLane", {"count": 1}, "image/jpeg")
    refusals = {
      "tooMany": await session.expectError("viewFromLane", {"count": laneViews.tourEyes + 1}),
      "none": await session.expectError("viewFromLane", {"count": 0}),
      "tooManyForATarget": await session.expectError("viewFromLane", {"target": "crate", "count": laneViews.targetEyes + 1}),
      "unknown": await session.expectError("viewFromLane", {"target": "nothing"}),
      "offTheGrid": await session.expectError("viewFromLane", {"target": [5000, 0]}),
    }
    return calls, refusals

  calls, refusals = stageBlenderServer.session(steps)
  (tmp_path / "corridorTour.jpg").write_bytes(calls["first"][0])
  first, again, foggy, changed = (calls[name][1] for name in ("first", "again", "foggy", "changed"))
  # The first eye stands at the corridor's far end from the safe point, facing back down its length, the longest reached ground.
  lead = first["eyes"][0]
  assert lead["standing"][0] > 110 - laneViews.eyeBlock and lead["headingDegrees"] == 270 and lead["run"] >= lead["standing"][0] + 110 - 2
  # The same lane gives the same tour, kept beside it and read again whatever the count; the fog's end shortening the far sight chooses
  # it again on the same lane, and changing the zone makes a new lane and a new tour.
  tourFile = Path(first["tourFile"])
  assert tourFile.parent == Path(first["laneFile"]).parent and tourFile.name == "tour-" + Path(first["laneFile"]).name.removeprefix("lane-").removesuffix(".bin") + ".json"
  assert (first["lane"], first["tour"], again["lane"], again["tour"]) == ("computed", "computed", "reused", "reused")
  assert again["tourFile"] == first["tourFile"] and len(again["eyes"]) == laneViews.tourEyes
  assert [{key: eye[key] for key in eye if key != "outputPath"} for eye in again["eyes"][:3]] == [{key: eye[key] for key in eye if key != "outputPath"} for eye in first["eyes"]]
  assert (foggy["lane"], foggy["tour"], foggy["farSight"]) == ("reused", "computed", 60.0) and foggy["tourFile"] == first["tourFile"]
  assert foggy["eyes"][0]["run"] <= 60
  assert (changed["lane"], changed["tour"]) == ("computed", "computed") and changed["tourFile"] != first["tourFile"]
  assert json.loads(Path(changed["tourFile"]).read_text())["eyes"][0]["view"] == changed["eyes"][0]["view"]
  assert "count is a whole number of eyes from 1 to 8 for a tour, got 9" in refusals["tooMany"]
  assert "got 0" in refusals["none"]
  assert "from 1 to 6 for a target, got 7" in refusals["tooManyForATarget"]
  assert "The target 'nothing' is neither a region ([]) nor an object" in refusals["unknown"]
  assert "The target point [5000.0, 0.0] lies off the lane's grid" in refusals["offTheGrid"]
