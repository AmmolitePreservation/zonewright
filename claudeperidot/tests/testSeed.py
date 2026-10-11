import datetime
import hashlib
import logging
import os
import sys

import pytest
import yaml

import ledger
from ledgerDoubles import FakeTable, changedInCommit, git, gitHead, makeHarness, ruleFake


def test_seedRunsTheCopyScriptAndFailsLoudWithIt(repository, tmp_path):
  harness = makeHarness(repository)
  marker = tmp_path / "copied.txt"
  script = tmp_path / "copyDatabase.py"
  script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('streamed')\n", encoding="utf-8")
  assert harness.ledger.seedCopy(script) == ["copied: boot world once (its migrations and rule injection), then run seed "
    "--baseline"]
  assert marker.read_text() == "streamed"
  failing = tmp_path / "failingCopy.py"
  failing.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
  with pytest.raises(ledger.LedgerError, match="the copy script failingCopy.py failed with exit code 3"):
    harness.ledger.seedCopy(failing)
  shell = tmp_path / "copy.sh"
  shell.write_text("true\n", encoding="utf-8")
  with pytest.raises(ledger.LedgerError, match="the copy script must be .ps1 or .py, not copy.sh"):
    harness.ledger.seedCopy(shell)
  assert harness.connections == []


def test_seedRefusesOnceThereIsABaseline(repository, tmp_path):
  harness = makeHarness(repository)
  (repository / "baseline.yaml").write_text("dump: {}\n", encoding="utf-8")
  with pytest.raises(ledger.LedgerError, match="baseline.yaml exists: claudeperidot is seeded"):
    harness.ledger.seedCopy(sys.executable)
  with pytest.raises(ledger.LedgerError, match="baseline.yaml exists already"):
    harness.ledger.seedBaseline()


def seededHost(host):
  """The host's dumps (the newest is peq-20261012-0300.sql.gz) and the four checkouts the baseline records."""
  dumps = host.path("/opt/eqemu/baseline")
  dumps.mkdir(parents=True)
  for name, stamp, content in (("peq-20261011-0300.sql.gz", 1791687600, b"older"), ("peq-20261012-0300.sql.gz", 1791774000,
      b"newest dump"), ("notes.txt", 1791860400, b"not a dump")):
    (dumps / name).write_bytes(content)
    os.utime(dumps / name, (stamp, stamp))
  commits = {}
  for name, path in (("akkStack", "/opt/eqemu/akk-stack"), ("code", "/opt/eqemu/akk-stack/code"),
      ("quests", "/opt/eqemu/akk-stack/server/quests"), ("maps", "/opt/eqemu/akk-stack/server/maps")):
    folder = host.path(path)
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init", "-q")
    git(folder, "-c", "user.name=Host", "-c", "user.email=host@test.invalid", "commit", "-q", "--allow-empty", "-m", name)
    commits[name] = gitHead(folder)
  return commits


def seededFake(host):
  fake = ruleFake()
  fake.host = host
  fake.tables["character_data"] = FakeTable([("id", "int")], ("id",), [{"id": 1}])
  fake.tables["eqtime"] = FakeTable([("id", "int")], ("id",), [{"id": 1}])
  fake.tables["login_api_tokens"] = FakeTable([("id", "int"), ("token", "varchar", "latin1")], ("id",), [{"id": 1,
    "token": b"live token"}])
  return fake


def test_theBaselineRecordsTheDumpVersionsBinaryLogCommitsAndWatchedTableChecksums(repository, host):
  commits = seededHost(host)
  fake = seededFake(host)
  harness = makeHarness(repository, fake)
  assert harness.ledger.seedBaseline() == ["baseline written: 2 table checksums; now take the Proxmox baseline snapshot"]
  assert yaml.safe_load((repository / "baseline.yaml").read_text()) == {
    "dump": {"path": "/opt/eqemu/baseline/peq-20261012-0300.sql.gz", "sha256": hashlib.sha256(b"newest dump").hexdigest(),
      "time": datetime.datetime.fromtimestamp(1791774000, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
    "dbVersion": {"version": 9325, "botsVersion": 0, "customVersion": 0},
    "binaryLog": {"file": "mysql-bin.000002", "position": fake.nextPosition},
    "deployed": commits,
    "tableChecksums": {"login_api_tokens": fake.tableChecksum("login_api_tokens"), "rule_values": fake.tableChecksum(
      "rule_values")},
  }
  assert changedInCommit(repository) == {"claudeperidot/baseline.yaml"}
  assert harness.remoteHead() == gitHead(repository)


@pytest.mark.parametrize("settings, binaryLogOn", [(["1", "MIXED", "1"], True), (["1", "ROW", "0"], True),
  (["0", "ROW", "1"], True), (["1", "ROW", "1"], False)])
def test_theBaselineNeedsARowFormatBinaryLogWithAnnotatedRows(repository, host, settings, binaryLogOn):
  seededHost(host)
  fake = seededFake(host)
  fake.serverSettings = settings
  fake.binaryLogOn = binaryLogOn
  harness = makeHarness(repository, fake)
  with pytest.raises(ledger.LedgerError, match="binlog_format ROW|the binary log must be on"):
    harness.ledger.seedBaseline()
  assert not (repository / "baseline.yaml").exists()


@pytest.fixture
def detachLedgerLog():
  rootLogger = logging.getLogger()
  handlers = list(rootLogger.handlers)
  level = rootLogger.level
  yield
  for handler in rootLogger.handlers[len(handlers):]:
    handler.close()
    rootLogger.removeHandler(handler)
  rootLogger.setLevel(level)


def test_theCommandLineReportsALedgerErrorAndExitsNonZero(tmp_path, monkeypatch, capsys, detachLedgerLog):
  monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
  assert ledger.main(["apply", "9999"]) == 1
  assert capsys.readouterr().err.startswith("ledger: no change 9999 in ")
  assert (tmp_path / "claudeperidot" / "logs" / "ledger.log").read_text(encoding="utf-8").strip().endswith(
    "ERROR ledger apply failed: no change 9999 in " + str(ledger.ledgerFolder / "changes"))


def test_theLedgerPushesOnlyAWorkingBranch(tmp_path):
  from ledgerDoubles import makeRepository, remoteHead
  remote = makeRepository(tmp_path / "onMain", branch="main")
  with pytest.raises(ledger.LedgerError, match="the ledger pushes only a working branch such as claude, and HEAD is main"):
    ledger.pushBranch(tmp_path / "onMain")
  assert remoteHead(remote, "main") is None
  remote = makeRepository(tmp_path / "onClaude")
  ledger.pushBranch(tmp_path / "onClaude")
  assert remoteHead(remote) == gitHead(tmp_path / "onClaude")
