import ctypes
import hashlib
import sqlite3
import subprocess
import uuid
from ctypes import wintypes
from pathlib import Path

import pytest

import ledger
from ledgerDoubles import changedInCommit, clientChangeFields, committedFiles, git, gitHead, makeHarness, planText, writeChange


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


class Credential(ctypes.Structure):
  _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


@pytest.fixture
def throwawayCredential():
  """A generic credential for this test session only, deleted afterwards; its password is a placeholder."""
  advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
  target = f"claudeperidotLedgerTest/{uuid.uuid4().hex}"
  blob = (ctypes.c_ubyte * 4)(*b"none")
  credential = Credential(Type=1, TargetName=target, CredentialBlobSize=4, CredentialBlob=blob, Persist=1,
    UserName="ledgerTest")
  if not advapi32.CredWriteW(ctypes.byref(credential), 0):
    raise OSError(ctypes.get_last_error(), "CredWriteW failed")
  yield target
  if not advapi32.CredDeleteW(target, 1, 0):
    raise OSError(ctypes.get_last_error(), "CredDeleteW failed")


def test_aCredentialCheckPassesOnlyWhileTheEntryExists(throwawayCredential):
  assert ledger.clientCheckPasses({"credential": throwawayCredential})
  assert not ledger.clientCheckPasses({"credential": throwawayCredential + "Missing"})


def loginDatabase(path, rows):
  connection = sqlite3.connect(path)
  connection.executescript("CREATE TABLE accounts (id integer primary key, account text not null, password text not null);"
    "CREATE TABLE characters (id integer primary key, character text not null, server text not null, account_id integer "
    "not null, unique (character, server));"
    "CREATE TABLE personas (id integer primary key, character_id integer not null, class text not null, level integer not "
    "null, last_seen text, unique (character_id, class));")
  connection.execute("INSERT INTO accounts (id, account, password) VALUES (1, 'claudeAccount', 'sealed')")
  for index, (character, server, hasPersona) in enumerate(rows, start=1):
    connection.execute("INSERT INTO characters (id, character, server, account_id) VALUES (?, ?, ?, 1)", (index, character,
      server))
    if hasPersona:
      connection.execute("INSERT INTO personas (character_id, class, level) VALUES (?, 'WAR', 1)", (index,))
  connection.commit()
  connection.close()


def test_aLoginDBCheckPassesForAPersonaRowOfThatCharacterOnThatServer(tmp_path):
  path = tmp_path / "login.db"
  loginDatabase(path, [("claudette", "claudeperidot", True), ("pariator", "peridot", True),
    ("nopersona", "claudeperidot", False)])

  def passes(server, character):
    return ledger.clientCheckPasses({"path": str(path), "server": server, "character": character})

  assert passes("claudeperidot", "Claudette")
  assert not passes("peridot", "Claudette")
  assert not passes("claudeperidot", "Pariator")
  assert not passes("claudeperidot", "Nopersona")
  assert not ledger.clientCheckPasses({"path": str(tmp_path / "absent.db"), "server": "a", "character": "b"})
  (tmp_path / "notADatabase.db").write_text("plain text", encoding="utf-8")
  with pytest.raises(ledger.LedgerError, match="cannot be read as MacroQuest's login.db"):
    ledger.clientCheckPasses({"path": str(tmp_path / "notADatabase.db"), "server": "a", "character": "b"})


def test_aPathUnderTheProfileIsExpanded():
  assert ledger.expandClientPath("~\\LocalProjects\\x") == str(Path.home()) + "\\LocalProjects\\x"
  assert ledger.expandClientPath("S:\\Research") == "S:\\Research"


def test_aStepsScriptNeverReachesACommandLine(tmp_path):
  seen = tmp_path / "commandLine.txt"
  ledger.runPowerShell(f"$marker = 'stepSecret' + '{uuid.uuid4().hex}'\n"
    f"[IO.File]::WriteAllText('{seen}', [Environment]::CommandLine + \"`n\" + $marker)")
  commandLine, marker = seen.read_text(encoding="utf-8").rsplit("\n", 1)
  assert marker.startswith("stepSecret")
  assert marker not in commandLine
  assert "stepSecret" not in commandLine


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
  assert changedInCommit(repository) == {"claudeperidot/plan.md", "claudeperidot/changes/0001-writeNote/intent.json"}
  assert "- [x] 1. Write the note [0001]" in (repository / "plan.md").read_text()
  assert harness.remoteHead() == gitHead(repository)
  assert harness.connections == []
  harness.ledger.revert("0001")
  assert not note.exists()
  assert "- [ ] 1. Write the note [0001]" in (repository / "plan.md").read_text()
  assert git(repository, "log", "-1", "--format=%s").strip() == "Revert 0001-writeNote: A test file exists"


def test_theIntentReachesTheRemoteBeforeAStepRuns(repository, tmp_path):
  harness, note = clientHarness(repository, tmp_path)
  seen = tmp_path / "remoteAtStep.txt"
  remote = harness.remote
  steps = [f"git -C '{remote}' show claude:claudeperidot/changes/0001-writeNote/intent.json | Set-Content -LiteralPath '{seen}'",
    f"[IO.File]::WriteAllText('{note}', 'hello')"]
  (repository / "changes/0001-writeNote/change.yaml").unlink()
  (repository / "changes/0001-writeNote").rmdir()
  digest = hashlib.sha256(b"hello").hexdigest()
  writeChange(repository, "0001-writeNote", clientChangeFields(scope=[str(note)], steps=steps,
    undo=[f"Remove-Item -LiteralPath '{note}'"], checks=[{"path": str(note), "sha256": digest}]))
  harness.commitAll("steps that look at the remote")
  harness.ledger.apply("0001")
  assert '"intent": "applying"' in seen.read_text(encoding="utf-8")


def test_aClientApplyInterruptedAfterItsStepsIsFinishedByApplyAgain(repository, tmp_path):
  harness, note = clientHarness(repository, tmp_path)
  realRecord = harness.ledger.record
  calls = []

  def record(paths, message):
    calls.append(message)
    if message.startswith("Apply "):
      raise ledger.LedgerError("the session ended before the record")
    realRecord(paths, message)

  harness.ledger.record = record
  with pytest.raises(ledger.LedgerError, match="the session ended before the record"):
    harness.ledger.apply("0001")
  harness.ledger.record = realRecord
  git(repository, "checkout", "--", ".")
  assert note.read_bytes() == b"hello"
  assert harness.ledger.apply("0001")[0] == "applied 0001-writeNote (finished the interrupted apply)"
  assert "claudeperidot/changes/0001-writeNote/intent.json" not in committedFiles(repository)


def test_revertRefusesAClientChangeWhoseFilesChangedSinceApply(repository, tmp_path):
  harness, note = clientHarness(repository, tmp_path)
  harness.ledger.apply("0001")
  note.write_bytes(b"edited by hand")
  with pytest.raises(ledger.LedgerError, match="revert needs every check of 0001-writeNote passing, as apply left it; it is "
      "not applied, so what it touched changed since apply"):
    harness.ledger.revert("0001")
  assert note.read_bytes() == b"edited by hand"


def test_aFailingStepFailsApplyAndLeavesTheIntentRecorded(repository, tmp_path):
  harness, note = clientHarness(repository, tmp_path, steps=["throw 'the step cannot run'"])
  with pytest.raises(ledger.LedgerError, match=r"^a PowerShell step failed \(exit 1\): the step cannot run"):
    harness.ledger.apply("0001")
  assert git(repository, "log", "-1", "--format=%s").strip() == "Applying 0001-writeNote: A test file exists"
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
