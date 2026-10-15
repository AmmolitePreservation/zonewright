import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqArchive
import eqgFiles
from testModelsAndDressing import planarSignedArea, readShapedMesh


async def surfaceHeights(session, points):
  measured = await session.expectSuccess("measure", {"points": [[x, y, 100] for x, y in points], "snapToSurface": True})
  return [round(point[2], 3) for point in measured["points"]]


def testPassesAreAdjustedRemovedAndCollapsedWithoutRedoingOthers(stageBlenderServer):
  hilltop, dipCenter = (0, 0), (16, 16)
  # Midpoints of the grid's edges, where a cut adds a vertex: on the hill's and the dip's slopes.
  edgePoints = [(4, 0), (0, 12), (12, 16), (16, 20), (4, 8)]

  async def steps(session):
    await session.expectSuccess("newFile", {"discardUnsavedChanges": True})
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [64, 64], "spacing": 8, "location": [0, 0, 0]})
    added = await session.expectSuccess("addShapingPass", {"objectName": "ground", "name": "hill"})
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "raise", "center": [0, 0, 0], "radius": 20, "strength": 10, "direction": [0, 0, 1]})
    full = await surfaceHeights(session, [hilltop])
    await session.expectSuccess("setShapingPass", {"objectName": "ground", "name": "hill", "strength": 0.5})
    half = await surfaceHeights(session, [hilltop])
    # Shaping at half strength still moves the ground as asked: the pass takes the move divided by its strength.
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "raise", "center": [0, 0, 5], "radius": 20, "strength": 2, "direction": [0, 0, 1]})
    raisedAtHalf = await surfaceHeights(session, [hilltop])
    await session.expectSuccess("setShapingPass", {"objectName": "ground", "name": "hill", "muted": True})
    muted = await surfaceHeights(session, [hilltop])
    mutedWrite = await session.expectError("sculptAtPoint", {"objectName": "ground", "mode": "raise", "center": [0, 0, 0], "radius": 20, "strength": 1})
    await session.expectSuccess("setShapingPass", {"objectName": "ground", "name": "hill", "muted": False, "strength": 1})
    await session.expectSuccess("addShapingPass", {"objectName": "ground", "name": "dip"})
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "lower", "center": [16, 16, 0], "radius": 10, "strength": 4, "direction": [0, 0, 1]})
    both = await surfaceHeights(session, [hilltop, dipCenter])
    faceChange = await session.expectError("deleteFaces", {"objectName": "ground", "selector": {"box": {"minimum": [24, 24, -10], "maximum": [32, 32, 10]}}})
    onEdges = await surfaceHeights(session, edgePoints)
    split = await session.expectSuccess("subdivide", {"objectName": "ground", "selector": {"all": True}, "cuts": 1})
    splitDetail = await session.expectSuccess("getObjectDetail", {"name": "ground"})
    onEdgesSplit = await surfaceHeights(session, edgePoints)
    removed = await session.expectSuccess("removeShapingPass", {"objectName": "ground", "name": "hill"})
    withoutHill = await surfaceHeights(session, [hilltop, dipCenter])
    collapsed = await session.expectSuccess("collapseShapingPasses", {"objectName": "ground"})
    afterCollapse = await surfaceHeights(session, [hilltop, dipCenter])
    detail = await session.expectSuccess("getObjectDetail", {"name": "ground"})
    subdivided = await session.expectSuccess("subdivide", {"objectName": "ground", "selector": {"all": True}, "cuts": 1})
    return added, full, half, raisedAtHalf, muted, mutedWrite, both, faceChange, onEdges, split, splitDetail, onEdgesSplit, removed, withoutHill, collapsed, afterCollapse, detail, subdivided

  (
    added, full, half, raisedAtHalf, muted, mutedWrite, both, faceChange, onEdges, split, splitDetail, onEdgesSplit, removed, withoutHill, collapsed,
    afterCollapse, detail, subdivided,
  ) = stageBlenderServer.session(steps)
  assert added["passes"] == [{"name": "hill", "strength": 1.0, "muted": False, "active": True}]
  assert full == [10.0] and half == [5.0]
  # The hill now holds 10 + 2 / 0.5 = 14 units: 7 at half strength, as seen after the raise.
  assert raisedAtHalf == [7.0]
  assert muted == [0.0]
  assert "muted" in mutedWrite
  # The dip lies outside the hill's reach and the hill outside the dip's.
  assert both == [14.0, -4.0]
  assert "collapse them (collapseShapingPasses)" in faceChange
  # Subdividing keeps both passes: each new vertex lies between the two it splits in every pass, so along the old edges nothing moves,
  # and removing a pass afterwards still takes back exactly its own work.
  assert split["subdividedFaces"] == 64 and split["faces"] == 256 and split["recutCaves"] == []
  assert [(entry["name"], entry["strength"]) for entry in splitDetail["shapingPasses"]] == [("hill", 1.0), ("dip", 1.0)]
  assert onEdgesSplit == onEdges and len(set(onEdges)) > 2
  assert removed["passes"] == [{"name": "dip", "strength": 1.0, "muted": False, "active": True}]
  assert withoutHill == [0.0, -4.0]
  assert collapsed["collapsed"] == ["dip"] and afterCollapse == [0.0, -4.0]
  assert detail["shapingPasses"] == []
  assert subdivided["faces"] > 0


def testPassesRefuseWhatTheyCannotHold(stageBlenderServer):
  async def steps(session):
    await session.expectSuccess("newFile", {"discardUnsavedChanges": True})
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [32, 32], "spacing": 8, "location": [0, 0, 0]})
    reserved = await session.expectError("addShapingPass", {"objectName": "ground", "name": "base"})
    await session.expectSuccess("addShapingPass", {"objectName": "ground", "name": "forms"})
    duplicate = await session.expectError("addShapingPass", {"objectName": "ground", "name": "forms"})
    tooStrong = await session.expectError("setShapingPass", {"objectName": "ground", "name": "forms", "strength": 3})
    unknown = await session.expectError("removeShapingPass", {"objectName": "ground", "name": "erosion"})
    return reserved, duplicate, tooStrong, unknown

  reserved, duplicate, tooStrong, unknown = stageBlenderServer.session(steps)
  assert "'base' names the shape passes build on" in reserved
  assert "already has a shaping pass 'forms'" in duplicate
  assert "strength must be within [-1.0, 2.0]" in tooStrong
  assert "has no shaping pass 'erosion'; its passes: ['forms']" in unknown


def testExportWritesTheTerrainWithItsPassesCombined(stageBlenderServer, tmp_path):
  texturePath = tmp_path / "ground.png"
  Image.new("RGBA", (8, 8), (90, 120, 60, 255)).save(texturePath)
  archivePath = tmp_path / "passplot.eqg"

  async def steps(session):
    await session.expectSuccess("newFile", {"discardUnsavedChanges": True})
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [64, 64], "spacing": 8, "location": [0, 0, 0], "collection": "terrain"})
    await session.expectSuccess("createMaterial", {"name": "grass", "diffuseTexture": str(texturePath)})
    await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
    await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 32, "direction": [0, 0, 1]})
    await session.expectSuccess("addShapingPass", {"objectName": "ground", "name": "hill"})
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "raise", "center": [0, 0, 0], "radius": 20, "strength": 10, "direction": [0, 0, 1]})
    await session.expectSuccess("setShapingPass", {"objectName": "ground", "name": "hill", "strength": 0.3})
    await session.expectSuccess("saveFile", {"path": str(tmp_path / "passplot.blend")})
    return await session.expectSuccess("exportZone", {"path": str(archivePath), "purpose": "test"})

  stageBlenderServer.session(steps)
  terrain = eqgFiles.parseModel(eqArchive.EQArchive(archivePath).read("ter_passplot.ter"), "ter_passplot.ter")
  assert round(float(terrain["vertices"][:, 2].max()), 4) == 3.0


def testViewsSeeAPassShapedJustBefore(stageBlenderServer):
  async def steps(session):
    await session.expectSuccess("newFile", {"discardUnsavedChanges": True})
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [64, 64], "spacing": 8, "location": [0, 0, 0]})
    await session.expectSuccess("setZoneProperties", {
      "ambientColor": [0.3, 0.3, 0.3], "specialAmbientColor": [0, 0, 0], "bounceColor": [0, 0, 0], "sunColor": [0.5, 0.5, 0.5],
      "sunAzimuthDegrees": 135, "sunElevationDegrees": 45, "fogColor": [0.5, 0.5, 0.5], "fogStart": 30, "fogEnd": 200, "fogDensity": 0.33, "fogOn": True, "maxClip": 400,
      "newEngineZone": False,
    })
    await session.expectSuccess("addShapingPass", {"objectName": "ground", "name": "pit"})
    await session.expectSuccess("sculptAtPoint", {"objectName": "ground", "mode": "lower", "center": [0, 0, 0], "radius": 20, "strength": 10, "direction": [0, 0, 1]})
    _, description = await session.expectImage("renderView", {"view": {"map": {"center": [0, 0], "width": 64}}, "shading": "layout"})
    return description

  assert stageBlenderServer.session(steps)["heightRange"] == [-10.0, 0.0]


def rowsOf(xOffset):
  """Selectors on a 64-unit grid at x offset xOffset: the rows from y 8 up (a cliff's top), and the row at y 0 (its foot)."""
  return (
    {"box": {"minimum": [xOffset - 40, 4, -100], "maximum": [xOffset + 40, 40, 100]}},
    {"box": {"minimum": [xOffset - 40, -1, -100], "maximum": [xOffset + 40, 1, 100]}},
  )


async def shapedMesh(session, name):
  return (await session.expectSuccess("runPython", {"code": f"objectName = '{name}'" + readShapedMesh}))["result"]


def invertedFaces(mesh):
  """Faces whose corners turn clockwise seen from above: facing down, or on a wall overhanging the ground at its foot."""
  return [face for face in mesh["faces"] if planarSignedArea([mesh["vertices"][index] for index in face]) < 0]


def footRow(mesh, y):
  return sorted([round(coordinate, 3) for coordinate in vertex] for vertex in mesh["vertices"] if abs(vertex[1] - y) < 1e-3)


def testCarveUnfoldsTheFacesItFlattensThatAPassInvertedInPlan(stageBlenderServer):
  # A cliff 40 high rises between the rows at y 0 and 8; a later pass leans the row at its foot 12 out past the row at its top, so the
  # 16 triangles between them hang inverted in plan, overhanging the foot 4. A carve then flattens the whole grid to 0.
  carve = {"mode": "carve", "outline": [[-40, -40], [40, -40], [40, 40], [-40, 40]], "base": 0, "profile": [[0, 0], [8, 0]], "conformBreaks": False}

  async def built(session, name, xOffset, passes):
    await session.expectSuccess("createTerrainGrid", {"name": name, "size": [64, 64], "spacing": 8, "location": [xOffset, 0, 0]})
    await session.expectSuccess("followContours", {"objectName": name})
    for passName, selector, offset in zip(("cliff", "lean"), rowsOf(xOffset), ([0, 0, 40], [0, 12, 0])):
      if passes:
        await session.expectSuccess("addShapingPass", {"objectName": name, "name": passName})
      await session.expectSuccess("moveVertices", {"objectName": name, "selector": selector, "offset": offset})
    if passes:
      await session.expectSuccess("addShapingPass", {"objectName": name, "name": "floor"})
    before = await shapedMesh(session, name)
    outline = [[x + xOffset, y] for x, y in carve["outline"]]
    carved = await session.expectSuccess("sculptOutline", carve | {"objectName": name, "outline": outline})
    return before, carved, await shapedMesh(session, name)

  async def steps(session):
    await session.expectSuccess("newFile", {"discardUnsavedChanges": True})
    layered = await built(session, "ground", 0, True)
    bare = await built(session, "bare", 200, False)
    return layered, bare

  (before, carved, after), (bareBefore, bareCarved, bareAfter) = stageBlenderServer.session(steps)
  assert len(invertedFaces(before)) == 16 and len(invertedFaces(bareBefore)) == 16
  # Flattened, the 16 would face straight down over the ground folded across them; their foot goes back where the base has it.
  assert (carved["unfoldedFaces"], carved["foldedFaces"]) == (16, 0)
  assert invertedFaces(after) == []
  assert footRow(after, 0) == [[x, 0.0, 0.0] for x in range(-32, 33, 8)] and footRow(after, 12) == []
  assert {round(vertex[2], 3) for vertex in after["vertices"]} == {0.0}
  # Without passes nothing records where the foot lay, so the folds are counted and left.
  assert (bareCarved["unfoldedFaces"], bareCarved["foldedFaces"]) == (0, 16)
  assert len(invertedFaces(bareAfter)) == 16 and footRow(bareAfter, 12) == [[x, 12.0, 0.0] for x in range(-32, 33, 8)]


def testUnfoldTerrainPutsFoldsBackInPlanKeepingTheirHeights(stageBlenderServer):
  # A plateau pass lifts the whole grid to 10 and a lean pass slides the row at y 0 to y 12, past the row at y 8: the 16 triangles
  # between them face straight down at 10. The base has the grid at 0, so heights taken from it would drop the plateau.
  strip = {"box": {"minimum": [-40, 6, -100], "maximum": [40, 14, 100]}}
  farSide = {"box": {"minimum": [-40, 20, -100], "maximum": [40, 40, 100]}}

  async def steps(session):
    await session.expectSuccess("newFile", {"discardUnsavedChanges": True})
    await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [64, 64], "spacing": 8, "location": [0, 0, 0]})
    await session.expectSuccess("createTerrainGrid", {"name": "bare", "size": [64, 64], "spacing": 8, "location": [200, 0, 0]})
    for name in ("ground", "bare"):
      await session.expectSuccess("followContours", {"objectName": name})
    await session.expectSuccess("addShapingPass", {"objectName": "ground", "name": "plateau"})
    await session.expectSuccess("moveVertices", {"objectName": "ground", "selector": {"all": True}, "offset": [0, 0, 10]})
    await session.expectSuccess("addShapingPass", {"objectName": "ground", "name": "lean"})
    await session.expectSuccess("moveVertices", {"objectName": "ground", "selector": rowsOf(0)[1], "offset": [0, 12, 0]})
    await session.expectSuccess("moveVertices", {"objectName": "bare", "selector": rowsOf(200)[1], "offset": [0, 12, 0]})
    before = await shapedMesh(session, "ground")
    elsewhere = await session.expectSuccess("unfoldTerrain", {"objectName": "ground", "selector": farSide})
    unfolded = await session.expectSuccess("unfoldTerrain", {"objectName": "ground", "selector": strip})
    after = await shapedMesh(session, "ground")
    bare = await session.expectSuccess("unfoldTerrain", {"objectName": "bare"})
    return before, elsewhere, unfolded, after, bare, await shapedMesh(session, "bare")

  before, elsewhere, unfolded, after, bare, bareAfter = stageBlenderServer.session(steps)
  assert len(invertedFaces(before)) == 16
  assert elsewhere == {"object": "ground", "unfoldedFaces": 0, "foldedFaces": 0, "foldedAt": [], "movedVertices": 0}
  assert unfolded == {"object": "ground", "unfoldedFaces": 16, "foldedFaces": 0, "foldedAt": [], "movedVertices": 9}
  assert invertedFaces(after) == []
  assert [round(vertex[2], 3) for vertex in after["vertices"]] == [round(vertex[2], 3) for vertex in before["vertices"]] == [10.0] * 81
  assert footRow(after, 0) == [[x, 0.0, 10.0] for x in range(-32, 33, 8)] and footRow(after, 12) == []
  unmoved = [index for index, vertex in enumerate(before["vertices"]) if abs(vertex[1] - 12) > 1e-3]
  assert len(unmoved) == 72 and all(after["vertices"][index] == before["vertices"][index] for index in unmoved)
  # Without passes it names where the folds lie and moves nothing.
  assert (bare["unfoldedFaces"], bare["foldedFaces"], bare["movedVertices"]) == (0, 16, 0) and "no shaping passes" in bare["warning"]
  assert len(bare["foldedAt"]) == 5 and all(168 < x < 232 and 8 < y < 12 and z == 0 for x, y, z in bare["foldedAt"])
  assert len(invertedFaces(bareAfter)) == 16
