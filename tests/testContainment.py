import json
import math
import sys
from pathlib import Path

import numpy
from PIL import Image

from conftest import serverRowValues, writePNG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqAxes
import exportPipeline
import planDrawing
import playerReach
import serverMapDrawing
import serverMapFiles
import serverNav
import serverRows
from playerScale import steepestWalkableDegrees, stepHeight
from testHousing import decision
from testModelsAndDressing import freshScene
from testReach import centersWithin, columnsWithin, cube, near, rampAlongY, rectangle, region
from testWater import liquidMaterials

cellSize = playerReach.reachGrid["cellSize"]
half = 80.0
# The rim's walls stand just inside the ground's edge, in its last column, as the reach course's do.
edge = half - 0.1
wallHeight = 12.0
environment = {
  "ambientColor": [0.3, 0.3, 0.35], "specialAmbientColor": [0, 0, 0], "bounceColor": [0.1, 0.1, 0.15], "sunColor": [0.6, 0.5, 0.4],
  "sunAzimuthDegrees": 40, "sunElevationDegrees": 35, "fogColor": [0.5, 0.55, 0.6], "fogStart": 100, "fogEnd": 3000, "fogDensity": 0.1,
  "fogOn": False, "minClip": 50, "maxClip": 4000,
  "sky": "none", "newEngineZone": False,
}
underworld = -50.0
otherZone = {"zone": "qeynos2", "x": 10, "y": 20, "z": 3, "headingDegrees": 90}
# Compass names as plans draw them: +X north, +Y west.
northLine = {"minimum": [60, -10, -5], "maximum": [76, 10, 20]}
rims = {"westWall": [[-edge, edge], [edge, edge]], "northWall": [[edge, edge], [edge, -edge]], "eastWall": [[edge, -edge], [-edge, -edge]], "southWall": [[-edge, -edge], [-edge, edge]]}
# A platform players walk up to by a ramp steeper than the NPC nav walks and over its climb, and a dais no one climbs.
rampDegrees = steepestWalkableDegrees - 5
platform = ([32, 32, 0], [48, 48, 10])
dais = ([-48, 32, 0], [-32, 48, 2 * stepHeight])
fromQeynos = {"name": "fromQeynos", "at": [-40, -40], "headingDegrees": 90, "kind": "zoneIn", "fromZone": "qeynos2", "fromNumber": 3}
withinCell = cellSize * math.sqrt(2)


def noProgress(done, of, message):
  pass


async def stone(session, names):
  for name in names:
    await session.expectSuccess("assignMaterial", {"objectName": name, "materialName": "stone"})
    await session.expectSuccess("projectUVs", {"objectName": name, "method": "box", "worldUnitsPerRepeat": 8})


async def containedZone(session, folder, short, seeds=True):
  """Flat ground 160 across walled all round, a zone line inside its north wall, a platform players reach by a ramp too steep for the
  NPC nav and a dais no one climbs, one play region over it all, and the zone row's values; with seeds, the safe point at its middle
  and a zoneIn entry naming its neighbour's row. Saved as <short>.blend."""
  await freshScene(session)
  await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [2 * half, 2 * half], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
  await session.expectSuccess("createMaterial", {"name": "grass", "diffuseTexture": str(writePNG(folder / "grass.png", 4, 4, (90, 120, 60, 255)))})
  await session.expectSuccess("createMaterial", {"name": "stone", "diffuseTexture": str(writePNG(folder / "stone.png", 4, 4, (120, 110, 100, 255)))})
  await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
  await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 64, "direction": [0, 0, 1]})
  for name, path in rims.items():
    await session.expectSuccess("placeBoundaryWall", {"name": name, "path": path, "height": wallHeight})
  await cube(session, "platform", *platform)
  await rampAlongY(session, "ramp", 40, platform[0][1] - platform[1][2] / math.tan(math.radians(rampDegrees)), platform[1][2], rampDegrees)
  await cube(session, "dais", *dais)
  await stone(session, ["platform", "ramp", "dais"])
  await session.expectSuccess("setZoneProperties", environment | serverRowValues(short) | {"underworld": underworld} | ({"safePoint": [0, 0, 0, 0]} if seeds else {}))
  await session.expectSuccess("placeZoneLine", {"number": 1, "label": "north", "target": otherZone} | northLine)
  await region(session, "yard", rectangle([-half, -half], [half, half]), "play", bottom=-20, top=40)
  if seeds:
    await session.expectImage("placeEntry", fromQeynos)
  await session.expectSuccess("saveFile", {"path": str(folder / f"{short}.blend")})


def recordFile(server, short):
  return exportPipeline.recordPath(server.toolingRoot, short)


def decided(row):
  return {column: row[column] for column in ("number", *serverRows.ownershipFloatColumns, "target_zone_id")}


def islandsOn(islands, box):
  """The reach flags of the islands whose centers lie over a box's footprint, by the height of each center."""
  low, high = box
  return sorted((island["center"][2], island["playersReach"]) for island in islands if low[0] < island["center"][0] < high[0] and low[1] < island["center"][1] < high[1])


def testAContainedZoneExportsForTheGame(stageBlenderServer, tmp_path):
  short = "contained"
  first, second = tmp_path / "first", tmp_path / "second"
  first.mkdir()
  second.mkdir()
  record = recordFile(stageBlenderServer, short)

  async def steps(session):
    await containedZone(session, tmp_path, short)
    exported = await session.expectSuccess("exportZone", {"path": str(first / f"{short}.eqg"), "purpose": "game"})
    afterFirst = record.read_bytes()
    # Moved to another number, the line is checked: the check writes its SQL in the check folder and leaves the record alone.
    await session.expectSuccess("deleteObjects", {"names": ["ATP_1_north"]})
    await session.expectSuccess("placeZoneLine", {"number": 2, "label": "west", "minimum": [-10, 60, -5], "maximum": [10, 76, 20], "target": otherZone})
    await session.expectSuccess("saveFile", {})
    checked = await session.expectSuccess("checkExport", {"path": str(second / f"{short}.eqg"), "purpose": "game"})
    afterCheck = record.read_bytes()
    await session.expectSuccess("deleteObjects", {"names": ["ATP_2_west"]})
    await session.expectSuccess("saveFile", {})
    again = await session.expectSuccess("exportZone", {"path": str(second / f"{short}.eqg"), "purpose": "game"})
    return exported, afterFirst, checked, afterCheck, again, record.read_bytes()

  exported, afterFirst, checked, afterCheck, again, afterSecond = stageBlenderServer.session(steps)
  assert exported["failures"] == [], exported["failures"]
  sqlFolder = first / "server" / "sql"
  assert sorted(path.relative_to(first).as_posix() for path in first.rglob("*") if path.is_file()) == [
    "contained.eqg", "contained_export.json", "server/maps/base/contained.map", "server/maps/nav/contained.nav", "server/maps/water/contained.wtr",
    "server/sql/contained.sql", "server/sql/contained_waysIn.sql",
  ]
  # The lane found no leak: every entry connected, and its key the one mapReach computes (computed here, as nothing reused it yet).
  lane = exported["containment"]
  assert lane["lane"] == "computed" and lane["reachGrid"] == playerReach.reachGrid and Path(lane["laneFile"]).name == f"lane-{lane['laneKey']}.bin"
  assert [(entry["name"], entry["connected"]) for entry in lane["entries"]] == [("safe point", True), ("fromQeynos", True)]
  # NPC paths are probed from the safe point to the zone line and to the stored entry.
  assert [(probe["name"], probe["point"], probe["reached"]) for probe in exported["serverFiles"]["nav"]["probes"]] == [
    ("ATP_1_north", [(low + high) / 2 for low, high in zip(northLine["minimum"], northLine["maximum"])], True), ("entry fromQeynos", [-40.0, -40.0, 0.0], True),
  ]
  assert lane["zoneLines"] == [{"name": "ATP_1_north", "reached": True}]
  assert {kind for kind in lane["problems"]} <= {"unreachedPlayFloor", "trap"} and "voidEdge" not in lane["problems"]
  # Its rows: the zone row and one zone_points row at the line's box's center in the server's axes, numbered ten times its number.
  rows = serverRows.readRows((sqlFolder / "contained.sql").read_text(encoding="ascii"))
  center = [(low + high) / 2 for low, high in zip(northLine["minimum"], northLine["maximum"])]
  assert (rows["numbersWritten"], rows["numbersDeleted"], rows["zonePointsDeleted"]) == ([10], [], [])
  point = rows["zonePoints"][0]
  assert [(point["number"], point["x"], point["y"], point["z"], point["target_zone_id"])] == [(10, center[1], center[0], center[2], 2)]
  assert (point["target_x"], point["target_y"], point["target_z"]) == (otherZone["y"], otherZone["x"], otherZone["z"])
  assert rows["zoneInsert"]["written"]["short_name"] == short and rows["zoneInsert"]["written"]["safe_z"] == 0
  waysIn = serverRows.readWaysIn((sqlFolder / "contained_waysIn.sql").read_text(encoding="ascii"))
  assert [(row["fromZone"], row["fromNumber"], row["target_zone_id"]) for row in waysIn["rows"]] == [("qeynos2", 3, 901)]
  # The record keeps the whole rows the SQL inserts, through JSON.
  assert json.loads(afterFirst) == {"shortName": short, "zonePoints": rows["zonePoints"]}
  assert exported["zonePointsRecord"] == {"path": str(recordFile(stageBlenderServer, short)), "rows": 1}
  # The manifest: every file with its hash, the zone's identity, the zone_points written and deleted, and the lane.
  manifest = json.loads((first / "contained_export.json").read_text(encoding="ascii"))
  assert manifest["files"]["server/sql/contained.sql"]["bytes"] == (sqlFolder / "contained.sql").stat().st_size
  assert sorted(manifest["files"]) == sorted(path.relative_to(first).as_posix() for path in first.rglob("*") if path.is_file() and path.name != "contained_export.json")
  assert manifest["zone"] == {"shortName": short, "zoneId": 901, "longName": "The contained test", "serverTemplate": "qeynos2"}
  assert manifest["zonePoints"] == {"written": [10], "deleted": []}
  assert manifest["lane"] == {key: lane[key] for key in ("laneKey", "reachGrid", "constants", "reachedArea")}
  assert manifest["lane"]["constants"]["stepHeight"]["value"] == stepHeight
  assert (manifest["failures"], manifest["findings"]) == (0, len(exported["findings"]))
  # The check of the moved line deletes the recorded row 10 and writes 20, in its own folder, and the record stays as the first export
  # left it.
  checkRows = serverRows.readRows((stageBlenderServer.toolingRoot / "exports" / short / "check" / "server" / "sql" / "contained.sql").read_text(encoding="ascii"))
  assert (checkRows["numbersWritten"], checkRows["numbersDeleted"]) == ([20], [10]) and checked["failures"] == []
  assert afterCheck == afterFirst
  # The second export, to a folder the first never wrote, with no zone line left: the record, not the folder, carries row 10, which its
  # SQL deletes where the table still holds it as the first export wrote it.
  assert again["failures"] == [] and "no zone lines" in [finding["finding"] for finding in again["findings"]]
  secondRows = serverRows.readRows((second / "server" / "sql" / "contained.sql").read_text(encoding="ascii"))
  assert (secondRows["numbersWritten"], secondRows["numbersDeleted"], secondRows["zonePoints"]) == ([], [10], [])
  assert secondRows["zonePointsDeleted"] == [{"zone": short} | decided(point)]
  assert afterSecond == afterFirst

  # The server's view of the first export from its files alone, the islands players reach orange and the others grey.
  mapContent = serverMapFiles.readMap((first / "server" / "maps" / "base" / "contained.map").read_bytes())
  records = serverMapFiles.readWater((first / "server" / "maps" / "water" / "contained.wtr").read_bytes())
  inspection = serverNav.inspectNav((first / "server" / "maps" / "nav" / "contained.nav").read_bytes(), [0, 0, 0], [], stageBlenderServer.toolingRoot, noProgress)
  reach = [island["playersReach"] for island in manifest["nav"]["islands"]]
  assert [island["center"] for island in inspection["islands"]] == [island["center"] for island in manifest["nav"]["islands"]]
  markers = [{"at": [0, 0], "label": "safe point"}, {"at": fromQeynos["at"], "label": "zoneIn fromQeynos"}, {"at": eqAxes.zoneFromServer([point["x"], point["y"], point["z"]])[:2].tolist(), "label": "zone_points 10"}]
  picture = tmp_path / "containedServerView.png"
  drawn = serverMapDrawing.drawServerPlan(picture, mapContent, records, ["ATP_1_north"], inspection, markers, islandReach=reach)
  frame = planDrawing.PlanFrame(drawn["frame"]["center"], drawn["frame"]["width"], drawn["frame"]["size"])
  pixels = numpy.asarray(Image.open(picture).convert("RGB"), dtype=numpy.int64)

  def pixelAt(point):
    x, y = frame.pixel(point)
    return pixels[int(y), int(x)]
  # The platform's top, which players reach up the ramp, is an island drawn orange; the dais's, which no one climbs, grey.
  top, daisTop = pixelAt((44, 36)), pixelAt((-44, 36))
  assert top[0] > top[1] > top[2], top
  assert abs(daisTop[0] - daisTop[1]) <= 12 and abs(daisTop[1] - daisTop[2]) <= 12, daisTop
  assert f"{sum(reach)} NPC islands players reach" in drawn["legend"]


def testALeakRefusesTheGameExportAndSaysWhere(stageBlenderServer, tmp_path):
  short = "leaky"
  archive = {"path": str(tmp_path / f"{short}.eqg")}

  async def steps(session):
    await containedZone(session, tmp_path, short)
    await session.expectSuccess("deleteObjects", {"names": ["southWall"]})
    await session.expectSuccess("saveFile", {})
    plan, opened = await session.expectImage("mapReach", {"problemViews": False})
    (tmp_path / "leakPlan.png").write_bytes(plan)
    openOnly = await session.expectSuccess("checkExport", archive | {"purpose": "game"})
    # A strip of the yard left out of every region, a none region players walk into, a landing on a tower they drop off and never climb
    # back, and a plot in a walled room.
    await session.expectSuccess("editRegion", {"name": "yard", "outline": rectangle([-half, -60], [half, half])})
    await region(session, "pond", rectangle([10, -60], [30, -40]), "none", bottom=-20, top=40)
    await cube(session, "tower", [-20, 10, 0], [-10, 20, 20])
    await stone(session, ["tower"])
    await session.expectImage("placeEntry", {"name": "rooftop", "at": [-15, 15], "headingDegrees": 0, "kind": "landing"})
    await session.expectSuccess("placeBoundaryWall", {"name": "roomWall", "path": [[-30, -30], [-10, -30], [-10, -10], [-30, -10], [-30, -30]], "height": wallHeight})
    await session.expectSuccess("setZoneHousing", decision)
    await session.expectSuccess("placePlot", {"address": "1 Walled Street", "center": [-20, -25], "facingDegrees": 0, "size": [8, 6]})
    await session.expectSuccess("saveFile", {})
    refused = await session.expectError("exportZone", archive | {"purpose": "game"})
    checked = await session.expectSuccess("checkExport", archive | {"purpose": "game"})
    return opened, openOnly, refused, checked

  opened, openOnly, refused, checked = stageBlenderServer.session(steps)
  # With the south wall gone, its edge is a leak the check refuses, numbered as mapReach numbers it on the lane mapReach left.
  edges = [problem for problem in opened["problems"] if problem["kind"] == "voidEdge"]
  assert [failure["failure"] for failure in openOnly["failures"]] == ["voidEdge"] and openOnly["containment"]["lane"] == "reused"
  leak = openOnly["failures"][0]
  assert len(edges) == 1 and (leak["number"], leak["center"], leak["length"]) == (edges[0]["number"], edges[0]["center"], edges[0]["length"])
  assert near(leak["center"], [-half - cellSize, 0]) and leak["length"] == columnsWithin(-edge, edge) * cellSize
  assert leak["message"].startswith(f"Players walk or slide off an edge onto nothing, along {leak['length']}")
  assert leak["message"].endswith(f"(mapReach problem {leak['number']}, at {leak['center']})")
  # Each of the five refuses the export, with its place and number.
  kinds = ["voidEdge", "noneRegion", "outsideRegions", "disconnectedEntry", "unreachedPlot"]
  assert [failure["failure"] for failure in checked["failures"]] == kinds
  found = {failure["failure"]: failure for failure in checked["failures"]}
  assert sorted(failure["number"] for failure in checked["failures"]) == list(range(1, 6))
  assert near(found["voidEdge"]["center"], [-half - cellSize, 0])
  assert found["noneRegion"]["region"] == "pond" and near(found["noneRegion"]["center"], [20, -50])
  assert found["noneRegion"]["area"] == centersWithin(10, 30) * centersWithin(-60, -40) * cellSize ** 2
  assert found["outsideRegions"]["region"] is None and near(found["outsideRegions"]["center"], [0, -70], 2 * cellSize)
  # The strip along the yard's east side, but the wall's own column and the row on the yard's outline, which the yard holds.
  assert found["outsideRegions"]["area"] == centersWithin(-half, edge) * (centersWithin(-edge, -60) - 1) * cellSize ** 2
  assert found["disconnectedEntry"]["entry"] == "rooftop" and near(found["disconnectedEntry"]["center"], [-15, 15])
  assert found["unreachedPlot"]["plot"] == "1 Walled Street" and all(-30 < value < -10 for value in found["unreachedPlot"]["center"][:2])
  # NPC paths are probed to the stored entries and the plot's entrance too. The walled room's floor is an island of the NPC nav, so
  # the path to the plot ends short of it. The tower's top, eroded by the NPC's radius, holds no nav, so the server takes the nearest
  # polygon its ground filter walks within (10, 200, 10) as the goal, on the ground at the tower's foot, and paths there.
  probes = {probe["name"]: probe for probe in checked["serverFiles"]["nav"]["probes"]}
  assert list(probes) == ["ATP_1_north", "entry fromQeynos", "entry rooftop", "plot 1 Walled Street"]
  assert [probes[name]["reached"] for name in probes] == [True, True, True, False]
  islands = checked["serverFiles"]["nav"]["largestIslands"]
  assert not [island for island in islands if -20 < island["center"][0] < -10 and 10 < island["center"][1] < 20]
  assert probes["entry rooftop"]["goalComponent"] == 0 and probes["plot 1 Walled Street"]["result"] == "partial"
  room = [island["number"] for island in islands if -30 < island["center"][0] < -10 and -30 < island["center"][1] < -10]
  assert len(room) == 1 and probes["plot 1 Walled Street"]["goalComponent"] == room[0]
  assert probes["plot 1 Walled Street"]["point"] == found["unreachedPlot"]["center"]
  unpathed = [finding["message"] for finding in checked["findings"] if finding["finding"] == "NPC nav"]
  assert unpathed == [f"NPCs cannot path from the safe point to plot 1 Walled Street within Peridot's 1,024 search nodes: it lies on island {room[0]}, apart from the main piece"]
  assert "exportZone (game) refused, nothing written: 5 failure(s)" in refused
  assert all(f'"failure": "{kind}"' in refused for kind in kinds) and not (tmp_path / f"{short}.eqg").exists()


def testSafePointOverNoMapGroundIsRefused(stageBlenderServer, tmp_path):
  short = "deeplake"
  check = {"path": str(tmp_path / f"{short}.eqg"), "purpose": "game"}
  bed = 2 * -underworld

  async def steps(session):
    await containedZone(session, tmp_path, short, seeds=False)
    # A lake whose bed sinks under the underworld, its water passable, its swimming decided.
    await liquidMaterials(session, tmp_path)
    await session.expectSuccess("sculptAlongPath", {"objectName": "ground", "mode": "carve", "path": [[0, 0, -bed]], "radius": 30, "strength": 1, "profile": [[0, 0], [1, bed]], "conformRim": False})
    await session.expectSuccess("floodWater", {"name": "lake", "seed": [0, 0], "level": -2, "material": "water"})
    await session.expectSuccess("buildSwimVolumes", {"body": "lake"})
    await session.expectSuccess("setZoneProperties", {"safePoint": [0, 0, 0, 0]})
    await session.expectSuccess("saveFile", {})
    overLake = await session.expectSuccess("checkExport", check)
    await session.expectSuccess("setZoneProperties", {"safePoint": [-60, 0, 0, 0]})
    await session.expectImage("placeEntry", fromQeynos)
    await session.expectSuccess("transformObjects", {"names": ["fromQeynos"], "translate": [40, 40, 0]})
    await session.expectSuccess("saveFile", {})
    entryOverLake = await session.expectSuccess("checkExport", check)
    return overLake, entryOverLake

  overLake, entryOverLake = stageBlenderServer.session(steps)

  def grounds(report):
    return [{key: value for key, value in failure.items() if key != "message"} for failure in report["failures"] if failure["failure"] == "no .map ground"]
  # Looking down from 1 over each, the server meets the lake's bed under the underworld: the water is passable, as the client lets
  # players through it.
  assert grounds(overLake) == [{"failure": "no .map ground", "arrival": "safe point", "kind": "safePoint", "at": [0, 0, 0], "ground": -bed}]
  message = [failure["message"] for failure in overLake["failures"] if failure["failure"] == "no .map ground"][0]
  assert message == (
    f"The safe point at [0.0, 0.0, 0.0] stands over no ground the server finds in the .map: looking down from 1 over it, as FindBestZ does, it"
    f" meets collision at {-bed:g}, under the underworld {underworld:g}; players arriving there fall"
  )
  assert grounds(entryOverLake) == [{"failure": "no .map ground", "arrival": "fromQeynos", "kind": "zoneIn", "at": [0.0, 0.0, 0.0], "ground": -bed}]
  # The scene's own checks say the same in their terms: the safe point over no ground the zone ships, the entry off its footing.
  assert "safe point over no ground" in [failure["failure"] for failure in overLake["failures"]]
  assert "entry off its footing" in [failure["failure"] for failure in entryOverLake["failures"]]


def testNoSeedsIsAFailureNotASkip(stageBlenderServer, tmp_path):
  short = "seedless"

  async def steps(session):
    await containedZone(session, tmp_path, short, seeds=False)
    return await session.expectSuccess("checkExport", {"path": str(tmp_path / f"{short}.eqg"), "purpose": "game"})

  checked = stageBlenderServer.session(steps)
  assert [failure["failure"] for failure in checked["failures"]] == ["safe point or underworld missing", "containment not checked"]
  message = checked["failures"][1]["message"]
  assert message.startswith("containment not checked: no seeds: the lane starts where players arrive") and checked["containment"] == message
  # Everything else is still built and shown: the server's files, the nav's islands, whose reach is said to be unchecked.
  server = checked["serverFiles"]
  assert server["built"] is True and sorted(server["files"]) == ["map", "nav", "water"]
  assert server["nav"]["islandCount"] == 4 and {island["playersReach"] for island in server["nav"]["largestIslands"]} == {None}
  islands = [finding for finding in checked["findings"] if finding["finding"] == "NPC islands"]
  assert len(islands) == 1 and "playersReach" not in islands[0] and "whether players reach them is not checked" in islands[0]["message"]
  assert server["rows"] == {"written": False, "message": "rows not written: the zone row lacks ['safePoint'] (setZoneProperties)"}


def testAnIslandIsAFindingNotAFailure(stageBlenderServer, tmp_path):
  short = "islands"
  # The ramp is walkable by players and too steep for the NPC nav, and the platform rises past the NPC's climb: an island players reach.
  settings = serverNav.serverNavSettings
  assert settings["maxSlope"] < rampDegrees and platform[1][2] > settings["agentClimb"] and dais[1][2] > max(settings["agentClimb"], stepHeight)

  async def steps(session):
    await containedZone(session, tmp_path, short)
    return await session.expectSuccess("exportZone", {"path": str(tmp_path / f"{short}.eqg"), "purpose": "game"})

  exported = stageBlenderServer.session(steps)
  assert exported["failures"] == [] and (tmp_path / f"{short}.eqg").is_file()
  islands = exported["serverFiles"]["nav"]["largestIslands"]
  # The platform's top is reached up the ramp; its hollow inside, as in any closed mesh, and the dais, top and inside, are not.
  onPlatform, onDais = islandsOn(islands, platform), islandsOn(islands, dais)
  assert [reached for _, reached in onPlatform] == [False, True] and [reached for _, reached in onDais] == [False, False]
  assert abs(onPlatform[1][0] - platform[1][2]) < 1 and abs(onDais[1][0] - dais[1][2]) < 1
  finding = [finding for finding in exported["findings"] if finding["finding"] == "NPC islands"]
  assert [(found["islands"], found["playersReach"]) for found in finding] == [(4, 1)]
  manifest = json.loads((tmp_path / f"{short}_export.json").read_text(encoding="ascii"))
  assert [island["playersReach"] for island in manifest["nav"]["islands"]] == [island["playersReach"] for island in islands]


def testATestExportComputesNoLane(stageBlenderServer, tmp_path):
  short = "testlane"
  folder = tmp_path / "export"
  folder.mkdir()
  lanes = playerReach.reachRoot(stageBlenderServer.toolingRoot)

  def laneFiles():
    return set(lanes.rglob("lane-*.bin"))

  async def steps(session):
    await containedZone(session, tmp_path, short)
    await session.expectSuccess("deleteObjects", {"names": ["southWall"]})
    await session.expectSuccess("saveFile", {})
    before = laneFiles()
    exported = await session.expectSuccess("exportZone", {"path": str(folder / f"{short}.eqg"), "purpose": "test"})
    checked = await session.expectSuccess("checkExport", {"path": str(folder / f"{short}.eqg"), "purpose": "test"})
    return before, exported, checked, laneFiles()

  before, exported, checked, after = stageBlenderServer.session(steps)
  # The open edge is no failure or finding of a test export, which computes no lane and writes no server files.
  assert exported["containment"] == checked["containment"] == "not checked for a test export"
  assert exported["failures"] == checked["failures"] == []
  assert not {"voidEdge", "containment not checked"} & {entry.get("finding", entry.get("failure")) for entry in exported["findings"] + checked["findings"]}
  assert after == before and not (folder / "server").exists()
