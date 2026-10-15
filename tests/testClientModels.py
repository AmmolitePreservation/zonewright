import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy
import pytest

from conftest import everquestClient, writePNG
from testModelsAndDressing import freshScene
from testVerifyExport import short as verifiedShort
from testVerifyExport import verifiedZone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import clientModelBundle
import eqArchive
import eqAxes
import eqgFiles
import modelMeasures
import pieceRows

clientRoot = Path(everquestClient)
crystal = {"zone": "chapterhouse", "model": "OBJ_FSCRYSTALA", "source": "crystals.eqg"}
tree = {"zone": "nektulosa", "model": "OBJ_TREE_BG_"}
kiln = {"zone": None, "model": "IT10801", "interactive": True, "objectType": "potteryKiln"}
door = {"zone": "eastwastesshard", "model": "OBJ_COLDAIN_1DOOR", "source": "ew_houses.eqg", "openType": 0, "triggerDoor": "book"}
book = {"zone": "freeportwest", "model": "OBJ_POK_BOOK_", "openType": 58, "destination": {"zone": "qeynos2", "x": 10, "y": 20, "z": 3, "headingDegrees": 90}}


def clientFiles(archiveName, entry):
  """A client model's bytes and those of each texture it names that its archive holds."""
  archive = eqArchive.EQArchive(clientRoot / archiveName)
  textures = modelMeasures.eqgMeasures(eqgFiles.parseModel(archive.read(entry), entry))["textures"]
  return {entry: archive.read(entry)} | {name: archive.read(name) for name in textures if name in archive.entries}


async def ourBox(session, folder, name, textureStem, location):
  """A box of our own material, its texture a PNG named textureStem (stored as textureStem.dds), at location."""
  await session.expectSuccess("createMaterial", {"name": name, "diffuseTexture": str(writePNG(folder / f"{textureStem}.png", 4, 4, (200, 20, 20, 255)))})
  await session.expectSuccess("createPrimitive", {"kind": "cube", "name": f"{name}Box", "size": [4, 4, 4], "location": location})
  await session.expectSuccess("assignMaterial", {"objectName": f"{name}Box", "materialName": name})
  await session.expectSuccess("projectUVs", {"objectName": f"{name}Box", "method": "box", "worldUnitsPerRepeat": 4})


async def groundScene(session, folder):
  await freshScene(session)
  await session.expectSuccess("createTerrainGrid", {"name": "ground", "size": [200, 200], "spacing": 20, "location": [0, 0, 0], "collection": "terrain"})
  await session.expectSuccess("createMaterial", {"name": "grass", "diffuseTexture": str(writePNG(folder / "grass.png", 4, 4, (90, 120, 60, 255)))})
  await session.expectSuccess("assignMaterial", {"objectName": "ground", "materialName": "grass"})
  await session.expectSuccess("projectUVs", {"objectName": "ground", "method": "planar", "worldUnitsPerRepeat": 32, "direction": [0, 0, 1]})


def testClientModelsAreCopiedIntoTheArchiveByteForByte(stageBlenderServer, tmp_path):
  archivePath = tmp_path / "copies.eqg"

  async def steps(session):
    await groundScene(session, tmp_path)
    placedCrystal = await session.expectSuccess("placeObject", crystal | {"name": "crystal", "location": [10, 0, 0], "headingDegrees": 0})
    await session.expectSuccess("placeObject", tree | {"name": "tree", "location": [-30, 0, 0], "headingDegrees": 0, "scale": 0.5})
    placedKiln = await session.expectSuccess("placeObject", kiln | {"name": "kiln", "location": [0, 30, 0], "headingDegrees": 0})
    await session.expectSuccess("placeDoor", door | {"name": "door", "location": [40, 40, 0], "headingDegrees": 90})
    await session.expectSuccess("placeDoor", book | {"name": "book", "location": [-40, 40, 0], "headingDegrees": 0})
    await session.expectSuccess("saveFile", {"path": str(tmp_path / "copies.blend")})
    exported = await session.expectSuccess("exportZone", {"path": str(archivePath), "purpose": "test"})
    return placedCrystal, placedKiln, exported

  placedCrystal, placedKiln, exported = stageBlenderServer.session(steps)
  copies = {
    "obj_fscrystala.mod": clientFiles("crystals.eqg", "obj_fscrystala.mod"), "obj_tree_bg_.mod": clientFiles("nektulosa.eqg", "obj_tree_bg_.mod"),
    "it10801.mod": clientFiles("tradeskill_objects.eqg", "it10801.mod"), "obj_coldain_1door.mod": clientFiles("ew_houses.eqg", "obj_coldain_1door.mod"),
    "obj_pok_book_.mod": clientFiles("pok_book.eqg", "obj_pok_book_.mod"),
  }
  # Its textures are looked for where the client finds them, its own archive first.
  assert placedCrystal["ships"]["model"]["textureArchives"][0] == "crystals.eqg"
  assert placedCrystal["ships"] == {"model": {
    "kind": "eqgModel", "archive": "crystals.eqg", "entry": "obj_fscrystala.mod", "actor": "OBJ_FSCRYSTALA",
    "sha256": hashlib.sha256(copies["obj_fscrystala.mod"]["obj_fscrystala.mod"]).hexdigest(), "textureArchives": placedCrystal["ships"]["model"]["textureArchives"],
  }, "interactive": None}
  assert placedKiln["ships"]["interactive"] == {"table": "object", "objectName": "IT10801_ACTORDEF", "type": 22, "displayName": None}
  archive = eqArchive.EQArchive(archivePath)
  # Every model and each texture it names are in the archive exactly as the client ships them, scenery and rows alike.
  for files in copies.values():
    assert all(archive.read(name) == data for name, data in files.items()), sorted(files)
  zone = eqgFiles.parseZone(archive.read("copies.zon"), "copies.zon")
  placed = {placement["name"]: placement for placement in zone["placements"]}
  # Scenery is placed by the .zon under the client's entry; interactive pieces never are.
  assert placed["OBJ_FSCRYSTALA01"]["model"] == "obj_fscrystala.mod" and placed["OBJ_FSCRYSTALA01"]["position"] == (10.0, 0.0, 0.0)
  assert placed["OBJ_TREE_BG_01"]["model"] == "obj_tree_bg_.mod" and abs(placed["OBJ_TREE_BG_01"]["scale"] - 0.5) < 1e-6
  assert sorted({placement["model"] for placement in zone["placements"]} - {"ter_copies.ter"}) == ["obj_fscrystala.mod", "obj_tree_bg_.mod"]
  manifest = json.loads((tmp_path / "copies_export.json").read_text(encoding="ascii"))
  recorded = {copy["entry"]: copy for copy in manifest["clientModels"]}
  assert sorted(recorded) == sorted(copies)
  assert all(recorded[entry]["sha256"] == hashlib.sha256(files[entry]).hexdigest() for entry, files in copies.items())
  assert {entry: (copy["placements"], copy["rows"]) for entry, copy in recorded.items()} == {
    "obj_fscrystala.mod": (1, 0), "obj_tree_bg_.mod": (1, 0), "it10801.mod": (0, 1), "obj_coldain_1door.mod": (0, 1), "obj_pok_book_.mod": (0, 1),
  }
  assert {texture["name"]: texture["archive"] for texture in recorded["obj_fscrystala.mod"]["textures"]} == {name: "crystals.eqg" for name in copies["obj_fscrystala.mod"] if name.endswith(".dds")}
  findings = {finding["finding"]: finding for finding in exported["findings"]}
  assert findings["pieces as rows"]["pieces"] == 3
  assert findings["client models copied"]["bytes"] == sum(len(data) for files in copies.values() for data in files.values())
  assert exported["excluded"] == []


def testInteractivePiecesAreRowsNamingTheCopy(stageBlenderServer, tmp_path):
  async def steps(session):
    await verifiedZone(session, tmp_path)
    await session.expectSuccess("placeDoor", door | {"name": "door", "location": [60, -30, 0], "headingDegrees": 90})
    await session.expectSuccess("placeDoor", book | {"name": "book", "location": [70, -30, 0], "headingDegrees": 0, "scaleFactor": 150})
    await session.expectSuccess("saveFile", {})
    checked = await session.expectSuccess("checkExport", {"path": str(tmp_path / f"{verifiedShort}.eqg"), "purpose": "game"})
    # A later check replaces the check folder.
    shutil.copytree(checked["checkFolder"], tmp_path / "firstCheck")
    await session.expectSuccess("transformObjects", {"names": ["kiln"], "translate": [0, 0, 5]})
    await session.expectSuccess("saveFile", {})
    raised = await session.expectSuccess("checkExport", {"path": str(tmp_path / f"{verifiedShort}.eqg"), "purpose": "game"})
    raisedTest = await session.expectSuccess("checkExport", {"path": str(tmp_path / f"{verifiedShort}.eqg"), "purpose": "test"})
    return checked, raised, raisedTest

  checked, raised, raisedTest = stageBlenderServer.session(steps)
  assert checked["failures"] == []
  folder = tmp_path / "firstCheck"
  pieces = pieceRows.readPieces((folder / "server" / "sql" / f"{verifiedShort}_pieces.sql").read_text(encoding="utf-8"))
  doors = {row["name"]: row for row in pieces["doors"]}
  # Doors are numbered in object-name order (book, door), the door's trigger names the book's number, and the book teleports to
  # qeynos2's point in the server's axes; places are in the server's axes, headings in 512ths of a turn.
  assert [(row["doorid"], row["name"]) for row in pieces["doors"]] == [(1, "OBJ_POK_BOOK_"), (2, "OBJ_COLDAIN_1DOOR")]
  assert doors["OBJ_COLDAIN_1DOOR"]["triggerdoor"] == 1 and doors["OBJ_COLDAIN_1DOOR"]["opentype"] == 0
  assert (doors["OBJ_COLDAIN_1DOOR"]["pos_x"], doors["OBJ_COLDAIN_1DOOR"]["pos_y"], doors["OBJ_COLDAIN_1DOOR"]["pos_z"]) == (-30.0, 60.0, 0.0)
  assert (doors["OBJ_COLDAIN_1DOOR"]["heading"], doors["OBJ_POK_BOOK_"]["heading"]) == (eqAxes.eqHeadingFromHeading(90), eqAxes.eqHeadingFromHeading(0)) == (0, 128)
  assert doors["OBJ_COLDAIN_1DOOR"]["size"] == 100
  assert (doors["OBJ_POK_BOOK_"]["opentype"], doors["OBJ_POK_BOOK_"]["dest_zone"], doors["OBJ_POK_BOOK_"]["size"]) == (58, "qeynos2", 150)
  assert (doors["OBJ_POK_BOOK_"]["dest_x"], doors["OBJ_POK_BOOK_"]["dest_y"], doors["OBJ_POK_BOOK_"]["dest_z"]) == (20.0, 10.0, 3.0)
  [wheel] = pieces["objects"]
  assert (wheel["objectname"], wheel["type"], wheel["zoneid"], wheel["size"], wheel["itemid"]) == ("IT10800_ACTORDEF", 21, 901, 100.0, 0)
  assert (wheel["xpos"], wheel["ypos"], wheel["zpos"]) == (10.0, 40.0, 0.0) and (wheel["min_expansion"], wheel["max_expansion"]) == (-1, -1)
  archive = eqArchive.EQArchive(folder / f"{verifiedShort}.eqg")
  zone = eqgFiles.parseZone(archive.read(f"{verifiedShort}.zon"), "zon")
  assert {"obj_pok_book_.mod", "obj_coldain_1door.mod", "it10800.mod"} <= set(archive.entries)
  assert not {"obj_pok_book_.mod", "obj_coldain_1door.mod", "it10800.mod"} & {placement["model"] for placement in zone["placements"]}
  # Raised 5 off the ground, the wheel's row would be stood back on it by the server at boot: a game export refuses it, a test lists it.
  moved = [failure for failure in raised["failures"] if failure["failure"] == "row moved by the server"]
  assert [(failure["object"], failure["z"], failure["server"]) for failure in moved] == [("kiln", 5.0, 0.0)]
  assert [(finding["object"], finding["server"]) for finding in raisedTest["findings"] if finding["finding"] == "row moved by the server"] == [("kiln", 0.0)]


def testRowsComeBackWithTheArchive(stageBlenderServer, tmp_path):
  async def steps(session):
    await verifiedZone(session, tmp_path)
    await session.expectSuccess("placeDoor", book | {"name": "book", "location": [70, -30, 0], "headingDegrees": 0})
    await session.expectSuccess("saveFile", {})
    checked = await session.expectSuccess("checkExport", {"path": str(tmp_path / f"{verifiedShort}.eqg"), "purpose": "game"})
    await freshScene(session)
    imported = await session.expectSuccess("importZoneFile", {"path": str(Path(checked["checkFolder"]) / f"{verifiedShort}.eqg")})
    placed = await session.expectSuccess("runPython", {"code": (
      f"objects = bpy.data.collections['{verifiedShort} rows'].objects\n"
      "result = {o.name: [[round(v, 3) for v in o.location], round(o.rotation_euler.z, 4), o['zonewrightClientContent']] for o in objects}"
    )})
    return imported, placed["result"]

  imported, placed = stageBlenderServer.session(steps)
  assert imported["rows"]["moved"] == [] and imported["rows"]["notPlaced"] == []
  assert sorted(row["row"] for row in imported["rows"]["placed"]) == [f"{verifiedShort}_door1", f"{verifiedShort}_object1"]
  assert placed[f"{verifiedShort}_object1"][0] == [40.0, 10.0, 0.0] and placed[f"{verifiedShort}_door1"][0] == [70.0, -30.0, 0.0]
  assert {value[2] for value in placed.values()} == {"zoneFile"}


def testACopiedTextureWhoseNameIsTakenIsStoredUnderItsArchivesName(stageBlenderServer, tmp_path):
  archivePath = tmp_path / "renamed.eqg"

  async def steps(session):
    await groundScene(session, tmp_path)
    await session.expectSuccess("placeObject", crystal | {"name": "crystal", "location": [10, 0, 0], "headingDegrees": 0})
    await ourBox(session, tmp_path, "ours", "crystal_colp_c", [60, -60, 0])
    await session.expectSuccess("saveFile", {"path": str(tmp_path / "renamed.blend")})
    return await session.expectSuccess("exportZone", {"path": str(archivePath), "purpose": "test"})

  exported = stageBlenderServer.session(steps)
  client = clientFiles("crystals.eqg", "obj_fscrystala.mod")
  clientModel = client["obj_fscrystala.mod"]
  archive = eqArchive.EQArchive(archivePath)
  stored = archive.read("obj_fscrystala.mod")
  renames = {"crystal_colp_c.dds": "crystals_crystal_colp_c.dds"}
  assert exported["failures"] == []
  # Ours keeps its name; the client's texture of that name is stored under its archive's, and the copied model names that instead.
  assert archive.read("crystal_colp_c.dds") != client["crystal_colp_c.dds"] and archive.read("crystals_crystal_colp_c.dds") == client["crystal_colp_c.dds"]
  assert all(archive.read(name) == data for name, data in client.items() if name.endswith(".dds") and name != "crystal_colp_c.dds")
  textures = lambda model: modelMeasures.eqgMeasures(eqgFiles.parseModel(model, "obj_fscrystala.mod"))["textures"]
  assert sorted(textures(stored)) == sorted(renames.get(name, name) for name in textures(clientModel))
  # Only the name changes: the new name is appended to the string table, and every vertex and triangle is the client's.
  assert len(stored) == len(clientModel) + len("crystals_crystal_colp_c.dds") + 1
  storedParsed, clientParsed = eqgFiles.parseModel(stored, "stored"), eqgFiles.parseModel(clientModel, "client")
  assert all(numpy.array_equal(storedParsed[key], clientParsed[key]) for key in ("vertices", "normals", "uvs", "triangles", "triangleMaterials", "triangleFlags"))
  manifest = json.loads((tmp_path / "renamed_export.json").read_text(encoding="ascii"))
  [copy] = [copy for copy in manifest["clientModels"] if copy["entry"] == "obj_fscrystala.mod"]
  assert copy["renamedTextures"] == renames and copy["sha256"] == hashlib.sha256(clientModel).hexdigest()
  assert copy["storedSha256"] == hashlib.sha256(stored).hexdigest()
  assert "crystals_crystal_colp_c.dds" in [texture["name"] for texture in copy["textures"]]
  # A rename the model does not name is refused.
  with pytest.raises(ValueError, match=r"no material names \['absent.dds'\]"):
    clientModelBundle.withTexturesRenamed(clientModel, {"absent.dds": "crystals_absent.dds"}, "obj_fscrystala.mod")


def testWhatCannotShipIsRefused(stageBlenderServer, tmp_path):
  startup = clientModelBundle.startupTextures(clientRoot)
  startupName = next(name for name in sorted(startup) if name.endswith(".dds"))

  async def steps(session):
    await groundScene(session, tmp_path)
    await session.expectSuccess("placeObject", crystal | {"name": "crystal", "location": [0, 0, 0], "headingDegrees": 0})
    await session.expectSuccess("placeDoor", crystal | {"name": "crystalRow", "location": [0, 0, 0], "headingDegrees": 0, "openType": 55})
    await session.expectSuccess("placeObject", kiln | {"name": "kiln", "location": [30, 0, 0], "headingDegrees": 0})
    await session.expectSuccess("transformObjects", {"names": ["kiln"], "rotateDegrees": [10, 0, 0]})
    await session.expectSuccess("placeObject", kiln | {"name": "floating", "location": [-30, 0, 5], "headingDegrees": 0})
    await session.expectSuccess("placeObject", {"zone": "neighborhood", "model": "IT67", "source": "gequip.s3d", "name": "classic", "location": [0, 40, 0], "headingDegrees": 0})
    await ourBox(session, tmp_path, "shadowedByStartup", startupName[:-4], [60, -50, 0])
    await session.expectSuccess("saveFile", {"path": str(tmp_path / "refused.blend")})
    checked = await session.expectSuccess("checkExport", {"path": str(tmp_path / "refused.eqg"), "purpose": "test"})
    refused = await session.expectError("exportZone", {"path": str(tmp_path / "refused.eqg"), "purpose": "test"})
    placementRefusals = {
      "noOpenType": await session.expectError("placeDoor", crystal | {"name": "noOpenType", "location": [0, 0, 0], "headingDegrees": 0}),
      "typeWithoutRow": await session.expectError("placeObject", crystal | {"name": "typed", "location": [0, 0, 0], "headingDegrees": 0, "objectType": "forge"}),
      "unknownType": await session.expectError("placeObject", kiln | {"name": "odd", "location": [0, 0, 0], "headingDegrees": 0, "objectType": "loom"}),
      "tooLarge": await session.expectError("placeObject", kiln | {"name": "huge", "location": [0, 0, 0], "headingDegrees": 0, "scale": 60}),
      "partPercent": await session.expectError("placeDoor", book | {"name": "part", "location": [0, 0, 0], "headingDegrees": 0, "scaleFactor": 100.5}),
    }
    return checked, refused, placementRefusals

  checked, refused, placementRefusals = stageBlenderServer.session(steps)
  failures = sorted((failure["failure"], failure.get("object") or failure.get("entry") or failure.get("texture")) for failure in checked["failures"])
  assert failures == sorted([
    ("placed as scenery and as a row", "crystalRow"), ("row cannot hold", "kiln"), ("client model not copied", "classic"),
    ("own texture shadowed", startupName),
  ])
  assert [finding["object"] for finding in checked["findings"] if finding["finding"] == "row moved by the server"] == ["floating"]
  assert "exportZone (test) refused, nothing written: 4 failure(s)" in refused and not (tmp_path / "refused.eqg").exists()
  assert "An interactive door's row needs its openType" in placementRefusals["noOpenType"]
  assert "objectType and displayName belong to an interactive object" in placementRefusals["typeWithoutRow"]
  assert "objectType is one of" in placementRefusals["unknownType"] and "'loom'" in placementRefusals["unknownType"]
  assert "An object row's scale is 0.01 to 49.99" in placementRefusals["tooLarge"]
  assert "A door row's size is a whole percent" in placementRefusals["partPercent"]


def testClientMeshesAreNeverEdited(stageBlenderServer, tmp_path):
  copyIntoCollection = """
crystal = bpy.data.objects['crystal']
kit = bpy.data.collections.new('kit')
stolen = crystal.copy()
del stolen['zonewrightClientContent']
del stolen['zonewrightClientModel']
kit.objects.link(stolen)
holder = bpy.data.objects.new('kitPlaced', None)
holder.instance_type = 'COLLECTION'
holder.instance_collection = kit
bpy.context.scene.collection.objects.link(holder)
"""

  async def steps(session):
    await groundScene(session, tmp_path)
    await session.expectSuccess("placeObject", crystal | {"name": "crystal", "location": [0, 0, 0], "headingDegrees": 0})
    await session.expectSuccess("createPrimitive", {"kind": "cube", "name": "block", "size": [20, 20, 20], "location": [0, 0, 0]})
    refusals = {
      "move": await session.expectError("moveVertices", {"objectName": "crystal", "selector": {"all": True}, "offset": [0, 0, 1]}),
      "join": await session.expectError("joinObjects", {"names": ["crystal", "block"], "into": "block"}),
      "cutUnion": await session.expectError("booleanCut", {"objectName": "block", "cutterName": "crystal", "operation": "UNION"}),
      "cutDifference": await session.expectError("booleanCut", {"objectName": "block", "cutterName": "crystal"}),
      "decimate": await session.expectError("decimate", {"objectName": "crystal", "ratio": 0.5}),
    }
    moved = await session.expectSuccess("transformObjects", {"names": ["crystal"], "translate": [5, 0, 0], "rotateDegrees": [0, 0, 30], "scale": [2, 2, 2]})
    await session.expectSuccess("deleteObjects", {"names": ["block"]})
    await session.expectSuccess("runPython", {"code": copyIntoCollection})
    await session.expectSuccess("saveFile", {"path": str(tmp_path / "edited.blend")})
    checked = await session.expectSuccess("checkExport", {"path": str(tmp_path / "edited.eqg"), "purpose": "test"})
    return refusals, moved, checked

  refusals, moved, checked = stageBlenderServer.session(steps)
  for action, message in refusals.items():
    assert "'crystal' is a client model" in message and "never edited" in message, action
  assert "booleanCut as the cutter" in refusals["cutUnion"] and "booleanCut as the cutter" in refusals["cutDifference"]
  assert moved
  copies = [failure for failure in checked["failures"] if failure["failure"] == "copy of a client model"]
  assert [(failure["object"], failure["members"]) for failure in copies] == [("kitPlaced", ["crystal.001"])]
