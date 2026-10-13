"""deployZone's request from a game export, read back as written, and the run of the ledger's zoneDeploy; the client's entry for a
new zone and its strings. The ledger side (zoneDeploy against the ledger's stand-ins) is tested in claudeperidot/tests."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from conftest import everquestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import eqClientZones
import exportPipeline
import pieceRows
import serverRows
import zoneDeployment

archiveBytes = b"the archive's bytes"
clientZones = eqClientZones.ClientZones([
  {"id": 2, "shortName": "qeynos2", "longName": "North Qeynos", "expansion": 0, "flags": 7, "eqstrID": 2244},
  {"id": 407, "shortName": "highpasshold", "longName": "Highpass Hold", "expansion": 0, "flags": 4, "eqstrID": 2247},
  {"id": 996, "shortName": "arttest", "longName": "Art Testing Domain", "expansion": 0, "flags": 0x800004, "eqstrID": 0},
])
zone = {
  "shortName": "testhollow", "zoneId": 901, "longName": "The Testers' Hollow", "timeType": "outdoor", "entryGate": "open",
  "serverTemplate": "highpasshold", "safePoint": [10.5, -20.25, 3, 45], "underworld": -100, "minClip": 60, "maxClip": 2000, "fogOn": False,
  "sky": "none", "eqstrID": 40001,
}
author = {"session": "session_0121Qm1vvZgAKaiV6dKmANxe", "agent": "a0206fa5316c5360e"}


def piece(name, table, row, position):
  return {"object": name, "position": position, "rotation": [0.0, 0.0, 0.0], "scale": 1.0, "row": {"table": table} | row}


pieces = [
  piece("door", "doors", {"name": "OBJ_CRDOOR", "destination": None, "triggerDoor": None, "openType": 0, "lockpick": 0, "keyItem": 0,
    "noKeyring": 0, "triggerType": 0, "closeTimerMilliseconds": 5000}, [10.0, 20.0, 0.0]),
  piece("kiln", "object", {"objectName": "IT10801_ACTORDEF", "type": 22, "displayName": "Kiln"}, [-5.0, 4.0, 0.0]),
]


def writeExport(folder, properties=zone, withPieces=True, waysIn=False):
  """A game export's files as exportZone writes them: the archive, server maps and rows, and its manifest."""
  short = properties["shortName"]
  folder.mkdir(parents=True, exist_ok=True)
  digest = hashlib.sha256(archiveBytes).hexdigest()
  line = {"name": "ATP_1_out", "number": 1, "label": "out", "minimum": [0, 0, 0], "maximum": [10, 10, 10],
    "target": {"zone": "highpasshold", "x": 1.0, "y": 2.0, "z": 3.0, "headingDegrees": 90}}
  entries = [{"name": "fromHighpass", "source": "placeEntry", "kind": "zoneIn", "fromZone": "highpasshold", "fromNumber": 20, "at": [1, 1, 0],
    "headingDegrees": 0}] if waysIn else []
  rows = serverRows.rowsFiles(properties, [line], entries, clientZones.idsByShortName(), None, [], digest)
  paths = exportPipeline.serverFilePaths(folder, short)
  contents = {folder / f"{short}.eqg": archiveBytes, paths["map"]: b"the map", paths["water"]: b"the water", paths["nav"]: b"the nav",
    paths["rows"]: rows["rows"].encode("ascii")}
  if withPieces:
    contents[paths["pieces"]] = pieceRows.piecesFile(properties, pieces, {"doors": [], "objects": []}, digest)["text"].encode("utf-8")
  if rows["waysIn"] is not None:
    contents[paths["waysIn"]] = rows["waysIn"].encode("ascii")
  for path, data in contents.items():
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
  manifest = {
    "purpose": "game", "writtenBy": "exportZone", "shortName": short, "failures": 0,
    "files": {path.relative_to(folder).as_posix(): {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for path, data in contents.items()},
    "zone": {key: properties[key] for key in ("shortName", "zoneId", "longName", "serverTemplate", "eqstrID")},
  }
  exportPipeline.manifestPath(folder, short).write_text(json.dumps(manifest), encoding="ascii")
  return folder / f"{short}.eqg"


def request(archive, target="claudeperidot"):
  return zoneDeployment.deployRequest(archive, clientZones, target, 20, author, "pending", "exploration")


def test_theRequestCarriesTheExportsRowsAsItsReadersReadThemAndItsMaps(tmp_path):
  archive = writeExport(tmp_path / "staging" / "testhollow")
  built, left = request(archive)
  assert built["zone"] == {"shortName": "testhollow", "zoneId": 901, "longName": "The Testers' Hollow", "template": "highpasshold"}
  folder = archive.parent
  rows = serverRows.readRows((folder / "server/sql/testhollow.sql").read_text(encoding="ascii"))
  assert built["rows"]["zone"] == {"written": rows["zoneInsert"]["written"], "copied": rows["zoneInsert"]["copied"]}
  assert built["rows"]["zone"]["written"]["safe_x"] == -20.25 and "ruleset" in built["rows"]["zone"]["copied"]
  assert [point["number"] for point in built["rows"]["zonePoints"]] == [10] and built["rows"]["zonePoints"][0]["target_zone_id"] == 407
  assert [(door["doorid"], door["name"], door["zone"]) for door in built["rows"]["doors"]] == [(1, "OBJ_CRDOOR", "testhollow")]
  assert [(item["objectname"], item["type"], item["zoneid"]) for item in built["rows"]["objects"]] == [("IT10801_ACTORDEF", 22, 901)]
  assert built["files"] == [
    {"path": "/opt/eqemu/akk-stack/server/maps/base/testhollow.map", "source": str(folder / "server/maps/base/testhollow.map"), "sha256": hashlib.sha256(b"the map").hexdigest()},
    {"path": "/opt/eqemu/akk-stack/server/maps/water/testhollow.wtr", "source": str(folder / "server/maps/water/testhollow.wtr"), "sha256": hashlib.sha256(b"the water").hexdigest()},
    {"path": "/opt/eqemu/akk-stack/server/maps/nav/testhollow.nav", "source": str(folder / "server/maps/nav/testhollow.nav"), "sha256": hashlib.sha256(b"the nav").hexdigest()},
  ]
  assert built["export"] == {"manifest": str(folder / "testhollow_export.json"), "archiveSHA256": hashlib.sha256(archiveBytes).hexdigest()}
  assert (built["command"], built["target"], built["planStep"], built["author"], built["ownerClearance"], built["disposition"]) == (
    "deploy", "claudeperidot", 20, author, "pending", "exploration")
  assert left == []


def test_aZoneWithoutPiecesDeploysNoDoorsOrObjectsAndTheWaysInAreLeftOut(tmp_path):
  archive = writeExport(tmp_path / "testhollow", withPieces=False, waysIn=True)
  built, left = request(archive)
  assert (built["rows"]["doors"], built["rows"]["objects"]) == ([], [])
  assert left == [{"file": str(archive.parent / "server/sql/testhollow_waysIn.sql"),
    "why": "it rewrites neighbours' zone_points rows, outside the zone; the owner decides those"}]


@pytest.mark.parametrize("spoil, message", [
  (lambda archive: (archive.parent / "server/maps/nav/testhollow.nav").write_bytes(b"another nav"), "is not the file the manifest lists"),
  (lambda archive: (archive.parent / "server/maps/base/stray.map").write_bytes(b"x"), "the export's server folder holds files its manifest does not list"),
  (lambda archive: (archive.parent / "server/sql/testhollow.sql").unlink(), "which the manifest lists, is gone"),
])
def test_anExportChangedSinceItWasWrittenIsRefused(tmp_path, spoil, message):
  archive = writeExport(tmp_path / "testhollow")
  spoil(archive)
  with pytest.raises(ValueError, match=message):
    request(archive)


def test_onlyANewZonesGameExportDeploysAndOnlyToClaudeperidot(tmp_path):
  archive = writeExport(tmp_path / "testhollow")
  with pytest.raises(ValueError, match="a deploy's target is one of \\['claudeperidot'\\]: Peridot is never written, got 'peridot'"):
    request(archive, "peridot")
  manifestPath = archive.parent / "testhollow_export.json"
  manifest = json.loads(manifestPath.read_text())
  manifestPath.write_text(json.dumps(manifest | {"writtenBy": "checkExport"}))
  with pytest.raises(ValueError, match="is not a game export exportZone wrote of testhollow with no failures"):
    request(archive)
  reused = writeExport(tmp_path / "arttest", zone | {"shortName": "arttest", "zoneId": 996})
  with pytest.raises(ValueError, match="arttest \\(zone 996\\) takes a slot the client registers"):
    request(reused)


def test_theClientsEntryForANewZoneFollowsEQEmusDocumentedRoute():
  strings = eqClientZones.readClientStrings("EQST0002\r\n0 3\r\n1 %1\r\n2244 North Qeynos\r\n2247 Highpass Hold\r\n")
  assert strings == {0: "3", 1: "%1", 2244: "North Qeynos", 2247: "Highpass Hold"}
  entry = eqClientZones.clientEntry(zone | {"longName": 'The "Hollow"'}, clientZones, strings)
  # The template's registration gives zoneType (its expansion, 0) and zoneFlags2 (4); a new string adds one to string 0's count.
  assert entry == {
    "zoneEntry": 'ZoneEntry(0, 901, "testhollow", "The \\"Hollow\\"", 40001, 4, 0, 0, 0),', "eqstrLine": '40001 The "Hollow"',
    "zoneTypeAndFlagsFrom": "highpasshold", "eqstrCountLine": "0 4",
  }
  assert "eqstrCountLine" not in eqClientZones.clientEntry(zone | {"eqstrID": 2247, "longName": "Highpass Hold"}, clientZones, strings)
  assert eqClientZones.clientEntry(zone | {"shortName": "arttest", "zoneId": 996}, clientZones, strings) is None
  with pytest.raises(ValueError, match="eqstr_us.txt line 3 is no '<id> <text>' line"):
    eqClientZones.readClientStrings("EQST0002\n0 1\nnot a line\n")


def test_theRealClientsStringsCountThemselves():
  strings = eqClientZones.loadClientStrings(Path(everquestClient))
  assert int(strings[0]) == len(strings) - 1
  assert strings[2247] == "Highpass Hold"


def test_aZoneDeployErrorComesBackAsAnError():
  with pytest.raises(ValueError, match="zoneDeploy failed: DeployError: command is slot, deploy or undeploy, got 'nothing'"):
    zoneDeployment.runZoneDeploy({"command": "nothing"}, lambda *arguments: None)
