"""Rows of tables that hold secrets or personal data stay on claudeperidot: only their digests are committed and pushed."""
import json

import pytest

import ledger
from ledgerDoubles import (FakeMariaDB, FakeSQLError, FakeTable, committedFiles, git, makeHarness, planText, rowChangeFields,
  rowsOf, writeChange)

secretToken = "ammoliteToken-9c2d41"
gmUpdate = "UPDATE account SET status = 255 WHERE id = 7"
gmCheck = "SELECT status = 255 AS applied FROM account WHERE id = 7"


def accountFake(host):
  fake = FakeMariaDB()
  fake.host = host
  fake.tables["account"] = FakeTable([("id", "int"), ("name", "varchar", "utf8mb4"), ("password", "varchar", "latin1"),
    ("status", "int")], ("id",), [{"id": 7, "name": b"claudeTester", "password": secretToken.encode(), "status": 0},
    {"id": 8, "name": b"someoneElse", "password": b"theirs", "status": 0}])
  fake.predicates["id = 7"] = lambda row: row["id"] == 7
  fake.mutations[gmUpdate] = lambda fake: fake.tables["account"].rows[0].update(status=255)
  fake.checks[gmCheck] = lambda fake: int(fake.tables["account"].rows[0]["status"] == 255)
  return fake


def gmHarness(repository, host):
  fake = accountFake(host)
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-gmAccount", rowChangeFields(title="Claude's account has status 255", scope=[{"table": "account",
    "where": "id = 7"}], disposition="fixture"), {"apply.sql": gmUpdate + ";\n", "check.sql": gmCheck + ";\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 13. GM fixture [0001]"]), encoding="utf-8")
  harness.commitAll()
  return harness, fake


def everythingPushed(harness):
  return "".join(git(harness.remote, "show", commit) for commit in git(harness.remote, "rev-list", "claude").split())


def test_aGMFixtureOnTheAccountTableKeepsItsSavedRowsOnTheHost(repository, host):
  harness, fake = gmHarness(repository, host)
  assert harness.ledger.stage("0001") == ["staged 0001-gmAccount: committed and pushed before it runs",
    "account is a player, state, login or bot table: its saved rows and revert.sql stay on claudeperidot in "
    "/opt/eqemu/ledger/0001-gmAccount; only their digests are committed"]
  saved = json.loads((repository / "changes/0001-gmAccount/saved.json").read_text())
  assert saved["sensitive"] is True
  assert "rows" not in saved["tables"][0]
  assert saved["tables"][0]["rowCount"] == 1
  assert "claudeperidot/changes/0001-gmAccount/revert.sql" not in committedFiles(repository)
  hostSaved = host.path("/opt/eqemu/ledger/0001-gmAccount/saved.json").read_text(encoding="utf-8")
  hostRevert = host.path("/opt/eqemu/ledger/0001-gmAccount/revert.sql").read_text(encoding="utf-8")
  assert saved["hostCopy"]["savedSHA256"] == ledger.hashlib.sha256(hostSaved.encode()).hexdigest()
  assert json.loads(hostSaved)["tables"][0]["rows"] == [["V37", "V" + b"claudeTester".hex().upper(),
    "V" + secretToken.encode().hex().upper(), "V30"]]
  assert secretToken in hostRevert
  pushed = everythingPushed(harness)
  assert secretToken not in pushed and secretToken.encode().hex().upper() not in pushed


def test_theGMFixtureAppliesShowsAppliedAndReverts(repository, host):
  harness, fake = gmHarness(repository, host)
  before = rowsOf(fake, "account")
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  assert fake.tables["account"].rows[0]["status"] == 255
  assert harness.ledger.status()[3] == "0001-gmAccount  db      fixture      applied"
  harness.ledger.revert("0001")
  assert rowsOf(fake, "account") == before
  pushed = everythingPushed(harness)
  assert secretToken not in pushed and secretToken.encode().hex().upper() not in pushed


def test_applyAndRevertRefuseAHostCopyThatIsNotTheOneStageWrote(repository, host):
  harness, fake = gmHarness(repository, host)
  harness.ledger.stage("0001")
  hostSaved = host.path("/opt/eqemu/ledger/0001-gmAccount/saved.json")
  original = hostSaved.read_bytes()
  hostSaved.write_bytes(original + b" ")
  with pytest.raises(ledger.LedgerError, match="0001-gmAccount: the saved rows on claudeperidot are not the ones stage wrote"):
    harness.ledger.apply("0001")
  assert harness.fake.writeScripts == []
  hostSaved.write_bytes(original)
  harness.ledger.apply("0001")
  hostRevert = host.path("/opt/eqemu/ledger/0001-gmAccount/revert.sql")
  hostRevert.write_text(hostRevert.read_text(encoding="utf-8").replace("0)", "1)"), encoding="utf-8", newline="\n")
  with pytest.raises(ledger.LedgerError, match="0001-gmAccount: revert.sql on claudeperidot is not the one stage wrote"):
    harness.ledger.revert("0001")
  assert fake.tables["account"].rows[0]["status"] == 255


def test_anErrorFromASensitiveChangeNeverEchoesItsValues(repository, host):
  fake = FakeMariaDB()
  fake.host = host
  fake.tables["login_api_tokens"] = FakeTable([("id", "int"), ("token", "varchar", "latin1")], ("id",),
    [{"id": 1, "token": secretToken.encode()}])
  fake.predicates["id = 1"] = lambda row: row["id"] == 1
  copy = "INSERT INTO login_api_tokens (id, token) SELECT 2, token FROM login_api_tokens WHERE id = 1"

  def duplicate(fake):
    raise FakeSQLError(1062, "23000", f"Duplicate entry '{secretToken}' for key 'token'")

  fake.mutations[copy] = duplicate
  fake.checks["SELECT 1 AS applied"] = lambda fake: 1
  fake.checks["SELECT 0 AS applied"] = lambda fake: 0
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-tokens", rowChangeFields(scope=[{"table": "login_api_tokens", "where": "id = 1"}]),
    {"apply.sql": copy + ";\n", "check.sql": "SELECT 0 AS applied;\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 1. Tokens [0001]"]), encoding="utf-8")
  harness.commitAll()
  harness.ledger.stage("0001")
  with pytest.raises(ledger.LedgerError, match=r"ERROR 1062 \(23000\) at line \d+: Duplicate entry '\?' for key '\?'") as raised:
    harness.ledger.apply("0001")
  assert secretToken not in str(raised.value)
  pushed = everythingPushed(harness)
  assert secretToken not in pushed and secretToken.encode().hex().upper() not in pushed


def test_aTableWithAColumnNamedLikeASecretIsKeptOnTheHostToo(repository, host):
  fake = FakeMariaDB()
  fake.host = host
  fake.tables["spire_settings"] = FakeTable([("id", "int"), ("apiKey", "varchar", "latin1")], ("id",),
    [{"id": 1, "apiKey": secretToken.encode()}])
  fake.predicates["id = 1"] = lambda row: row["id"] == 1
  fake.checks["SELECT 0 AS applied"] = lambda fake: 0
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-spire", rowChangeFields(scope=[{"table": "spire_settings", "where": "id = 1"}]),
    {"apply.sql": "DELETE FROM spire_settings WHERE id = 1;\n", "check.sql": "SELECT 0 AS applied;\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 1. Spire [0001]"]), encoding="utf-8")
  harness.commitAll()
  assert harness.ledger.stage("0001")[1].startswith("spire_settings.apiKey is named like a secret: its saved rows")
  assert secretToken not in everythingPushed(harness)
