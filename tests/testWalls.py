import sys
from pathlib import Path

import numpy

from conftest import writePNG
import structurePlots

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import playerScale

# The medium body group, which holds the walked human male, is the one these tests size their courses by.
medium = playerScale.groups["medium"]
eyeHeight = medium.eyeHeight

turnPost = {"piece": "testKitPost", "at": "turns"}


async def kitAndPlot(session, folder):
  kitPath = await structurePlots.testKit(session, folder)
  await structurePlots.testPlot(session, folder)
  return str(kitPath)


def wallArguments(kitPath, name, path, **changes):
  return {"name": name, "kitPath": kitPath, "path": path, "frontSide": "left", "sections": ["testKitWall25", "testKitWall12"]} | changes


def testAShearedWallKeepsVerticalsVerticalFeetUnderTheGroundAndSharesAModelPerRise(stageBlenderServer, tmp_path):
  path = [[30, -45], [30, -107.5], [80, -107.5]]

  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    built = await session.expectSuccess("buildWall", wallArguments(kitPath, "slopeWall", path, posts=turnPost))
    names = [section["name"] for section in built["sections"]]
    laid = await structurePlots.parts(session, names)
    shears = (await session.expectSuccess("runPython", {"code": "import bpy\nresult = sorted(mesh.name for mesh in bpy.data.meshes if 'zonewrightShearOf' in mesh)"}))["result"]
    pieces = (await session.expectSuccess("runPython", {"code": "import bpy\nresult = {name: sorted({(round(vertex.co.x, 6), round(vertex.co.y, 6)) for vertex in bpy.data.collections[name].objects[0].data.vertices}) for name in ('testKitWall25', 'testKitWall12')}"}))["result"]
    return built, laid, shears, pieces

  built, laid, shears, pieces = stageBlenderServer.session(steps)
  sections = built["sections"]
  assert [leg["sections"] for leg in built["legs"]] == [["testKitWall25", "testKitWall25", "testKitWall12"], ["testKitWall25", "testKitWall25"]]
  heights = [joint["height"] for joint in built["joints"]]
  assert all(abs((height - heights[0]) / 1.0 - round(height - heights[0])) <= 1e-6 for height in heights)
  for section in sections:
    part = laid[section["name"]]
    if section["rise"] == 0:
      assert part["type"] == "EMPTY" and part["instance"] == section["piece"]
      continue
    assert part["type"] == "MESH" and part["mesh"] in shears
    assert sorted({(round(x, 6), round(y, 6)) for x, y, _ in part["local"]}) == sorted(tuple(point) for point in pieces[section["piece"]])
  assert len(shears) == len({(section["piece"], section["rise"]) for section in sections if section["rise"] != 0})
  ends = []
  for index, section in enumerate(sections):
    points = numpy.array(laid[section["name"]]["points"])
    leg = 0 if index < 3 else 1
    along = points[:, 1] if leg == 0 else points[:, 0]
    across = points[:, 0] if leg == 0 else points[:, 1]
    low, high = along.min(), along.max()
    start, end = (high, low) if leg == 0 else (low, high)
    footStart = points[numpy.abs(along - start) < 1e-4][:, 2].min()
    footEnd = points[numpy.abs(along - end) < 1e-4][:, 2].min()
    for face in (across.min(), across.max()):
      for step in range(int(abs(end - start)) + 1):
        share = step / abs(end - start)
        place = start + (end - start) * share
        x, y = (face, place) if leg == 0 else (place, face)
        assert footStart + (footEnd - footStart) * share <= structurePlots.plotGround(x, y) - 1.0 + 1e-6, (section["name"], x, y)
    ends.append((section["name"], leg, start, end, footStart, footEnd))
  for (_, leg, _, end, _, footEnd), (_, nextLeg, nextStart, _, footStart, _) in zip(ends[:-1], ends[1:]):
    if leg == nextLeg:
      assert abs(end - nextStart) < 1e-4 and abs(footEnd - footStart) < 1e-4
  assert [post["joint"] for post in built["posts"]] == [0, 3, 5]


def testASteppedWallStandsEachSectionLevelSinkUnderItsLowestGround(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    built = await session.expectSuccess("buildWall", wallArguments(kitPath, "steppedWall", [[55, -45], [55, -107.5]], follow="step"))
    return built, await structurePlots.parts(session, [section["name"] for section in built["sections"]])

  built, laid = stageBlenderServer.session(steps)
  for section in built["sections"]:
    points = numpy.array(laid[section["name"]]["points"])
    north = points[:, 1].max()
    assert laid[section["name"]]["type"] == "EMPTY"
    assert abs(points[:, 2].min() - (structurePlots.slopeHeight(north) - 1.0)) <= 0.01, section


def testALegTheModulesCannotFillIsRefusedNamingTheLengthsThatFit(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    return await session.expectError("buildWall", wallArguments(kitPath, "shortWall", [[0, -20], [113, -20]]))

  refusal = stageBlenderServer.session(steps)
  assert "Leg 0 is 113.00 long" in refusal and "112.5 (move its end -0.50)" in refusal and "125 (move its end +12.00)" in refusal


def testATurnNeedsAPostAndAThinPostIsRefused(stageBlenderServer, tmp_path):
  path = [[30, -60], [30, -85], [55, -85]]

  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    bare = await session.expectError("buildWall", wallArguments(kitPath, "cornerWall", path))
    thin = await session.expectError("buildWall", wallArguments(kitPath, "cornerWall", path, posts={"piece": "testKitThinPost", "at": "turns"}))
    built = await session.expectSuccess("buildWall", wallArguments(kitPath, "cornerWall", path, posts={"piece": "testKitPost", "at": "turns"}))
    posts = await structurePlots.parts(session, [post["name"] for post in built["posts"]])
    return bare, thin, built, posts

  bare, thin, built, posts = stageBlenderServer.session(steps)
  assert "turns 90.00 degrees at point 1; a turn needs a post" in bare
  assert "8 x 8, no wider or deeper than the wall (10)" in thin
  turn = next(post for post in built["posts"] if post["at"] == [30.0, -85.0])
  lowest = structurePlots.slopeHeight(-85 + 6)
  assert abs(turn["base"] - (lowest - 1.0)) <= 1e-4
  assert abs(min(point[2] for point in posts[turn["name"]]["points"]) - (lowest - 1.0)) <= 0.01


def testVariantsPutAnotherSectionInTheRun(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await structurePlots.testKit(session, tmp_path)
    await session.expectSuccess("createKitPiece", {
      "name": "testKitWall25Window", "kind": "wall", "size": [25, 10, 30], "location": [100, 40, 0],
      "materials": {"face": "testKitStone", "edge": "testKitTrim"}, "worldUnitsPerRepeat": {"face": 12.5, "edge": 5},
    })
    await session.expectSuccess("cutOpening", {"piece": "testKitWall25Window", "kind": "window", "along": 0, "width": 8, "height": 8, "sill": 12})
    await session.expectSuccess("saveFile", {})
    await structurePlots.testPlot(session, tmp_path)
    plain = await session.expectSuccess("buildWall", wallArguments(str(kitPath), "plainRun", [[10, -20], [110, -20]]))
    varied = await session.expectSuccess("buildWall", wallArguments(str(kitPath), "variedRun", [[10, 0], [110, 0]], variants={"2": "testKitWall25Window"}))
    other = await session.expectError("buildWall", wallArguments(str(kitPath), "otherRun", [[10, -35], [110, -35]], variants={"2": "testKitWall12"}))
    laid = await structurePlots.parts(session, [section["name"] for section in varied["sections"]])
    return plain, varied, other, laid

  plain, varied, other, laid = stageBlenderServer.session(steps)
  assert [section["piece"] for section in varied["sections"]] == ["testKitWall25", "testKitWall25", "testKitWall25Window", "testKitWall25"]
  assert [laid[section["name"]]["instance"] for section in varied["sections"]] == ["testKitWall25", "testKitWall25", "testKitWall25Window", "testKitWall25"]
  assert [section["piece"] for section in plain["sections"]] == ["testKitWall25"] * 4
  assert [joint["at"][0] for joint in varied["joints"]] == [10.0, 35.0, 60.0, 85.0, 110.0]
  assert "Variant 'testKitWall12' at section 2 is 12.5 long" in other


def testAWallBuriedPastItsMaximumIsRefused(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "raise", "center": [44, -20, 0], "radius": 10, "strength": 15, "direction": [0, 0, 1]})
    return await session.expectError("buildWall", wallArguments(kitPath, "humpWall", [[20, -20], [70, -20]]))

  refusal = stageBlenderServer.session(steps)
  assert "Section 0 would be buried" in refusal and "over maximumBurial 10" in refusal
  depth = float(refusal.split("would be buried ")[1].split(" deep")[0])
  assert depth > 10


def testAWallOnLevelGroundStaysLevelEveryJointAtOneHeight(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    flat = await session.expectSuccess("buildWall", wallArguments(kitPath, "flatWall", [[10, -20], [60, -20], [60, -7.5]], posts=turnPost))
    contour = await session.expectSuccess("buildWall", wallArguments(kitPath, "contourWall", [[30, -80], [80, -80]]))
    return flat, contour

  flat, contour = stageBlenderServer.session(steps)
  # On the plaza at 0, and along the slope's level line at y -80: no joint lower than another, no section sheared.
  assert {joint["height"] for joint in flat["joints"]} == {flat["joints"][0]["height"]} and abs(flat["joints"][0]["height"] + 1.5) <= 1e-4
  assert [section["rise"] for section in flat["sections"]] == [0.0, 0.0, 0.0]
  assert len({joint["height"] for joint in contour["joints"]}) == 1 and [section["rise"] for section in contour["sections"]] == [0.0, 0.0]
  assert [section["base"] for section in contour["sections"]] == [[contour["joints"][0]["height"]] * 2] * 2


def testAFramedDoorStandsInAWallRunOfItsModule(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await structurePlots.testKit(session, tmp_path)
    await session.expectSuccess("createMaterial", {"name": "testKitFrame", "diffuseTexture": str(writePNG(tmp_path / "textures" / "testKitFrame.png", 4, 4, (40, 30, 20, 255)))})
    await session.expectSuccess("createKitPiece", {
      "name": "testKitWall25Door", "kind": "wall", "size": [25, 10, 30], "location": [100, 40, 0],
      "materials": {"face": "testKitStone", "edge": "testKitTrim"}, "worldUnitsPerRepeat": {"face": 12.5, "edge": 5},
    })
    await session.expectSuccess("cutOpening", {"piece": "testKitWall25Door", "kind": "door", "along": 0, "width": 10, "height": 16, "frame": {"width": 1.5, "depth": 0.5, "material": "testKitFrame", "worldUnitsPerRepeat": 2.5}})
    await session.expectSuccess("saveFile", {})
    await structurePlots.testPlot(session, tmp_path)
    return await session.expectSuccess("buildWall", wallArguments(str(kitPath), "gateRun", [[10, -20], [110, -20]], variants={"1": "testKitWall25Door"}))

  built = stageBlenderServer.session(steps)
  assert [section["piece"] for section in built["sections"]] == ["testKitWall25", "testKitWall25Door", "testKitWall25", "testKitWall25"]


def testASteepShearIsRefusedAndASteppedWallReportsItsSectionsBases(stageBlenderServer, tmp_path):
  down = [[-15, -60], [-40, -60]]

  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    steep = await session.expectError("buildWall", wallArguments(kitPath, "gorgeWall", down, maximumBurial=50))
    stepped = await session.expectSuccess("buildWall", wallArguments(kitPath, "gorgeWall", down, follow="step", maximumBurial=50))
    buried = await session.expectError("buildWall", wallArguments(kitPath, "buriedWall", [[-15, -80], [-40, -80]], follow="step"))
    return steep, stepped, buried

  steep, stepped, buried = stageBlenderServer.session(steps)
  # Down the gorge's wall, 2 in every 1, a sheared section would lean its courses well past 30 degrees.
  assert "Section 0 would be sheared" in steep and "steeper than 30: its courses would run up the slope; step the wall" in steep
  base = stepped["sections"][0]["base"]
  assert base[0] == base[1] and abs(base[0] - (structurePlots.gorgeHeight(-40) - 1.0)) <= 0.01
  assert [joint["bases"] for joint in stepped["joints"]] == [[None, base[0]], [base[1], None]]
  assert "or allow a deeper burial (maximumBurial)" in buried and "step it" not in buried


def testAnElevationStandsOnTheGroundShortOfTheWallsOtherLegs(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    return await session.expectSuccess("buildWall", wallArguments(kitPath, "yard", [[20, -30], [70, -30], [70, -5], [20, -5], [20, -30]], posts=turnPost))

  built = stageBlenderServer.session(steps)
  # The yard's fronts face in: each leg is seen from inside it, a player's eye (playerScale) over the plaza and short of the leg across, aimed
  # two fifths of the way up the wall from its foot so the foot is in frame.
  assert built["views"]["front0"] == {"eye": [45.0, -17.0, round(eyeHeight, 3)], "target": [45.0, -30.0, 12.0]}
  assert built["views"]["front1"] == {"eye": [32.0, -17.5, round(eyeHeight, 3)], "target": [70.0, -17.5, 12.0]}


def testAnElevationUnderAnOverhangLooksAtTheWallFromTheGroundUnderIt(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    await session.expectSuccess("createPrimitive", {"kind": "cube", "name": "overhang", "size": [100, 100, 10], "location": [45, -20, 100]})
    return await session.expectSuccess("buildWall", wallArguments(kitPath, "shelteredWall", [[20, -20, 0], [45, -20, 0]], frontSide="right"))

  built = stageBlenderServer.session(steps)
  # The overhang's top, at 110, is the highest ground over the wall and the slope in front of it; the elevation stands on the slope
  # 39 out (1.3 of the wall's height), aimed 12 up the wall from its path's height.
  front = built["views"]["front0"]
  assert front["target"] == [32.5, -20.0, 12.0]
  assert front["eye"][:2] == [32.5, -59.0] and abs(front["eye"][2] - (structurePlots.slopeHeight(-59.0) + eyeHeight)) <= 0.02


def testSectionsAndVariantsAHundredThousandthOffInSizeAreTakenAndFurtherOffRefused(stageBlenderServer, tmp_path):
  async def steps(session):
    kitPath = await structurePlots.testKit(session, tmp_path)
    for name, size, location in (
      ("testKitWall25Deep", [25, 10.00002, 30], [100, 80, 0]), ("testKitWall12Tall", [12.5, 10, 30.00002], [130, 80, 0]),
      ("testKitWall25Deeper", [25, 10.05, 30], [100, 120, 0]),
    ):
      await session.expectSuccess("createKitPiece", {
        "name": name, "kind": "wall", "size": size, "location": location,
        "materials": {"face": "testKitStone", "edge": "testKitTrim"}, "worldUnitsPerRepeat": {"face": 12.5, "edge": 5},
      })
    await session.expectSuccess("saveFile", {})
    await structurePlots.testPlot(session, tmp_path)
    deep = await session.expectSuccess("buildWall", wallArguments(str(kitPath), "deepRun", [[10, -20], [110, -20]], variants={"1": "testKitWall25Deep"}))
    tall = await session.expectSuccess("buildWall", wallArguments(str(kitPath), "tallRun", [[10, 0], [47.5, 0]], sections=["testKitWall25", "testKitWall12Tall"]))
    deeper = await session.expectError("buildWall", wallArguments(str(kitPath), "deeperRun", [[10, -45], [110, -45]], variants={"1": "testKitWall25Deeper"}))
    return deep, tall, deeper

  deep, tall, deeper = stageBlenderServer.session(steps)
  assert [section["piece"] for section in deep["sections"]] == ["testKitWall25", "testKitWall25Deep", "testKitWall25", "testKitWall25"]
  assert [section["piece"] for section in tall["sections"]] == ["testKitWall25", "testKitWall12Tall"]
  assert "Variant 'testKitWall25Deeper' at section 1 is 25 long, 10.05 deep, 30 tall; section 1 is a 25 module 10 deep and 30 tall" in deeper


readLaid = """
import bpy, json
result = {name: json.loads(bpy.data.collections[name]['zonewrightStructure'])['laid'] for name in names}
"""


async def laidOf(session, *names):
  """Each named wall's sections and posts as its record keeps them."""
  return (await session.expectSuccess("runPython", {"code": f"names = {list(names)!r}\n" + readLaid}))["result"]


def onWall(kitPath, name, path, base, **changes):
  return wallArguments(kitPath, name, path, on=base) | changes


def testARunOnAWallStandsEachSectionAndPostOnTheTopsUnderIt(stageBlenderServer, tmp_path):
  slope = [[30, -45], [30, -107.5]]
  corner = [[10, -20], [60, -20], [60, -7.5]]

  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    lower = await session.expectSuccess("buildWall", wallArguments(kitPath, "slopeLower", slope))
    upper = await session.expectSuccess("buildWall", onWall(kitPath, "slopeUpper", slope, "slopeLower"))
    await session.expectSuccess("buildWall", wallArguments(kitPath, "cornerLower", corner, posts=turnPost))
    cornerUpper = await session.expectSuccess("buildWall", onWall(kitPath, "cornerUpper", corner, "cornerLower", posts=turnPost))
    laid = await laidOf(session, "slopeLower", "slopeUpper", "cornerLower", "cornerUpper")
    names = [section["name"] for section in lower["sections"] + upper["sections"]]
    return lower, upper, cornerUpper, laid, await structurePlots.parts(session, names)

  lower, upper, cornerUpper, laid, placed = stageBlenderServer.session(steps)
  for low, high in (("slopeLower", "slopeUpper"), ("cornerLower", "cornerUpper")):
    assert [section["base"] for section in laid[high]["sections"]] == [[base + section["height"] for base in section["base"]] for section in laid[low]["sections"]]
    assert [post["base"] for post in laid[high]["posts"]] == [post["base"] + post["height"] for post in laid[low]["posts"]]
  assert len(laid["cornerUpper"]["posts"]) == 3
  # On the slope the lower run's sections rise by whole steps, so the upper run shears its sections by the same rises and shares their models.
  assert any(section["rise"] != 0 for section in lower["sections"])
  assert [section["model"] for section in upper["sections"]] == [section["model"] for section in lower["sections"]]
  assert [section["shear"] for section in upper["sections"]] == ["reused" if section["rise"] else None for section in lower["sections"]]
  assert [section["standsOn"] for section in upper["sections"]] == [[section["name"]] for section in lower["sections"]]
  assert sorted(cornerUpper["standsOn"]) == sorted([section["name"] for section in laid["cornerLower"]["sections"]] + [post["name"] for post in laid["cornerLower"]["posts"]])
  for under, over in zip(lower["sections"], upper["sections"]):
    top, foot = numpy.array(placed[under["name"]]["points"]), numpy.array(placed[over["name"]]["points"])
    for end in (top[:, 1].max(), top[:, 1].min()):
      assert abs(top[numpy.abs(top[:, 1] - end) < 1e-4][:, 2].max() - foot[numpy.abs(foot[:, 1] - end) < 1e-4][:, 2].min()) <= 1e-4, (over["name"], end)
  # The elevation of the upper run aims 12 up from its own foot, the top under it a quarter of the way along its second section, from
  # the slope in front.
  second = upper["sections"][1]["base"]
  front = upper["views"]["front0"]
  assert front["target"][:2] == [30.0, -76.25] and abs(front["target"][2] - (0.75 * second[0] + 0.25 * second[1] + 12.0)) <= 1e-3
  assert front["eye"][:2] == [84.875, -76.25] and abs(front["eye"][2] - (structurePlots.slopeHeight(-76.25) + eyeHeight)) <= 0.02


def testARunOnAWallIsRefusedOffItsTopOverAStepOrWithItsOwnHeights(stageBlenderServer, tmp_path):
  stepped = [[55, -45], [55, -107.5]]

  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    await session.expectSuccess("buildWall", wallArguments(kitPath, "steppedLower", stepped, follow="step"))
    aside = await session.expectError("buildWall", onWall(kitPath, "asideRun", [[60, -45], [60, -70]], "steppedLower", follow="step"))
    straddling = await session.expectError("buildWall", onWall(kitPath, "straddlingRun", [[55, -57.5], [55, -82.5]], "steppedLower", follow="step"))
    heights = await session.expectError("buildWall", onWall(kitPath, "heightRun", [[55, -45, 0], [55, -70, 0]], "steppedLower"))
    sunk = await session.expectError("buildWall", onWall(kitPath, "sunkRun", [[55, -45], [55, -70]], "steppedLower", sink=1))
    level = await session.expectSuccess("buildWall", onWall(kitPath, "levelRun", stepped, "steppedLower", follow="step"))
    lower = await laidOf(session, "steppedLower")
    return aside, straddling, heights, sunk, level, lower["steppedLower"]

  aside, straddling, heights, sunk, level, lower = stageBlenderServer.session(steps)
  assert (
    "Section 0, from [60.0, -45.0] to [60.0, -70.0], leaves the top of 'steppedLower' at [60.0, -45.0]: the run's line stands 5.00 off the line"
    " of its section 'steppedLowerSection01' there, more than the 0.01 that keeps the run's faces within its faces"
  ) in aside
  assert "Section 0, from [55.0, -57.5] to [55.0, -82.5], stands over a bend or step in the top of 'steppedLower' at [55.0, -70.0]" in straddling
  assert "A run on a wall takes its path as [x, y] points" in heights
  assert "sink and maximumBurial are null with on (got 1.0 and None)" in sunk
  assert [section["base"] for section in level["sections"]] == [[round(section["base"][0] + section["height"], 6)] * 2 for section in lower["sections"]]


def testAWallUnderARunCannotBeTakenBackAndTheRunGoesStaleWhenItsTopMoves(stageBlenderServer, tmp_path):
  line = [[10, -20], [110, -20]]

  def state(listed, name):
    entry = next(entry for entry in listed["structures"] if entry["name"] == name)
    return entry["stale"], entry["why"], entry["base"]

  async def steps(session):
    kitPath = await kitAndPlot(session, tmp_path)
    await session.expectSuccess("buildWall", wallArguments(kitPath, "lowerWall", line))
    await session.expectSuccess("buildWall", onWall(kitPath, "upperWall", line, "lowerWall"))
    fresh = await session.expectSuccess("getStructures")
    carried = await session.expectError("removeStructure", {"name": "lowerWall"})
    await session.expectSuccess("editStructure", {"name": "lowerWall"})
    same = await session.expectSuccess("getStructures")
    await session.expectSuccess("editStructure", {"name": "lowerWall", "changes": {"sink": 3}})
    sunk = await session.expectSuccess("getStructures")
    await session.expectSuccess("editStructure", {"name": "upperWall"})
    relaid = await laidOf(session, "lowerWall", "upperWall")
    refit = await session.expectSuccess("getStructures")
    await session.expectSuccess("editStructure", {"name": "lowerWall", "changes": {"path": [[10, -30], [110, -30]]}})
    moved = await session.expectSuccess("getStructures")
    await session.expectSuccess("removeStructure", {"name": "upperWall"})
    removed = await session.expectSuccess("removeStructure", {"name": "lowerWall"})
    return fresh, carried, same, sunk, relaid, refit, moved, removed

  fresh, carried, same, sunk, relaid, refit, moved, removed = stageBlenderServer.session(steps)
  assert state(fresh, "upperWall") == (False, [], {"on": "lowerWall", "moved": False, "largestChange": 0.0, "refused": None})
  assert state(fresh, "lowerWall") == (False, [], None)
  assert "['upperWall'] stand on 'lowerWall' (their on): take them back first" in carried
  # Laid again as it was, the wall under the run keeps its tops, and the run stays fresh.
  assert state(same, "upperWall")[0] is False
  # Sunk 2 deeper, the wall under the run drops its tops by 2 and the run goes stale until it is laid again on them.
  assert state(sunk, "upperWall") == (True, ["base"], {"on": "lowerWall", "moved": True, "largestChange": 2.0, "refused": None})
  assert [section["base"] for section in relaid["upperWall"]["sections"]] == [[base + section["height"] for base in section["base"]] for section in relaid["lowerWall"]["sections"]]
  assert all(abs(section["base"][0] - (28.5 - 2.0)) <= 1e-4 for section in relaid["upperWall"]["sections"])
  assert state(refit, "upperWall")[0] is False
  stale, why, base = state(moved, "upperWall")
  assert stale and why == ["base"] and base["moved"] and base["largestChange"] is None
  assert "leaves the top of 'lowerWall' at [10.0, -20.0]: the run's line stands 10.00 off the line of its section 'lowerWallSection01'" in base["refused"]
  assert removed["name"] == "lowerWall"
