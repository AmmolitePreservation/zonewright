"""Files changes: host files installed from the workstation and removed, through the host stand-in's real shell over a folder
standing in for the host's /opt/eqemu, against real git repositories."""
import hashlib
import json

import pytest

import ledger
from ledgerDoubles import FakeMariaDB, changedInCommit, gitHead, makeHarness, planText, remoteHead, rowChangeFields, writeChange

maps = "/opt/eqemu/akk-stack/server/maps"


def sha(content):
  return hashlib.sha256(content).hexdigest()


def filesFields(entries, **overrides):
  fields = rowChangeFields(title="The test zone's maps are on the host", kind="files", scope=[entry["path"] for entry in entries],
    files=entries, activation="restart the zone processes")
  fields.update(overrides)
  return fields


def setUp(repository, host, tmp_path, entries):
  for kind in ("base", "water"):
    host.path(f"{maps}/{kind}").mkdir(parents=True, exist_ok=True)
  fake = FakeMariaDB()
  fake.host = host
  harness = makeHarness(repository, fake)
  writeChange(repository, "20261013-010000-testMaps", filesFields(entries))
  (repository / "plan.md").write_text(planText(["- [ ] 1. Maps [20261013-010000]", "- [ ] 2. By hand"]), encoding="utf-8")
  harness.commitAll()
  return harness


def source(tmp_path, name, content):
  path = tmp_path / "staging" / name
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_bytes(content)
  return path


def test_aFilesChangeInstallsNewAndReplacedFilesAndRevertPutsBackWhatWasThere(repository, host, tmp_path):
  newMap = bytes(range(256)) * 3
  newWater = b"WATR\x02\x00\x00\x00\r\n\x00"
  host.path(f"{maps}/water").mkdir(parents=True)
  host.path(f"{maps}/water/testzone.wtr").write_bytes(b"old water")
  entries = [
    {"path": f"{maps}/base/testzone.map", "source": str(source(tmp_path, "testzone.map", newMap)), "sha256": sha(newMap)},
    {"path": f"{maps}/water/testzone.wtr", "source": str(source(tmp_path, "testzone.wtr", newWater)), "sha256": sha(newWater)},
  ]
  harness = setUp(repository, host, tmp_path, entries)
  change = harness.ledger.find("20261013-010000")
  assert harness.ledger.state(change).label == "not applied; not staged"
  harness.ledger.stage("20261013-010000")
  saved = json.loads((change.folder / "saved.json").read_text())
  assert saved == {
    "before": {entries[0]["path"]: None, entries[1]["path"]: sha(b"old water")},
    "after": {entries[0]["path"]: sha(newMap), entries[1]["path"]: sha(newWater)},
    "kept": {entries[1]["path"]: "/opt/eqemu/ledger/20261013-010000-testMaps/previous/1"},
  }
  assert host.path("/opt/eqemu/ledger/20261013-010000-testMaps/previous/1").read_bytes() == b"old water"
  assert remoteHead(harness.remote) == gitHead(repository.parent)
  assert harness.ledger.apply("20261013-010000") == ["applied 20261013-010000-testMaps", "activation: restart the zone processes"]
  assert host.path(f"{maps}/base/testzone.map").read_bytes() == newMap
  assert host.path(f"{maps}/water/testzone.wtr").read_bytes() == newWater
  assert not host.path(f"{maps}/base/testzone.map.ledger").exists()
  assert not host.path("/opt/eqemu/ledger/20261013-010000-testMaps/incoming").exists()
  assert "- [x] 1. Maps [20261013-010000]" in (repository / "plan.md").read_text()
  assert harness.ledger.state(change).label == "applied"
  harness.ledger.revert("20261013-010000")
  assert not host.path(f"{maps}/base/testzone.map").exists()
  assert host.path(f"{maps}/water/testzone.wtr").read_bytes() == b"old water"
  assert harness.ledger.state(change).label == "not applied"
  assert "- [ ] 1. Maps [20261013-010000]" in (repository / "plan.md").read_text()


def test_aFilesChangeRemovesAFileAndRevertRestoresIt(repository, host, tmp_path):
  harness = setUp(repository, host, tmp_path, [{"path": f"{maps}/base/gone.map", "source": None, "sha256": None}])
  host.path(f"{maps}/base/gone.map").write_bytes(b"the old map")
  harness.ledger.stage("20261013-010000")
  harness.ledger.apply("20261013-010000")
  assert not host.path(f"{maps}/base/gone.map").exists()
  harness.ledger.revert("20261013-010000")
  assert host.path(f"{maps}/base/gone.map").read_bytes() == b"the old map"


def test_applyRefusesASourceThatChangedSinceItWasWrittenAndWritesNothing(repository, host, tmp_path):
  path = source(tmp_path, "testzone.map", b"map")
  harness = setUp(repository, host, tmp_path, [{"path": f"{maps}/base/testzone.map", "source": str(path), "sha256": sha(b"map")}])
  harness.ledger.stage("20261013-010000")
  path.write_bytes(b"another map")
  with pytest.raises(ledger.LedgerError, match="is not the file with SHA-256"):
    harness.ledger.apply("20261013-010000")
  assert not host.path(f"{maps}/base/testzone.map").exists()
  assert not (harness.ledger.find("20261013-010000").folder / "intent.json").exists()


def test_revertRefusesAFileChangedSinceApply(repository, host, tmp_path):
  path = source(tmp_path, "testzone.map", b"map")
  harness = setUp(repository, host, tmp_path, [{"path": f"{maps}/base/testzone.map", "source": str(path), "sha256": sha(b"map")}])
  harness.ledger.stage("20261013-010000")
  harness.ledger.apply("20261013-010000")
  host.path(f"{maps}/base/testzone.map").write_bytes(b"edited on the host")
  with pytest.raises(ledger.LedgerError, match=r"revert needs 20261013-010000-testMaps applied; it is partly applied \(0 of 1\); some files are neither"):
    harness.ledger.revert("20261013-010000")
  assert host.path(f"{maps}/base/testzone.map").read_bytes() == b"edited on the host"


def test_stageRefusesAChangeTheHostAlreadyHolds(repository, host, tmp_path):
  path = source(tmp_path, "testzone.map", b"map")
  harness = setUp(repository, host, tmp_path, [{"path": f"{maps}/base/testzone.map", "source": str(path), "sha256": sha(b"map")}])
  host.path(f"{maps}/base/testzone.map").write_bytes(b"map")
  with pytest.raises(ledger.LedgerError, match="is applied; stage saves the files of a change that has not run"):
    harness.ledger.stage("20261013-010000")
  assert "saved.json" not in {path.name for path in harness.ledger.find("20261013-010000").folder.iterdir()}


@pytest.mark.parametrize("entries, message", [
  ([{"path": "/opt/eqemu/akk-stack/server/quests/x.lua", "source": None, "sha256": None}], "a files scope entry is a host file under"),
  ([{"path": f"{maps}/base/../../x", "source": None, "sha256": None}], "a files scope entry is a host file under"),
  ([{"path": f"{maps}/base/a.map", "source": "C:\\a.map", "sha256": None}], "removes its file with source and sha256 both null"),
  ([{"path": f"{maps}/base/a.map", "source": "a.map", "sha256": "0" * 64}], "source is an absolute workstation path"),
  ([{"path": f"{maps}/base/a.map", "source": None, "sha256": None}] * 2, "names each path once"),
])
def test_filesChangeFieldsAreValidatedWhereTheyEnter(entries, message):
  assert any(message in problem for problem in ledger.validateChangeFields(filesFields(entries)))


def test_aFilesChangeIsNeverProduction():
  fields = filesFields([{"path": f"{maps}/base/a.map", "source": None, "sha256": None}], disposition="production",
    production="contentSQL")
  assert "a files change stays where it was made and cannot be production" in ledger.validateChangeFields(fields)


def test_stageCommitsTheSavedHashesBeforeAnythingIsWritten(repository, host, tmp_path):
  path = source(tmp_path, "testzone.map", b"map")
  harness = setUp(repository, host, tmp_path, [{"path": f"{maps}/base/testzone.map", "source": str(path), "sha256": sha(b"map")}])
  harness.ledger.stage("20261013-010000")
  assert changedInCommit(repository.parent) == {"claudeperidot/changes/20261013-010000-testMaps/saved.json"}
  assert not host.path(f"{maps}/base/testzone.map").exists()
