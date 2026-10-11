import hashlib
import subprocess
from pathlib import Path

import pytest

import ledger
from ledgerDoubles import changedInCommit, clientChangeFields, git, gitHead, makeHarness, planText, writeChange


def junction(link, target):
  subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_existenceChecksTellFilesDirectoriesAndJunctionsApart(tmp_path):
  folder = tmp_path / "folder"
  folder.mkdir()
  other = tmp_path / "other"
  other.mkdir()
  note = folder / "note.txt"
  note.write_text("x", encoding="utf-8")
  link = tmp_path / "link"
  junction(link, folder)

  def passes(**check):
    return ledger.clientCheckPasses({key: str(value) for key, value in check.items()})

  assert passes(path=note, exists="file")
  assert not passes(path=folder, exists="file")
  assert passes(path=folder, exists="directory")
  assert not passes(path=note, exists="directory")
  assert not passes(path=link, exists="directory")
  assert passes(path=link, exists="junction", target=folder)
  assert not passes(path=link, exists="junction", target=other)
  assert not passes(path=folder, exists="junction", target=folder)
  assert not passes(path=tmp_path / "missing", exists="file")


def test_aHashCheckPassesOnlyForThoseExactBytes(tmp_path):
  note = tmp_path / "eqhost.txt"
  note.write_bytes(b"[LoginServer]\r\nHost=claudeperidot.strata.unseencolor.com:5999\r\n")
  digest = hashlib.sha256(note.read_bytes()).hexdigest()
  assert ledger.clientCheckPasses({"path": str(note), "sha256": digest})
  note.write_bytes(b"[LoginServer]\nHost=claudeperidot.strata.unseencolor.com:5999\n")
  assert not ledger.clientCheckPasses({"path": str(note), "sha256": digest})
  assert not ledger.clientCheckPasses({"path": str(tmp_path / "missing.txt"), "sha256": digest})


def test_anIniCheckReadsTheKeyInItsSectionOnly(tmp_path):
  ini = tmp_path / "eqclient.ini"
  ini.write_bytes(b"[Defaults]\r\nSound=FALSE\r\nMaximized=0\r\n[Options]\r\nMaxBGFPS=80\r\nSound=TRUE\r\nName=caf\xe9\r\n")

  def value(section, key, expected):
    return ledger.clientCheckPasses({"path": str(ini), "section": section, "key": key, "value": expected})

  assert value("Defaults", "Sound", "FALSE")
  assert value("defaults", "sound", "FALSE")
  assert not value("Defaults", "Sound", "false")
  assert value("Options", "Sound", "TRUE")
  assert value("Options", "MaxBGFPS", "80")
  assert not value("Defaults", "MaxBGFPS", "80")
  assert value("Options", "Name", "caf\u00e9")
  assert not value("Options", "Missing", "")
  assert not ledger.clientCheckPasses({"path": str(tmp_path / "none.ini"), "section": "A", "key": "B", "value": ""})


def test_aPathUnderTheProfileIsExpanded():
  assert ledger.expandClientPath("~\\LocalProjects\\x") == str(Path.home()) + "\\LocalProjects\\x"
  assert ledger.expandClientPath("S:\\Research") == "S:\\Research"


def clientHarness(repository, tmp_path, steps=None):
  note = tmp_path / "workstation" / "note.txt"
  note.parent.mkdir()
  digest = hashlib.sha256(b"hello").hexdigest()
  harness = makeHarness(repository)
  writeChange(repository, "0001-writeNote", clientChangeFields(scope=[str(note)],
    steps=steps or [f"[IO.File]::WriteAllText('{note}', 'hello')"], undo=[f"Remove-Item -LiteralPath '{note}'"],
    checks=[{"path": str(note), "sha256": digest}]))
  (repository / "plan.md").write_text(planText(["- [ ] 1. Write the note [0001]", "- [ ] 2. By hand"]), encoding="utf-8")
  harness.commitAll()
  return harness, note


def test_aClientChangeRunsItsStepsAndUndoInPowerShellAndRecordsEach(repository, tmp_path):
  harness, note = clientHarness(repository, tmp_path)
  harness.ledger.stage("0001")
  assert harness.ledger.apply("0001") == ["applied 0001-writeNote", "activation: none"]
  assert note.read_bytes() == b"hello"
  assert changedInCommit(repository) == {"claudeperidot/plan.md"}
  assert "- [x] 1. Write the note [0001]" in (repository / "plan.md").read_text()
  assert harness.events[-1] == ("push", gitHead(repository))
  assert harness.connections == []
  harness.ledger.revert("0001")
  assert not note.exists()
  assert "- [ ] 1. Write the note [0001]" in (repository / "plan.md").read_text()
  assert git(repository, "log", "-1", "--format=%s").strip() == "Revert 0001-writeNote: A test file exists"


def test_revertRefusesAClientChangeWhoseFilesChangedSinceApply(repository, tmp_path):
  harness, note = clientHarness(repository, tmp_path)
  harness.ledger.apply("0001")
  note.write_bytes(b"edited by hand")
  with pytest.raises(ledger.LedgerError, match="revert needs every check of 0001-writeNote passing, as apply left it; it is "
      "not applied, so what it touched changed since apply"):
    harness.ledger.revert("0001")
  assert note.read_bytes() == b"edited by hand"


def test_aFailingStepFailsApplyAndRecordsNothing(repository, tmp_path):
  harness, note = clientHarness(repository, tmp_path, steps=["throw 'the step cannot run'"])
  head = gitHead(repository)
  with pytest.raises(ledger.LedgerError, match=r"^a PowerShell step failed \(exit 1\): the step cannot run\r?\nAt line:4 "):
    harness.ledger.apply("0001")
  assert gitHead(repository) == head
  assert not note.exists()


def test_applyRefusesAClientChangeThatIsPartlyInPlace(repository, tmp_path):
  note = tmp_path / "note.txt"
  marker = tmp_path / "marker.txt"
  marker.write_text("already here", encoding="utf-8")
  harness = makeHarness(repository)
  writeChange(repository, "0001-twoFiles", clientChangeFields(scope=[str(note), str(marker)],
    steps=[f"Set-Content -LiteralPath '{note}' -Value 'x'; Set-Content -LiteralPath '{marker}' -Value 'y'"],
    checks=[{"path": str(note), "exists": "file"}, {"path": str(marker), "exists": "file"}]))
  (repository / "plan.md").write_text(planText(["- [ ] 1. Both files [0001]"]), encoding="utf-8")
  harness.commitAll()
  with pytest.raises(ledger.LedgerError, match=r"apply needs 0001-twoFiles not applied; it is partly applied \(1 of 2\)"):
    harness.ledger.apply("0001")
  assert not note.exists()
  assert marker.read_text(encoding="utf-8") == "already here"
