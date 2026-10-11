import json

import pytest

import ledger
from ledgerDoubles import (FakeTable, changedInCommit, committedFiles, expansionCheck, expansionUpdate, expansionValue, git,
  gitHead, itemsFake, makeHarness, planText, rowChangeFields, rowFiles, ruleFake, rowsOf, schemaChangeFields,
  schemaFiles, schemaReverts, schemaSteps, setRule, writeChange)

changeFolder = "changes/0001-expansionTest"


def rowHarness(repository, fake=None, files=None, fields=None):
  harness = makeHarness(repository, fake if fake is not None else ruleFake())
  writeChange(repository, "0001-expansionTest", fields or rowChangeFields(), files or rowFiles())
  (repository / "plan.md").write_text(planText(["- [x] 0. Read Peridot", "- [ ] 1. Set the expansion [0001]",
    "- [ ] 2. Something by hand"]), encoding="utf-8")
  harness.commitAll()
  return harness


def lastMessage(folder):
  return git(folder, "log", "-1", "--format=%s").strip()


def committedJSON(folder, path, revision="HEAD"):
  return json.loads(git(folder, "show", f"{revision}:claudeperidot/{path}"))


def pushHook(harness, action):
  """Runs action after each push of a commit whose change folder holds intent.json: the moment between the write-ahead
  record reaching the remote and the database write."""
  realPush = harness.ledger.push

  def push(folder):
    realPush(folder)
    if f"claudeperidot/{changeFolder}/intent.json" in committedFiles(folder):
      action()

  harness.ledger.push = push


def test_stageSavesTheRowsWritesRevertAndPushesBeforeAnythingRuns(repository):
  harness = rowHarness(repository)
  before = rowsOf(harness.fake, "rule_values")
  assert harness.ledger.stage("0001") == ["staged 0001-expansionTest: committed and pushed before it runs"]
  assert harness.fake.writeScripts == []
  assert rowsOf(harness.fake, "rule_values") == before
  assert changedInCommit(repository) == {f"claudeperidot/{changeFolder}/saved.json", f"claudeperidot/{changeFolder}/revert.sql"}
  assert lastMessage(repository) == "Stage 0001-expansionTest: Expansion:CurrentExpansion is 19 in ruleset 1"
  assert harness.remoteHead() == gitHead(repository)
  saved = json.loads((repository / changeFolder / "saved.json").read_text())
  assert saved["sensitive"] is False
  assert [bytes.fromhex(row[1][1:]) for row in saved["tables"][0]["rows"]] == [b"Expansion:CurrentExpansion", b"Test:Gone",
    b"Test:Quoted"]
  assert saved["tables"][0]["digest"] == ledger.tableDigest(saved["tables"][0]["rows"])
  assert saved["checksum"] == ledger.scopeChecksum(saved["tables"])


def test_applyPushesItsIntentBeforeTheDatabaseWriteAndRecordsItsResult(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  assert harness.ledger.apply("0001") == ["applied 0001-expansionTest", "activation: #reload rules"]
  write = next(event for event in harness.events if event[0] == "write")
  assert write[1] == write[2]
  assert committedJSON(repository, f"{changeFolder}/intent.json", write[1]) == {"intent": "applying"}
  assert git(repository, "log", "-1", "--format=%s", write[1]).strip() == ("Applying 0001-expansionTest: "
    "Expansion:CurrentExpansion is 19 in ruleset 1")
  assert expansionValue(harness.fake) == b"19"
  names = {row["rule_name"] for row in harness.fake.tables["rule_values"].rows if row["ruleset_id"] == 1}
  assert names == {b"Expansion:CurrentExpansion", b"Test:New", b"Test:Quoted"}
  assert expansionValue(harness.fake, 2) == b"7"
  assert changedInCommit(repository) == {f"claudeperidot/{changeFolder}/applied.json",
    f"claudeperidot/{changeFolder}/intent.json", "claudeperidot/plan.md", "claudeperidot/tableChecksums.json"}
  assert f"claudeperidot/{changeFolder}/intent.json" not in committedFiles(repository)
  assert lastMessage(repository) == "Apply 0001-expansionTest: Expansion:CurrentExpansion is 19 in ruleset 1"
  assert harness.remoteHead() == gitHead(repository)
  assert committedJSON(repository, "tableChecksums.json") == {"rule_values": {"checksum": harness.fake.tableChecksum(
    "rule_values"), "by": "apply 0001-expansionTest"}}
  plan = (repository / "plan.md").read_text()
  assert "- [x] 1. Set the expansion [0001]" in plan
  assert ledger.planSection(plan, "In progress") == "2. Something by hand"
  assert ledger.planSection(plan, "Next") == "None."


def test_everyWriteTheLedgerMakesCarriesItsDomainAndItsChangesTag(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  harness.ledger.revert("0001")
  transactions = ledger.parseBinaryLog(harness.fake.renderBinaryLog(0))
  assert [transaction.domain for transaction in transactions] == [7, 7]
  assert [sorted({key[3] for key in transaction.writes}) for transaction in transactions] == [["0001"], ["0001"]]


def test_applyIsWholeOrNothingWhenTheCheckFails(repository):
  harness = rowHarness(repository)
  harness.fake.checks[expansionCheck] = lambda fake: 0
  harness.ledger.stage("0001")
  before = rowsOf(harness.fake, "rule_values")
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 check is not 1 after apply"):
    harness.ledger.apply("0001")
  assert rowsOf(harness.fake, "rule_values") == before
  assert harness.fake.log == []
  assert not (repository / changeFolder / "applied.json").exists()
  assert committedJSON(repository, f"{changeFolder}/intent.json") == {"intent": "applying"}
  assert "- [ ] 1. Set the expansion [0001]" in (repository / "plan.md").read_text()


def test_aRevertAfterAnApplyThatRolledBackIsRefusedNotFinished(repository):
  harness = rowHarness(repository)
  harness.fake.checks[expansionCheck] = lambda fake: 0
  harness.ledger.stage("0001")
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 check is not 1 after apply"):
    harness.ledger.apply("0001")
  with pytest.raises(ledger.LedgerError, match="revert needs 0001-expansionTest applied; it is not applied"):
    harness.ledger.revert("0001")
  assert committedJSON(repository, f"{changeFolder}/intent.json") == {"intent": "applying"}
  assert lastMessage(repository) == "Applying 0001-expansionTest: Expansion:CurrentExpansion is 19 in ruleset 1"


def test_applyRefusesRowsChangedSinceStage(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  setRule(harness.fake, 1, b"Test:Gone", b"edited by a GM")
  with pytest.raises(ledger.LedgerError, match="changed since stage, so revert.sql would put back stale rows"):
    harness.ledger.apply("0001")
  assert harness.fake.writeScripts == []


def test_aRowEditedAfterApplysOwnCheckIsCaughtInsideTheTransaction(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  pushHook(harness, lambda: setRule(harness.fake, 1, b"Test:Gone", b"edited by a GM"))
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 rows changed since stage"):
    harness.ledger.apply("0001")
  assert expansionValue(harness.fake) == b"4"
  assert {row["rule_name"]: row["rule_value"] for row in harness.fake.tables["rule_values"].rows
    if row["ruleset_id"] == 1}[b"Test:Gone"] == b"edited by a GM"


def test_applyRefusesToWriteARowOutsideItsScope(repository):
  fake = ruleFake()
  outside = "UPDATE rule_values SET rule_value = '99' WHERE ruleset_id = 2"
  fake.mutations[outside] = lambda fake: setRule(fake, 2, b"Expansion:CurrentExpansion", b"99")
  harness = rowHarness(repository, fake, {"apply.sql": f"{expansionUpdate};\n{outside};\n", "check.sql": expansionCheck + ";\n"})
  harness.ledger.stage("0001")
  before = rowsOf(fake, "rule_values")
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 apply wrote rows outside its scope"):
    harness.ledger.apply("0001")
  assert rowsOf(fake, "rule_values") == before


def test_applyRefusesToMoveARowOutOfItsScope(repository):
  fake = ruleFake()
  move = "UPDATE rule_values SET ruleset_id = 3 WHERE ruleset_id = 1 AND rule_name = 'Test:Gone'"
  fake.mutations[move] = lambda fake: [row.update(ruleset_id=3) for row in fake.tables["rule_values"].rows
    if row["rule_name"] == b"Test:Gone"]
  harness = rowHarness(repository, fake, {"apply.sql": f"{expansionUpdate};\n{move};\n", "check.sql": expansionCheck + ";\n"})
  harness.ledger.stage("0001")
  before = rowsOf(fake, "rule_values")
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 apply wrote rows outside its scope"):
    harness.ledger.apply("0001")
  assert rowsOf(fake, "rule_values") == before


def test_applyRefusesAnUnstagedChangeAndUncommittedEdits(repository):
  harness = rowHarness(repository)
  with pytest.raises(ledger.LedgerError, match="0001-expansionTest is not staged: run stage 0001 first"):
    harness.ledger.apply("0001")
  harness.ledger.stage("0001")
  applySQL = repository / changeFolder / "apply.sql"
  applySQL.write_text(applySQL.read_text() + "DELETE FROM rule_values;\n")
  with pytest.raises(ledger.LedgerError, match="has uncommitted edits"):
    harness.ledger.apply("0001")
  git(repository, "checkout", "--", f"{changeFolder}/apply.sql")
  plan = repository / "plan.md"
  plan.write_text(plan.read_text() + "\nA note not yet committed.\n")
  with pytest.raises(ledger.LedgerError, match="has uncommitted edits"):
    harness.ledger.apply("0001")
  assert harness.fake.writeScripts == []
  git(repository, "checkout", "--", "plan.md")
  harness.ledger.apply("0001")
  with pytest.raises(ledger.LedgerError, match="apply needs 0001-expansionTest not applied; it is applied"):
    harness.ledger.apply("0001")
  assert len(harness.fake.writeScripts) == 1


def test_stageRefusesAChangeThatIsAlreadyApplied(repository):
  harness = rowHarness(repository)
  harness.fake.checks[expansionCheck] = lambda fake: 1
  with pytest.raises(ledger.LedgerError, match="0001-expansionTest is applied; not staged; stage saves the rows of a change that "
      "has not run"):
    harness.ledger.stage("0001")
  assert not (repository / changeFolder / "saved.json").exists()


def test_revertPutsBackExactlyTheSavedRowsAndUnticksThePlan(repository):
  harness = rowHarness(repository)
  before = rowsOf(harness.fake, "rule_values")
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  assert harness.ledger.revert("0001") == ["reverted 0001-expansionTest", "activation: #reload rules"]
  assert rowsOf(harness.fake, "rule_values") == before
  write = [event for event in harness.events if event[0] == "write"][-1]
  assert committedJSON(repository, f"{changeFolder}/intent.json", write[2]) == {"intent": "reverting"}
  assert not (repository / changeFolder / "applied.json").exists()
  assert f"claudeperidot/{changeFolder}/applied.json" not in committedFiles(repository)
  assert f"claudeperidot/{changeFolder}/intent.json" not in committedFiles(repository)
  assert committedJSON(repository, "tableChecksums.json")["rule_values"] == {"checksum": harness.fake.tableChecksum(
    "rule_values"), "by": "revert 0001-expansionTest"}
  assert lastMessage(repository) == "Revert 0001-expansionTest: Expansion:CurrentExpansion is 19 in ruleset 1"
  plan = (repository / "plan.md").read_text()
  assert "- [ ] 1. Set the expansion [0001]" in plan
  assert ledger.planSection(plan, "In progress") == "1. Set the expansion [0001]"
  assert ledger.planSection(plan, "Next") == "2. Something by hand"
  assert harness.remoteHead() == gitHead(repository)


def test_revertsCheckGuardRollsBackARevertTheCheckStillCallsApplied(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  applied = rowsOf(harness.fake, "rule_values")
  harness.fake.checks[expansionCheck] = lambda fake: 1
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 check is not 0 after revert"):
    harness.ledger.revert("0001")
  assert rowsOf(harness.fake, "rule_values") == applied


def test_revertRefusesRowsChangedSinceApply(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  setRule(harness.fake, 1, b"Test:New", b"edited by a GM")
  edited = rowsOf(harness.fake, "rule_values")
  head = gitHead(repository)
  with pytest.raises(ledger.LedgerError, match=r"the rows of 0001-expansionTest changed since apply \(rule_values\); revert "
      "refuses to overwrite them"):
    harness.ledger.revert("0001")
  assert len(harness.fake.writeScripts) == 1
  assert rowsOf(harness.fake, "rule_values") == edited
  assert gitHead(repository) == head


def test_aRowEditedAfterRevertsOwnCheckIsCaughtInsideTheTransaction(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  pushHook(harness, lambda: setRule(harness.fake, 1, b"Test:New", b"edited by a GM"))
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 rows changed since apply"):
    harness.ledger.revert("0001")
  assert expansionValue(harness.fake) == b"19"
  assert {row["rule_name"]: row["rule_value"] for row in harness.fake.tables["rule_values"].rows
    if row["ruleset_id"] == 1}[b"Test:New"] == b"edited by a GM"


def test_aRevertWhoseRowsChangeRightAfterItsCommitIsNotRecordedAsClean(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  harness.fake.afterWrite.append(lambda fake: setRule(fake, 1, b"Test:Gone", b"edited the moment revert committed"))
  with pytest.raises(ledger.LedgerError, match="0001-expansionTest: revert committed, but the rows differ from those saved at "
      "stage"):
    harness.ledger.revert("0001")
  assert (repository / changeFolder / "applied.json").exists()
  assert committedJSON(repository, f"{changeFolder}/intent.json") == {"intent": "reverting"}


def test_revertRefusesAChangeThatIsNotApplied(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  with pytest.raises(ledger.LedgerError, match="revert needs 0001-expansionTest applied; it is not applied"):
    harness.ledger.revert("0001")


def dropAfterTheWrite(fake):
  """Makes the SSH connection drop right after the next write script runs, as an interruption after COMMIT would."""
  realExecute = fake.execute
  dropped = {"now": False}

  def execute(command, standardInput=b"", timeoutSeconds=60):
    if dropped["now"]:
      return 255, b"", b"ssh: connection reset"
    result = realExecute(command, standardInput, timeoutSeconds)
    if b"gtid_domain_id" in standardInput:
      dropped["now"] = True
    return result

  fake.execute = execute
  return lambda: fake.__dict__.pop("execute")


def test_anApplyInterruptedAfterItsCommitIsFinishedByRunningApplyAgain(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  reconnect = dropAfterTheWrite(harness.fake)
  with pytest.raises(ledger.LedgerError, match="exited 255"):
    harness.ledger.apply("0001")
  reconnect()
  assert expansionValue(harness.fake) == b"19"
  assert not (repository / changeFolder / "applied.json").exists()
  assert any(line.endswith("an interrupted apply (intent.json): run apply 0001 again to finish it")
    for line in harness.ledger.status())
  with pytest.raises(ledger.LedgerError, match="has an interrupted apply \\(intent.json\\): run apply 0001 again"):
    harness.ledger.revert("0001")
  assert harness.ledger.apply("0001") == ["applied 0001-expansionTest (finished the interrupted apply)",
    "activation: #reload rules"]
  assert len(harness.fake.writeScripts) == 1
  assert f"claudeperidot/{changeFolder}/intent.json" not in committedFiles(repository)
  assert "- [x] 1. Set the expansion [0001]" in (repository / "plan.md").read_text()
  harness.ledger.revert("0001")
  assert expansionValue(harness.fake) == b"4"


def test_aRevertInterruptedAfterItsCommitIsFinishedByRunningRevertAgain(repository):
  harness = rowHarness(repository)
  before = rowsOf(harness.fake, "rule_values")
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  reconnect = dropAfterTheWrite(harness.fake)
  with pytest.raises(ledger.LedgerError, match="exited 255"):
    harness.ledger.revert("0001")
  reconnect()
  assert rowsOf(harness.fake, "rule_values") == before
  with pytest.raises(ledger.LedgerError, match="has an interrupted revert \\(intent.json\\): run revert 0001 again"):
    harness.ledger.apply("0001")
  assert harness.ledger.revert("0001")[0] == "reverted 0001-expansionTest (finished the interrupted revert)"
  assert len(harness.fake.writeScripts) == 2
  assert {f"claudeperidot/{changeFolder}/applied.json", f"claudeperidot/{changeFolder}/intent.json"} \
    & committedFiles(repository) == set()
  assert "- [ ] 1. Set the expansion [0001]" in (repository / "plan.md").read_text()


def test_aRowWithIDZeroComesBackAsZero(repository):
  fake = ruleFake()
  fake.tables["zone_points"] = FakeTable([("id", "int", "", (), 0, 0, True), ("number", "int")], ("id",),
    [{"id": 0, "number": 5}, {"id": 1, "number": 6}])
  fake.predicates["number < 10"] = lambda row: row["number"] < 10
  update = "UPDATE zone_points SET number = 9 WHERE id = 0"
  fake.mutations[update] = lambda fake: fake.tables["zone_points"].rows[0].update(number=9)
  check = "SELECT COUNT(*) AS applied FROM zone_points WHERE id = 0 AND number = 9"
  fake.checks[check] = lambda fake: int(any(row["id"] == 0 and row["number"] == 9 for row in fake.tables["zone_points"].rows))
  harness = rowHarness(repository, fake, {"apply.sql": update + ";\n", "check.sql": check + ";\n"},
    rowChangeFields(scope=[{"table": "zone_points", "where": "number < 10"}]))
  before = rowsOf(fake, "zone_points")
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  harness.ledger.revert("0001")
  assert rowsOf(fake, "zone_points") == before


def test_stageRefusesATableThatIsNotInnoDB(repository):
  fake = ruleFake()
  fake.tables["rule_values"].engine = "MyISAM"
  harness = rowHarness(repository, fake)
  with pytest.raises(ledger.LedgerError, match="rule_values is a BASE TABLE with engine MyISAM; a row change runs in one "
      "transaction, so its tables must be InnoDB"):
    harness.ledger.stage("0001")


def schemaHarness(repository):
  fake = itemsFake()
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-itemsColumns", schemaChangeFields(), schemaFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. Add the columns [0001]"]), encoding="utf-8")
  harness.commitAll()
  return harness, fake


def test_aSchemaChangeResumesFromTheStepAfterTheOneThatFailed(repository):
  harness, fake = schemaHarness(repository)
  originalRows = rowsOf(fake, "items")
  harness.ledger.stage("0001")
  saved = json.loads((repository / "changes/0001-itemsColumns/saved.json").read_text())
  assert set(saved["tables"]) == {"items"}
  assert (repository / "changes/0001-itemsColumns/showCreate/items.sql").read_text() == ("CREATE TABLE `items` (\n  `id` "
    "int(11) NOT NULL,\n  `name` varchar(64) NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=latin1;\n")
  fake.failSteps.add(schemaSteps[1])
  with pytest.raises(ledger.LedgerError, match="0001-itemsColumns: 1 of 2 steps are done; fix the cause and run apply again"):
    harness.ledger.apply("0001")
  assert harness.ledger.state(harness.ledger.find("0001")).label == "partly applied (1 of 2)"
  harness.ledger.apply("0001")
  assert [column.name for column in fake.tables["items"].columns] == ["id", "name", "a", "b"]
  assert [statements for transaction in ledger.parseBinaryLog(fake.renderBinaryLog(0))
    for statements in transaction.statements] == [("0001", schemaSteps[0], "peq"), ("0001", schemaSteps[1], "peq")]
  assert "- [x] 1. Add the columns [0001]" in (repository / "plan.md").read_text()
  harness.ledger.revert("0001")
  assert rowsOf(fake, "items") == originalRows
  assert [column.name for column in fake.tables["items"].columns] == ["id", "name"]


def test_aPartlyAppliedSchemaChangeRevertsOnlyTheStepsDone(repository):
  harness, fake = schemaHarness(repository)
  harness.ledger.stage("0001")
  fake.failSteps.add(schemaSteps[1])
  with pytest.raises(ledger.LedgerError, match=r"1 of 2 steps are done; fix the cause and run apply again to resume from the "
      r"next step \(claudeperidot: .* exited 1: ERROR 1205 \(HY000\) at line \d+: Lock wait timeout exceeded"):
    harness.ledger.apply("0001")
  harness.ledger.revert("0001")
  assert [statements for transaction in ledger.parseBinaryLog(fake.renderBinaryLog(0))
    for statements in transaction.statements] == [("0001", schemaSteps[0], "peq"),
    ("0001", schemaReverts[1], "peq")]
  assert [column.name for column in fake.tables["items"].columns] == ["id", "name"]


def test_schemaApplyRefusesTablesChangedSinceStage(repository):
  harness, fake = schemaHarness(repository)
  harness.ledger.stage("0001")
  fake.tables["items"].rows[0]["name"] = b"Renamed by Spire"
  with pytest.raises(ledger.LedgerError, match="the tables of 0001-itemsColumns changed since stage"):
    harness.ledger.apply("0001")
  assert harness.fake.writeScripts == []


def test_schemaRevertRefusesTablesChangedSinceApply(repository):
  harness, fake = schemaHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  fake.tables["items"].rows[0]["a"] = 7
  with pytest.raises(ledger.LedgerError, match="the tables of 0001-itemsColumns changed since apply; revert refuses to "
      "overwrite them"):
    harness.ledger.revert("0001")
  assert [column.name for column in fake.tables["items"].columns] == ["id", "name", "a", "b"]


def test_aSchemaChangeThatDropsATableRecordsItAbsent(repository):
  fake = itemsFake()
  fake.tables["scratch"] = FakeTable([("id", "int")], ("id",), [{"id": 1}])
  fake.createStatements["scratch"] = "CREATE TABLE `scratch` (\n  `id` int(11) NOT NULL\n) ENGINE=InnoDB"
  drop = "DROP TABLE scratch"
  fake.mutations[drop] = lambda fake: fake.tables.pop("scratch")
  fake.mutations["CREATE TABLE scratch (id INT NOT NULL PRIMARY KEY)"] = lambda fake: fake.tables.update(scratch=FakeTable(
    [("id", "int")], ("id",), [{"id": 1}]))
  check = "SELECT COUNT(*) = 0 AS applied FROM information_schema.TABLES WHERE TABLE_NAME = 'scratch'"
  fake.checks[check] = lambda fake: int("scratch" not in fake.tables)
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-dropScratch", schemaChangeFields(scope=[{"table": "scratch"}], schemaSteps=1), {
    "apply.sql": drop + ";\n", "revert.sql": "CREATE TABLE scratch (id INT NOT NULL PRIMARY KEY);\n", "check.sql": check + ";\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 1. Drop it [0001]"]), encoding="utf-8")
  harness.commitAll()
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  assert committedJSON(repository, "tableChecksums.json") == {"scratch": {"checksum": "absent", "by": "apply 0001-dropScratch"}}
