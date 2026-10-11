import logging
import sys

import pytest
import yaml

import ledger
from ledgerDoubles import FakeTable, changedInCommit, gitHead, makeHarness, ruleFake


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


def seededFake():
  fake = ruleFake()
  fake.tables["character_data"] = FakeTable([("id", "int", "")], ("id",), [{"id": b"1"}])
  commits = {"akk-stack": "13f442d" + "0" * 33, "akk-stack/code": "ae4bd2833c0e6ff00b97e26789fc806341180260",
    "akk-stack/server/quests": "c327bed" + "1" * 33, "akk-stack/server/maps": "fbd3b19128" + "2" * 30}
  fake.shell = [
    (r"^ls -1t /opt/eqemu/baseline$", lambda command: "peq-20261012-0300.sql.gz\nnotes.txt\npeq-20261011-0300.sql.gz\n"),
    (r"^stat -c %Y /opt/eqemu/baseline/peq-20261012-0300\.sql\.gz$", lambda command: "1791774000\n"),
    (r"^sha256sum /opt/eqemu/baseline/peq-20261012-0300\.sql\.gz$", lambda command: "ab" * 32 + "  /opt/eqemu/baseline/x\n"),
  ]
  for suffix, commit in commits.items():
    fake.shell.append((rf"-C /opt/eqemu/{suffix} rev-parse HEAD$", lambda command, commit=commit: commit + "\n"))
  return fake, commits


def test_theBaselineRecordsTheDumpVersionsBinaryLogCommitsAndWatchedTableChecksums(repository):
  fake, commits = seededFake()
  harness = makeHarness(repository, fake)
  assert harness.ledger.seedBaseline() == ["baseline written: 1 table checksums; now take the Proxmox baseline snapshot"]
  assert yaml.safe_load((repository / "baseline.yaml").read_text()) == {
    "dump": {"path": "/opt/eqemu/baseline/peq-20261012-0300.sql.gz", "sha256": "ab" * 32, "time": "2026-10-12T03:00:00Z"},
    "dbVersion": {"version": 9325, "botsVersion": 0, "customVersion": 0},
    "binaryLog": {"file": "binlog.000002", "position": 4321},
    "deployed": {"akkStack": commits["akk-stack"], "code": commits["akk-stack/code"], "quests": commits["akk-stack/server/quests"],
      "maps": commits["akk-stack/server/maps"]},
    "tableChecksums": {"rule_values": fake.tableChecksum("rule_values")},
  }
  assert changedInCommit(repository) == {"claudeperidot/baseline.yaml"}
  assert harness.events[-1] == ("push", gitHead(repository))


def test_theBaselineNeedsTheBinaryLogOn(repository):
  fake, _ = seededFake()
  fake.masterStatus = []
  harness = makeHarness(repository, fake)
  with pytest.raises(ledger.LedgerError, match="the baseline needs one db_version row and the binary log on"):
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
