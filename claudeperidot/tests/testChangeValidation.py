import copy
from pathlib import Path

import pytest

import ledger
from ledgerDoubles import cleared, clientChangeFields, questFields, rowChangeFields, writeChange

realLedger = Path(ledger.__file__).resolve().parent.parent


def withField(fields, name, value):
  changed = copy.deepcopy(fields)
  changed[name] = value
  return changed


def withoutField(fields, name):
  changed = copy.deepcopy(fields)
  del changed[name]
  return changed


def labFields(**overrides):
  fields = rowChangeFields(title="A lab change", kind="lab", scope=["/root/.gitconfig on Peridot"], disposition="fixture",
    ownerClearance=cleared, activation="none", steps=[{"via": "facet run", "host": "peridot", "run": "true"}],
    undo=[{"via": "facet run", "host": "peridot", "run": "true"}],
    checks=[{"via": "facet run", "host": "peridot", "run": "true", "expect": "exit 0"}])
  fields.update(overrides)
  return fields


def configFields(**overrides):
  fields = questFields(kind="config", scope=["eqemu_config.json"], repository="/opt/eqemu/config", rendered=[{
    "template": "eqemu_config.json", "file": "/opt/eqemu/akk-stack/server/eqemu_config.json",
    "filledKeys": ["server.database.password"]}], render="/opt/eqemu/config/render.sh")
  fields.update(overrides)
  return fields


def test_theTemplateAndTheRecordedChangesAreValid():
  changes = ledger.loadChanges(realLedger)
  numbered = [change.number for change in changes if len(change.number) == 4]
  assert numbered == [f"{number:04}" for number in range(1, len(numbered) + 1)]
  assert all(len(change.number) == 15 for change in changes[len(numbered):])
  assert all(change.fields["author"]["session"].startswith("session_") for change in changes)
  assert {(change.fields["disposition"], change.fields["production"]) for change in changes} <= {("fixture", "none"), ("exploration", "none")}
  assert all(ledger.validateChangeFields(change.fields) == [] for change in changes)
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
  credential = clientChangeFields(checks=[{"credential": "claudeperidot/eqLogin"}])
  persona = clientChangeFields(checks=[{"path": "~\\LocalProjects\\mqPeridot\\build\\bin\\release\\config\\login.db",
    "server": "claudeperidot", "character": "Claudette"}])
  exports = clientChangeFields(checks=[{"path": "~\\LocalProjects\\mqPeridot\\build\\bin\\release\\MQ2Main.dll",
    "exports": "SetFrameLimiterSuspended"}])
  for fields in (rowChangeFields(), schema, production, questFields(), config, configFields(), clientChangeFields(), credential,
      persona, exports, labFields(), rowChangeFields(ownerClearance=cleared)):
    assert ledger.validateChangeFields(fields) == []


@pytest.mark.parametrize("fields, problem", [
  (rowChangeFields(kind="code"), "kind must be one of db, quest, config, client, lab, not 'code'"),
  (rowChangeFields(disposition="undecided"), "disposition must be one of fixture, exploration, production, not 'undecided'"),
  (rowChangeFields(significance="major"), "significance must be one of minor, significant, not 'major'"),
  (rowChangeFields(ownerClearance="cleared"), "ownerClearance is pending, or {ownerMessage: the owner's message"),
  (rowChangeFields(ownerClearance={"ownerMessage": ""}), "Claude never clears a change on its own judgment"),
  (rowChangeFields(ownerClearance={"clearedBy": "Claude"}), "Claude never clears a change on its own judgment"),
  (withoutField(rowChangeFields(), "why"), "missing why"),
  (withoutField(withoutField(rowChangeFields(), "scope"), "commits"), "missing scope, commits"),
  (withoutField(rowChangeFields(), "author"), "missing author"),
  (rowChangeFields(author="Claude"), "author must be a mapping of session and, for a subagent, agent"),
  (rowChangeFields(author={"agent": "a2b9b798809b906e3"}), "author must be a mapping of session and, for a subagent, agent"),
  (rowChangeFields(author={"session": "0121Qm1vvZgAKaiV6dKmANxe", "agent": "a2b9b798"}), "author session, agent malformed"),
  (withoutField(clientChangeFields(), "undo"), "missing undo"),
  (withoutField(questFields(), "source"), "missing source"),
  (withField(rowChangeFields(), "notes", "x"), "unknown fields notes"),
  (withField(rowChangeFields(), "steps", ["x"]), "unknown fields steps"),
  (rowChangeFields(title="two\nlines"), "title must be one non-empty line"),
  (rowChangeFields(production="contentSQL"), "exploration changes never reach Peridot: production must be none"),
  (rowChangeFields(disposition="fixture", production="contentSQL"), "fixture changes never reach Peridot"),
  (rowChangeFields(disposition="production"), "a production row change reaches Peridot as contentSQL"),
  (rowChangeFields(disposition="production", production="customMigration"), "reaches Peridot as contentSQL"),
  (rowChangeFields(scope=[{"table": "items"}], schemaSteps=1, disposition="production", production="contentSQL"),
    "a production schema change reaches Peridot as customMigration"),
  (clientChangeFields(disposition="production", production="runbook"), "a client change stays where it was made"),
  (labFields(disposition="production", production="runbook"), "a lab change stays where it was made"),
  (rowChangeFields(commits=["abcdef1"]), "a db change has no commits"),
  (questFields(commits=[]), "a quest change names its commits, oldest first"),
  (questFields(commits=["NOTASHA"]), "commits must be a list of lowercase commit SHAs"),
  (questFields(repository="quests"), "repository must be the checkout's absolute path on the host"),
  (questFields(source="relative\\clone"), "source must be the workstation clone the commits are made in"),
  (questFields(scope=["../escape.lua"]), "a quest scope entry is a file path inside the repository"),
  (configFields(render=None), "rendered and render go together"),
  (configFields(render="render.sh; rm -rf /"), "render must be one host command"),
  (configFields(rendered=[{"template": "other.json", "file": "/opt/x.json", "filledKeys": ["a"]}]),
    "a rendered entry is a scope template"),
  (configFields(rendered=[{"template": "eqemu_config.json", "file": "/opt/x.json", "filledKeys": []}]),
    "a rendered entry is a scope template"),
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
  (clientChangeFields(checks=[{"credential": "two words"}]), "a credential check names a Credential Manager target"),
  (clientChangeFields(checks=[{"path": "C:\\login.db", "server": "claudeperidot"}]), "is not one of"),
  (clientChangeFields(checks=[{"path": "C:\\MQ2Main.dll", "exports": "two words"}]), "is not one of"),
  (labFields(checks=[{"via": "facet run", "host": "peridot", "run": "true"}]), "with expect"),
  (labFields(steps=[{"via": "ssh", "host": "peridot", "run": "true"}]), "via: facet run or sonicwall"),
  (labFields(commits=["abcdef1"]), "a lab change has no commits"),
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


def test_timestampedChangesSortAfterNumberedOnesAndPlanStepsNameThem(repository):
  writeChange(repository, "20261012-163005-later", rowChangeFields())
  writeChange(repository, "0030-numbered", rowChangeFields())
  writeChange(repository, "20261012-091500-earlier", rowChangeFields())
  changes = ledger.loadChanges(repository)
  assert [(change.number, change.name) for change in changes] == [
    ("0030", "numbered"), ("20261012-091500", "earlier"), ("20261012-163005", "later")]
  writeChange(repository, "20261012-1630-short", rowChangeFields())
  with pytest.raises(ledger.LedgerError, match="20261012-1630-short is not a change folder name"):
    ledger.loadChanges(repository)
  [step] = ledger.readPlanSteps("- [ ] 4. Both kinds [0030, 20261012-163005]")
  assert step.changes == ("0030", "20261012-163005")


def test_aChangeFolderWithoutChangeYAMLIsRefused(repository):
  (repository / "changes" / "0003-empty").mkdir()
  with pytest.raises(ledger.LedgerError, match="0003-empty has no change.yaml"):
    ledger.loadChanges(repository)


@pytest.mark.parametrize("table, columns, reason", [
  ("account", ["id", "status"], "account is a player, state, login or bot table"),
  ("login_api_tokens", ["id", "token"], "login_api_tokens is a player, state, login or bot table"),
  ("discord_webhooks", ["id", "webhook_url"], "discord_webhooks is a player, state, login or bot table"),
  ("variables", ["varname", "value"], None),
  ("spire_settings", ["id", "apiKey"], "spire_settings.apiKey is named like a secret"),
  ("tool_logins", ["id", "admin_password"], "tool_logins.admin_password is named like a secret"),
  ("hooks", ["id", "slackWebhookURL"], "hooks.slackWebhookURL is named like a secret"),
  ("doors", ["id", "keyitem", "nokeyring"], None),
])
def test_tablesHoldingSecretsOrPersonalDataAreKnownByGroupAndColumnName(table, columns, reason):
  assert ledger.sensitiveReason(table, columns) == reason


def test_securityTablesAreWatchedThoughPlayTablesAndRuntimeTablesAreNot():
  assert [ledger.isWatched(table) for table in ("rule_values", "login_api_tokens", "gm_ips", "account", "character_data",
    "eqtime", "spawn_events", "chatchannels", "items")] == [True, True, True, False, False, False, False, False, True]
