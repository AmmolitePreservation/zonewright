import datetime

import pytest

import ledger
from ledgerDoubles import (FakeTable, changedInCommit, cleared, expansionValue, gitHead, itemsFake, makeHarness, planText,
  questFields, rowChangeFields, rowFiles, ruleFake, rowsOf, schemaChangeFields, schemaFiles, setRule, writeChange)


def promoteHarness(repository, fields, fake=None, files=None, extra=None):
  harness = makeHarness(repository, fake if fake is not None else ruleFake())
  writeChange(repository, "0001-forPeridot", fields, files if files is not None else rowFiles())
  for name, otherFields in (extra or {}).items():
    writeChange(repository, name, otherFields, rowFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. The change [0001]"]), encoding="utf-8")
  harness.commitAll()
  return harness


productionRows = rowChangeFields(disposition="production", production="contentSQL", ownerClearance=cleared)


def test_promoteRefusesAChangeThatIsNotProduction(repository):
  harness = promoteHarness(repository, rowChangeFields())
  with pytest.raises(ledger.LedgerError, match="0001-forPeridot is exploration; only a production change is promoted"):
    harness.ledger.promote("0001")


def test_promoteRefusesAProductionChangeTheOwnerHasNotCleared(repository):
  harness = promoteHarness(repository, rowChangeFields(disposition="production", production="contentSQL"))
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  with pytest.raises(ledger.LedgerError, match="0001-forPeridot waits for the owner's clearance: ownerClearance names no "
      "message of the owner's"):
    harness.ledger.promote("0001")
  assert not (repository / "changes/0001-forPeridot/peridot").exists()


def test_promoteWaitsWhileASignificantProductionChangeAwaitsTheOwner(repository):
  pending = rowChangeFields(disposition="production", production="contentSQL", significance="significant")
  harness = promoteHarness(repository, productionRows, extra={"0002-bigOne": pending,
    "0003-smallOne": rowChangeFields(disposition="production", production="contentSQL"),
    "0004-experiment": rowChangeFields(significance="significant")})
  with pytest.raises(ledger.LedgerError, match="promote waits for the owner to clear the significant changes 0002-bigOne$"):
    harness.ledger.promote("0001")
  assert not (repository / "changes/0001-forPeridot/peridot").exists()


def test_promoteRefusesAChangeThatIsNotApplied(repository):
  harness = promoteHarness(repository, productionRows)
  harness.ledger.stage("0001")
  with pytest.raises(ledger.LedgerError, match="promote needs 0001-forPeridot applied; it is not applied"):
    harness.ledger.promote("0001")


def test_aRowChangeBecomesContentSQLThatOnlyRunsOnTheRowsItWasMadeAgainst(repository):
  harness = promoteHarness(repository, productionRows)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  assert harness.ledger.promote("0001") == ["promoted 0001-forPeridot: changes/0001-forPeridot/peridot"]
  assert changedInCommit(repository) == {"claudeperidot/changes/0001-forPeridot/peridot/content.sql",
    "claudeperidot/changes/0001-forPeridot/peridot/check.sql"}
  assert harness.remoteHead() == gitHead(repository)
  content = (repository / "changes/0001-forPeridot/peridot/content.sql").read_text()
  assert "Run it with the mysql command-line client (mysql peq < content.sql), whose DELIMITER command its guards use" in content
  peridot = ruleFake()
  ledger.Server(peridot).sql(content)
  assert expansionValue(peridot) == b"19"
  assert len(peridot.log) == 1
  edited = ruleFake()
  setRule(edited, 1, b"Test:Gone", b"changed on Peridot since the copy")
  before = rowsOf(edited, "rule_values")
  with pytest.raises(ledger.LedgerError, match="0001: Peridot rows differ from those the change was made against"):
    ledger.Server(edited).sql(content)
  assert rowsOf(edited, "rule_values") == before
  assert edited.log == []


def test_aRowChangeOnATableHoldingSecretsIsNeverPromoted(repository, host):
  fake = ruleFake()
  fake.host = host
  fake.tables["login_server_admins"] = FakeTable([("id", "int"), ("account_password", "varchar", "latin1")], ("id",),
    [{"id": 1, "account_password": b"hash"}])
  fake.predicates["id = 1"] = lambda row: row["id"] == 1
  fake.checks["SELECT 1 AS applied"] = lambda fake: 1
  fake.checks["SELECT 0 AS applied"] = lambda fake: 0
  harness = promoteHarness(repository, rowChangeFields(disposition="production", production="contentSQL",
    ownerClearance=cleared, scope=[{"table": "login_server_admins", "where": "id = 1"}]), fake=fake,
    files={"apply.sql": "DELETE FROM login_server_admins WHERE id = 1;\n", "check.sql": "SELECT 0 AS applied;\n"})
  harness.ledger.stage("0001")
  with pytest.raises(ledger.LedgerError, match="login_server_admins is a player, state, login or bot table, so promote never "
      "writes it"):
    harness.ledger.promote("0001")


def test_aSchemaChangeBecomesTheNextCustomMigration(repository):
  fields = schemaChangeFields(disposition="production", production="customMigration", ownerClearance=cleared)
  harness = promoteHarness(repository, fields, fake=itemsFake(), files=schemaFiles())
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  with pytest.raises(ledger.LedgerError, match="a custom migration needs --manifestVersion"):
    harness.ledger.promote("0001")
  harness.ledger.promote("0001", 2)
  assert (repository / "changes/0001-forPeridot/peridot/customMigration.cpp").read_text() == (
    "\tManifestEntry{\n"
    "\t\t.version = 2,\n"
    f"\t\t.description = \"{datetime.date.today():%Y_%m_%d}_forPeridot\",\n"
    "\t\t.check = \"SELECT applied FROM (SELECT COUNT(*) AS applied FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = "
    "DATABASE() AND TABLE_NAME = 'items' AND COLUMN_NAME IN ('a', 'b')) AS ledgerCheck WHERE applied = 2\",\n"
    "\t\t.condition = \"empty\",\n"
    "\t\t.match = \"\",\n"
    "\t\t.sql =\n"
    "\t\t\tR\"(ALTER TABLE items ADD COLUMN a INT;\n)\"\n"
    "\t\t\tR\"(ALTER TABLE items ADD COLUMN b INT;\n)\",\n"
    "\t\t.content_schema_update = true,\n"
    "\t},\n")


def test_aQuestChangeBecomesACherryPickOfItsCommits(repository, host):
  from testRepositoryChanges import questSetup
  setup = questSetup(repository, host, questFields(disposition="production", production="questCommit", ownerClearance=cleared))
  setup.harness.ledger.stage("0001")
  setup.harness.ledger.apply("0001")
  setup.harness.ledger.promote("0001")
  commits = " ".join(setup.commits)
  assert (repository / "changes/0001-questFix/peridot/cherryPick.sh").read_bytes() == (b"#!/bin/sh\nset -e\n"
    b"cd /opt/eqemu/akk-stack/server/quests\ngit cherry-pick " + commits.encode("ascii") + b"\n")
