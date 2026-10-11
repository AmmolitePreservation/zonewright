import copy
from pathlib import Path

import pytest

import ledger
from ledgerDoubles import clientChangeFields, questFields, rowChangeFields, writeChange

realLedger = Path(ledger.__file__).resolve().parent.parent


def withField(fields, name, value):
  changed = copy.deepcopy(fields)
  changed[name] = value
  return changed


def withoutField(fields, name):
  changed = copy.deepcopy(fields)
  del changed[name]
  return changed


def test_theTemplateAndTheRecordedChangesAreValid():
  changes = ledger.loadChanges(realLedger)
  assert [change.identifier for change in changes] == ["0001-housingClientCopy", "0002-housingClientLoginHost",
    "0003-housingClientLargeAddress", "0004-housingClientSettings", "0005-mqClaudeLinkPlugin"]
  assert {(change.kind, change.fields["disposition"], change.fields["production"]) for change in changes} == {
    ("client", "fixture", "none")}
  template = realLedger / "changes" / "template"
  assert ledger.validateChangeFields(ledger.readYAML(template / "change.yaml")) == []
  assert [ledger.firstKeyword(statement) for statement in ledger.splitStatements((template / "apply.sql").read_text())] == [
    "INSERT"]
  assert [ledger.firstKeyword(statement) for statement in ledger.splitStatements((template / "check.sql").read_text())] == [
    "SELECT"]


def test_wellFormedChangesOfEveryFormHaveNoProblems():
  schema = rowChangeFields(scope=[{"table": "items"}], schemaSteps=2, disposition="production", production="customMigration")
  production = rowChangeFields(disposition="production", production="contentSQL", significance="significant")
  config = questFields(kind="config", disposition="production", production="runbook")
  for fields in (rowChangeFields(), schema, production, questFields(), config, clientChangeFields()):
    assert ledger.validateChangeFields(fields) == []


@pytest.mark.parametrize("fields, problem", [
  (rowChangeFields(kind="code"), "kind must be one of db, quest, config, client, not 'code'"),
  (rowChangeFields(disposition="undecided"), "disposition must be one of fixture, exploration, production, not 'undecided'"),
  (rowChangeFields(significance="major"), "significance must be one of minor, significant, not 'major'"),
  (rowChangeFields(ownerClearance="maybe"), "ownerClearance must be one of pending, cleared, not 'maybe'"),
  (withoutField(rowChangeFields(), "why"), "missing why"),
  (withoutField(withoutField(rowChangeFields(), "scope"), "commits"), "missing scope, commits"),
  (withoutField(clientChangeFields(), "undo"), "missing undo"),
  (withField(rowChangeFields(), "notes", "x"), "unknown fields notes"),
  (withField(rowChangeFields(), "steps", ["x"]), "unknown fields steps"),
  (rowChangeFields(title="two\nlines"), "title must be one non-empty line"),
  (rowChangeFields(production="contentSQL"), "exploration changes never reach Peridot: production must be none"),
  (rowChangeFields(disposition="fixture", production="contentSQL"), "fixture changes never reach Peridot"),
  (rowChangeFields(disposition="production"), "a production row change reaches Peridot as contentSQL"),
  (rowChangeFields(disposition="production", production="customMigration"), "reaches Peridot as contentSQL"),
  (rowChangeFields(scope=[{"table": "items"}], schemaSteps=1, disposition="production", production="contentSQL"),
    "a production schema change reaches Peridot as customMigration"),
  (clientChangeFields(disposition="production", production="runbook"), "a client change stays on the workstation"),
  (rowChangeFields(commits=["abcdef1"]), "a db change has no commits"),
  (questFields(commits=[]), "a quest change names its commits, oldest first"),
  (questFields(commits=["NOTASHA"]), "commits must be a list of lowercase commit SHAs"),
  (questFields(repository="quests"), "repository must be the checkout's absolute path on the host"),
  (questFields(scope=["../escape.lua"]), "a quest scope entry is a file path inside the repository"),
  (rowChangeFields(scope=[{"table": "rule_values"}]), "a db scope entry is a table name and a where predicate"),
  (rowChangeFields(scope=[{"table": "rule`values", "where": "1"}]), "a db scope entry is a table name and a where predicate"),
  (rowChangeFields(scope=[]), "scope must be a non-empty list"),
  (rowChangeFields(scope=[{"table": "items", "where": "id = 1"}], schemaSteps=1), "a schema scope entry is a table name"),
  (rowChangeFields(scope=[{"table": "items"}], schemaSteps=0), "schemaSteps must be a whole number of steps, at least 1"),
  (rowChangeFields(scope=[{"table": "items"}], schemaSteps=True), "schemaSteps must be a whole number of steps, at least 1"),
  (clientChangeFields(scope=["relative\\path"]), "a client scope entry is a path that is absolute or starts with ~\\"),
  (clientChangeFields(steps=[]), "steps must be a non-empty list of PowerShell scripts"),
  (clientChangeFields(checks=[{"path": "C:\\x", "sha256": "AB" * 32}]), "is not one of"),
  (clientChangeFields(checks=[{"path": "C:\\x", "exists": "junction"}]), "is not one of"),
  (clientChangeFields(checks=[{"path": "x", "exists": "file"}]), "a check needs a path that is absolute or starts with ~\\"),
])
def test_aChangeWithAProblemIsRefusedNamingIt(fields, problem):
  problems = ledger.validateChangeFields(fields)
  assert any(problem in found for found in problems), problems


def test_loadChangeNamesEveryProblemOfTheFolder(repository):
  writeChange(repository, "0007-twoProblems", rowChangeFields(disposition="undecided", notes="x"))
  with pytest.raises(ledger.LedgerError) as raised:
    ledger.loadChanges(repository)
  assert str(raised.value) == ("0007-twoProblems/change.yaml: unknown fields notes; disposition must be one of fixture, "
    "exploration, production, not 'undecided'")


def test_aDuplicateKeyInChangeYAMLIsRefused(repository):
  folder = repository / "changes" / "0001-duplicate"
  folder.mkdir()
  (folder / "change.yaml").write_text("title: one\ntitle: two\n", encoding="utf-8")
  with pytest.raises(ledger.LedgerError, match="duplicate key 'title' on line 2"):
    ledger.loadChanges(repository)


def test_changeFolderNamesAndNumbersAreChecked(repository):
  writeChange(repository, "0001-first", rowChangeFields())
  writeChange(repository, "template", {"anything": "goes"})
  assert [change.identifier for change in ledger.loadChanges(repository)] == ["0001-first"]
  writeChange(repository, "0001-second", rowChangeFields())
  with pytest.raises(ledger.LedgerError, match="0001-first and 0001-second share number 0001"):
    ledger.loadChanges(repository)
  (repository / "changes" / "0001-second" / "change.yaml").unlink()
  (repository / "changes" / "0001-second").rmdir()
  writeChange(repository, "0002-bad_name", rowChangeFields())
  with pytest.raises(ledger.LedgerError, match="0002-bad_name is not a change folder name"):
    ledger.loadChanges(repository)


def test_aChangeFolderWithoutChangeYAMLIsRefused(repository):
  (repository / "changes" / "0003-empty").mkdir()
  with pytest.raises(ledger.LedgerError, match="0003-empty has no change.yaml"):
    ledger.loadChanges(repository)
