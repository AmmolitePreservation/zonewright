import json

import pytest

import ledger
from ledgerDoubles import (FakeMariaDB, changedInCommit, committedFiles, expansionCheck, expansionUpdate, expansionValue, git,
  gitHead, itemsFake, makeHarness, planText, questFields, rowChangeFields, rowFiles, ruleFake, rowsOf, schemaChangeFields,
  schemaFiles, schemaReverts, schemaSteps, setRule, testDelete, testInsert, writeChange)

changeFolder = "changes/0001-expansionTest"


def rowHarness(repository, fake=None):
  harness = makeHarness(repository, fake if fake is not None else ruleFake())
  writeChange(repository, "0001-expansionTest", rowChangeFields(), rowFiles())
  (repository / "plan.md").write_text(planText(["- [x] 0. Read Peridot", "- [ ] 1. Set the expansion [0001]",
    "- [ ] 2. Something by hand"]), encoding="utf-8")
  harness.commitAll()
  return harness


def lastMessage(folder):
  return git(folder, "log", "-1", "--format=%s").strip()


def test_stageSavesTheRowsWritesRevertAndPushesBeforeAnythingRuns(repository):
  harness = rowHarness(repository)
  before = rowsOf(harness.fake, "rule_values")
  assert harness.ledger.stage("0001") == ["staged 0001-expansionTest: committed and pushed before it runs"]
  assert harness.fake.writeScripts == []
  assert rowsOf(harness.fake, "rule_values") == before
  assert changedInCommit(repository) == {f"claudeperidot/{changeFolder}/saved.json", f"claudeperidot/{changeFolder}/revert.sql"}
  assert lastMessage(repository) == "Stage 0001-expansionTest: Expansion:CurrentExpansion is 19 in ruleset 1"
  assert harness.events[-1] == ("push", gitHead(repository))
  saved = json.loads((repository / changeFolder / "saved.json").read_text())
  assert [bytes.fromhex(row[1][1:]) for row in saved["tables"][0]["rows"]] == [b"Expansion:CurrentExpansion", b"Test:Gone",
    b"Test:Quoted"]
  assert saved["checksum"] == ledger.scopeChecksum(saved["tables"])
  assert (repository / changeFolder / "revert.sql").read_text() == (
    "-- Written by ledger.py stage from the rows in saved.json; ledger.py revert runs it to put back exactly those rows.\n"
    "DELETE FROM `rule_values` WHERE (ruleset_id = 1);\n"
    "INSERT INTO `rule_values` (`ruleset_id`, `rule_name`, `rule_value`, `notes`) VALUES\n"
    "  (1, 'Expansion:CurrentExpansion', '4', 'The current expansion'),\n"
    "  (1, 'Test:Gone', 'x', NULL),\n"
    "  (1, 'Test:Quoted', _latin1 X'4F27427269656E205C20636166E9', '');\n")


def test_applyRunsOnlyAfterTheStagedRecordIsPushedAndCommitsItsResult(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  stageHead = gitHead(repository)
  assert harness.ledger.apply("0001") == ["applied 0001-expansionTest", "activation: #reload rules"]
  writeIndex = next(index for index, event in enumerate(harness.events) if event[0] == "write")
  assert ("push", stageHead) in harness.events[:writeIndex]
  assert harness.events[writeIndex] == ("write", stageHead)
  script = harness.fake.writeScripts[0]
  assert script.startswith("SET NAMES utf8mb4;\nSET time_zone = '+00:00';\nSET SESSION group_concat_max_len = 4294967295;\n"
    "SET SESSION gtid_domain_id = 7;\nSTART TRANSACTION;\nDELIMITER //\nBEGIN NOT ATOMIC\n")
  assert f"{expansionUpdate};\n{testInsert};\n{testDelete};\n" in script
  assert script.endswith(f"  IF ((SELECT applied FROM ({expansionCheck}) AS ledgerCheck) = 1) IS NOT TRUE THEN\n    SIGNAL "
    "SQLSTATE '45000' SET MESSAGE_TEXT = 'ledger: 0001 check is not 1 after apply';\n  END IF;\nEND//\nDELIMITER ;\nCOMMIT;\n")
  assert {domain for domain, _ in harness.fake.writes} == {7}
  assert expansionValue(harness.fake) == b"19"
  names = {row["rule_name"] for row in harness.fake.tables["rule_values"].rows if row["ruleset_id"] == b"1"}
  assert names == {b"Expansion:CurrentExpansion", b"Test:New", b"Test:Quoted"}
  assert next(row["rule_value"] for row in harness.fake.tables["rule_values"].rows if row["ruleset_id"] == b"2") == b"7"
  assert changedInCommit(repository) == {f"claudeperidot/{changeFolder}/applied.json", "claudeperidot/plan.md"}
  assert lastMessage(repository) == "Apply 0001-expansionTest: Expansion:CurrentExpansion is 19 in ruleset 1"
  assert harness.events[-1] == ("push", gitHead(repository))
  applied = json.loads((repository / changeFolder / "applied.json").read_text())
  assert applied["tableChecksums"] == {"rule_values": harness.fake.tableChecksum("rule_values")}
  plan = (repository / "plan.md").read_text()
  assert "- [x] 1. Set the expansion [0001]" in plan
  assert ledger.planSection(plan, "In progress") == "2. Something by hand"
  assert ledger.planSection(plan, "Next") == "None."


def test_applyIsWholeOrNothingWhenTheCheckFails(repository):
  harness = rowHarness(repository)
  harness.fake.checks[expansionCheck] = lambda fake: 0
  harness.ledger.stage("0001")
  stageHead = gitHead(repository)
  before = rowsOf(harness.fake, "rule_values")
  with pytest.raises(ledger.LedgerError, match="ledger: 0001 check is not 1 after apply"):
    harness.ledger.apply("0001")
  assert rowsOf(harness.fake, "rule_values") == before
  assert not (repository / changeFolder / "applied.json").exists()
  assert gitHead(repository) == stageHead
  assert "- [ ] 1. Set the expansion [0001]" in (repository / "plan.md").read_text()


def test_applyRefusesRowsChangedSinceStage(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  setRule(harness.fake, 1, b"Test:Gone", b"edited by a GM")
  with pytest.raises(ledger.LedgerError, match="changed since stage, so revert.sql would put back stale rows"):
    harness.ledger.apply("0001")
  assert harness.fake.writeScripts == []


def test_applyRefusesAnUnstagedChangeUncommittedEditsAndAChangeAlreadyApplied(repository):
  harness = rowHarness(repository)
  with pytest.raises(ledger.LedgerError, match="0001-expansionTest is not staged: run stage 0001 first"):
    harness.ledger.apply("0001")
  harness.ledger.stage("0001")
  applySQL = repository / changeFolder / "apply.sql"
  applySQL.write_text(applySQL.read_text() + "DELETE FROM rule_values;\n")
  with pytest.raises(ledger.LedgerError, match="has uncommitted edits"):
    harness.ledger.apply("0001")
  git(repository, "checkout", "--", f"{changeFolder}/apply.sql")
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
  script = harness.fake.writeScripts[-1]
  assert "SET SESSION gtid_domain_id = 7;\nSTART TRANSACTION;\n" in script
  assert script.count("BEGIN NOT ATOMIC") == 3
  assert "DELETE FROM `rule_values` WHERE (ruleset_id = 1);\n" in script
  assert "MESSAGE_TEXT = 'ledger: 0001 revert did not restore the saved rows'" in script
  assert script.endswith("COMMIT;\n")
  assert not (repository / changeFolder / "applied.json").exists()
  assert f"claudeperidot/{changeFolder}/applied.json" not in committedFiles(repository)
  assert lastMessage(repository) == "Revert 0001-expansionTest: Expansion:CurrentExpansion is 19 in ruleset 1"
  plan = (repository / "plan.md").read_text()
  assert "- [ ] 1. Set the expansion [0001]" in plan
  assert ledger.planSection(plan, "In progress") == "1. Set the expansion [0001]"
  assert ledger.planSection(plan, "Next") == "2. Something by hand"
  assert harness.events[-1] == ("push", gitHead(repository))


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


def test_revertRefusesAChangeThatIsNotApplied(repository):
  harness = rowHarness(repository)
  harness.ledger.stage("0001")
  with pytest.raises(ledger.LedgerError, match="revert needs 0001-expansionTest applied; it is not applied"):
    harness.ledger.revert("0001")


def test_aSchemaChangeResumesFromTheStepAfterTheOneThatFailed(repository):
  fake = itemsFake()
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-itemsColumns", schemaChangeFields(), schemaFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. Add the columns [0001]"]), encoding="utf-8")
  harness.commitAll()
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
  assert harness.fake.writeScripts[-1].endswith(f"SET SESSION gtid_domain_id = 7;\n{schemaSteps[1]};\n")
  assert [column[0] for column in fake.tables["items"].columns] == ["id", "name", "a", "b"]
  assert "- [x] 1. Add the columns [0001]" in (repository / "plan.md").read_text()
  harness.ledger.revert("0001")
  assert harness.fake.writeScripts[-1].endswith(f"{schemaReverts[0]};\n{schemaReverts[1]};\n")
  assert rowsOf(fake, "items") == originalRows
  assert [column[0] for column in fake.tables["items"].columns] == ["id", "name"]


def test_aPartlyAppliedSchemaChangeRevertsOnlyTheStepsDone(repository):
  fake = itemsFake()
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-itemsColumns", schemaChangeFields(), schemaFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. Add the columns [0001]"]), encoding="utf-8")
  harness.commitAll()
  harness.ledger.stage("0001")
  fake.failSteps.add(schemaSteps[1])
  with pytest.raises(ledger.LedgerError, match=r"1 of 2 steps are done; fix the cause and run apply again to resume from the "
      r"next step \(claudeperidot: .* exited 1: ERROR 1205 \(HY000\): Lock wait timeout exceeded"):
    harness.ledger.apply("0001")
  harness.ledger.revert("0001")
  assert harness.fake.writeScripts[-1].endswith(f"SET SESSION gtid_domain_id = 7;\n{schemaReverts[1]};\n")
  assert [column[0] for column in fake.tables["items"].columns] == ["id", "name"]


def test_schemaApplyRefusesTablesChangedSinceStage(repository):
  fake = itemsFake()
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-itemsColumns", schemaChangeFields(), schemaFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. Add the columns [0001]"]), encoding="utf-8")
  harness.commitAll()
  harness.ledger.stage("0001")
  fake.tables["items"].rows[0]["name"] = b"Renamed by Spire"
  with pytest.raises(ledger.LedgerError, match="the tables of 0001-itemsColumns changed since stage"):
    harness.ledger.apply("0001")
  assert harness.fake.writeScripts == []


def questHarness(repository, before, after):
  fake = FakeMariaDB()
  hostFiles = dict(before)

  def hashLines(files):
    return "".join(f"{files[path] or 'absent'}  {path}\n" for path in sorted(files))

  fake.shell = [
    (r"git cat-file -e bbbb222", lambda command: hashLines(after)),
    (r"sha256sum --", lambda command: hashLines(hostFiles)),
    (r"git merge --ff-only bbbb222", lambda command: hostFiles.update(after) or ""),
    (r"revert --no-edit bbbb222 aaaa111", lambda command: hostFiles.update(before) or ""),
  ]
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-questFix", questFields(scope=sorted(before)))
  (repository / "plan.md").write_text(planText(["- [ ] 1. Fix the quest [0001]"]), encoding="utf-8")
  harness.commitAll()
  return harness, hostFiles


def test_aQuestChangeAppliesAndRevertsInTheHostCheckoutJudgedByFileHashes(repository):
  before = {"global/spells/17779.lua": None, "global/global_player.pl": "1" * 64}
  after = {"global/spells/17779.lua": "2" * 64, "global/global_player.pl": "3" * 64}
  harness, hostFiles = questHarness(repository, before, after)
  harness.ledger.stage("0001")
  assert json.loads((repository / "changes/0001-questFix/saved.json").read_text()) == {"before": before, "after": after}
  assert harness.ledger.state(harness.ledger.find("0001")).label == "not applied"
  harness.ledger.apply("0001")
  assert hostFiles == after
  merge = next(command for command, _ in harness.fake.calls if "merge" in command)
  assert merge == ("sudo -n -u \"$(stat -c %U /opt/eqemu/akk-stack/server/quests)\" sh -c 'cd /opt/eqemu/akk-stack/server/quests "
    "&& git merge --ff-only bbbb222'")
  assert "- [x] 1. Fix the quest [0001]" in (repository / "plan.md").read_text()
  hostFiles["global/global_player.pl"] = "4" * 64
  assert harness.ledger.state(harness.ledger.find("0001")).label == ("partly applied (1 of 2); some files are neither as "
    "committed nor as staged")
  hostFiles["global/global_player.pl"] = after["global/global_player.pl"]
  harness.ledger.revert("0001")
  assert hostFiles == before
  revert = next(command for command, _ in harness.fake.calls if "revert" in command)
  assert "git -c user.name=Claude -c user.email=claude@claudeperidot revert --no-edit bbbb222 aaaa111" in revert


def test_stageRefusesQuestCommitsThatLeaveAScopeFileAsItIs(repository):
  same = {"global/global_player.pl": "1" * 64}
  harness, _ = questHarness(repository, same, same)
  with pytest.raises(ledger.LedgerError, match="the commits leave global/global_player.pl as it is on the host now"):
    harness.ledger.stage("0001")
