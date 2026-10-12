from pathlib import Path

import ledger
from ledgerDoubles import (FakeMariaDB, FakeSQLError, clientChangeFields, expansionValue, git, makeHarness, planText,
  rowChangeFields, rowFiles, ruleFake, schemaChangeFields, setRule, writeChange)

realLedger = Path(ledger.__file__).resolve().parent.parent


def statusHarness(repository, tmp_path, planSteps, inProgress=None, fake=None):
  """0001 (exploration) checks 1, 0002 (production, significant, pending) checks 0, 0003 (schema, 3 steps) checks 2,
  0004 (client fixture) has its file."""
  fake = fake or FakeMariaDB()
  for value in range(3):
    fake.checks[f"SELECT {value} AS applied"] = lambda fake, value=value: value
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-applied", rowChangeFields(), {"check.sql": "SELECT 1 AS applied;"})
  writeChange(repository, "0002-notApplied", rowChangeFields(disposition="production", production="contentSQL",
    significance="significant"), {"check.sql": "SELECT 0 AS applied;"})
  writeChange(repository, "0003-partSchema", schemaChangeFields(schemaSteps=3), {"check.sql": "SELECT 2 AS applied;"})
  present = tmp_path / "present.txt"
  present.write_text("here", encoding="utf-8")
  writeChange(repository, "0004-clientFile", clientChangeFields(scope=[str(present)], checks=[{"path": str(present),
    "exists": "file"}]))
  (repository / "plan.md").write_text(planText(planSteps, inProgress), encoding="utf-8")
  harness.commitAll()
  return harness


def test_statusDerivesEachStateFromItsCheckInOneQuery(repository, tmp_path):
  harness = statusHarness(repository, tmp_path, ["- [x] 1. The first [0001]", "- [ ] 2. The second [0002]"])
  lines = harness.ledger.status()
  assert lines == [
    "In progress (plan.md): 2. The second [0002]",
    "Next (plan.md): None.",
    "",
    "0001-applied     db      exploration  applied; not staged",
    "0002-notApplied  db      production   not applied; not staged",
    "0003-partSchema  db      exploration  partly applied (2 of 3); not staged",
    "0004-clientFile  client  fixture      applied",
    "",
    "Closing a session:",
    "  fixtures (kept): 0004",
    "  exploration changes still applied: 0001, 0003",
    "  production changes the owner has not cleared: 0002",
    "  drift: run drift; it must be clean",
  ]
  assert len(harness.fake.sqlScripts) == 1
  assert harness.fake.writeScripts == []


def test_aCheckThatFailsOrGivesNullMarksOnlyItsOwnChange(repository, tmp_path):
  fake = FakeMariaDB()

  def missingTable(fake):
    raise FakeSQLError(1146, "42S02", "Table 'peq.nothere' doesn't exist")

  fake.checks["SELECT applied FROM nothere"] = missingTable
  fake.checks["SELECT NULL AS applied"] = lambda fake: None
  harness = statusHarness(repository, tmp_path, ["- [x] 1. The first [0001]"], fake=fake)
  writeChange(repository, "0005-brokenCheck", rowChangeFields(), {"check.sql": "SELECT applied FROM nothere;"})
  writeChange(repository, "0006-nullCheck", rowChangeFields(), {"check.sql": "SELECT NULL AS applied;"})
  writeChange(repository, "0007-twoStatements", rowChangeFields(), {"check.sql": "SELECT 1 AS applied; SELECT 0 AS applied;"})
  harness.commitAll()
  lines = harness.ledger.status()
  assert lines[3:10] == [
    "0001-applied        db      exploration  applied; not staged",
    "0002-notApplied     db      production   not applied; not staged",
    "0003-partSchema     db      exploration  partly applied (2 of 3); not staged",
    "0004-clientFile     client  fixture      applied",
    "0005-brokenCheck    db      exploration  check failed; ERROR 1146 (42S02): Table '?' doesn't exist",
    "0006-nullCheck      db      exploration  check failed; check.sql gave NULL, not a whole number",
    "0007-twoStatements  db      exploration  check failed; 0007-twoStatements/check.sql must be one SELECT giving one row "
    "with a column named applied",
  ]


def test_statusFlagsAnApplyThatStoppedBeforeItsCommitAndATickWithoutItsChange(repository, tmp_path):
  harness = statusHarness(repository, tmp_path, ["- [ ] 1. Apply the first [0001]", "- [x] 2. The second [0002]",
    "- [x] 3. The client file [0004]", "- [ ] 4. Unknown [0009]"])
  lines = harness.ledger.status()
  assert lines[3:6] == [
    "plan.md: step 1's changes are applied but it is not ticked: an apply stopped before its commit; run apply again to "
    "finish it",
    "plan.md: step 2 is ticked but not all of its changes are applied",
    "plan.md: step 4 names 0009, which is not in the ledger",
  ]


def test_statusFlagsAnInProgressSectionThatIsNotTheFirstUntickedStep(repository, tmp_path):
  harness = statusHarness(repository, tmp_path, ["- [x] 1. The first [0001]", "- [ ] 2. The second"], inProgress="7. Other")
  assert harness.ledger.status()[3] == ("plan.md: the In progress section does not name the first unticked step, 2. The "
    "second")


def test_statusShowsRowsChangedSinceApply(repository):
  harness = makeHarness(repository, ruleFake())
  writeChange(repository, "0001-expansionTest", rowChangeFields(), rowFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. Set the expansion [0001]"]), encoding="utf-8")
  harness.commitAll()
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  assert harness.ledger.status()[3] == "0001-expansionTest  db      exploration  applied"
  setRule(harness.fake, 2, b"Expansion:CurrentExpansion", b"8")
  assert harness.ledger.status()[3] == "0001-expansionTest  db      exploration  applied"
  setRule(harness.fake, 1, b"Test:New", b"edited by a GM")
  assert expansionValue(harness.fake) == b"19"
  assert harness.ledger.status()[3] == "0001-expansionTest  db      exploration  applied; rows changed since apply"


def test_statusWritesNothing(repository, tmp_path):
  harness = statusHarness(repository, tmp_path, ["- [x] 1. The first [0001]"])
  files = {path: path.read_bytes() for path in repository.rglob("*") if path.is_file()}
  harness.ledger.status()
  assert {path: path.read_bytes() for path in repository.rglob("*") if path.is_file()} == files
  assert git(repository, "status", "--porcelain") == ""
  assert [event for event in harness.events if event[0] == "push"] == []


def test_statusOfOnlyWorkstationChangesNeverConnects(repository, tmp_path):
  present = tmp_path / "present.txt"
  present.write_text("here", encoding="utf-8")
  writeChange(repository, "0001-clientFile", clientChangeFields(scope=[str(present)], checks=[{"path": str(present),
    "exists": "file"}, {"path": str(tmp_path / "absent.txt"), "exists": "file"}]))
  (repository / "plan.md").write_text(planText(["- [ ] 1. The client file [0001]"]), encoding="utf-8")

  def refuseToConnect():
    raise AssertionError("status connected for workstation changes")

  lines = ledger.Ledger(repository, refuseToConnect, lambda folder: None).status()
  assert "0001-clientFile  client  fixture      partly applied (1 of 2)" in lines


def test_rewritePlanTicksStepsWhoseChangesAreAllAppliedAndNamesTheFirstTwoUntickedSteps():
  text = planText(["- [x] 1. Done by hand", "- [ ] 2. Both [0001, 0002]", "- [ ] 3. Only one [0001]", "- [ ] 4. Next by hand",
    "- [x] 5. Was applied [0003]"])
  rewritten = ledger.rewritePlan(text, {"0001": True, "0002": False, "0003": False}, {"0001"})
  steps = {step.number: step.ticked for step in ledger.readPlanSteps(rewritten)}
  assert steps == {1: True, 2: False, 3: True, 4: False, 5: True}
  assert ledger.planSection(rewritten, "In progress") == "2. Both [0001, 0002]"
  assert ledger.planSection(rewritten, "Next") == "4. Next by hand"
  unticked = ledger.rewritePlan(rewritten, {"0003": False}, {"0003"})
  assert {step.number: step.ticked for step in ledger.readPlanSteps(unticked)}[5] is False
  assert unticked.endswith("- [ ] 5. Was applied [0003]\n")
  finished = ledger.rewritePlan(planText(["- [x] 1. Done [0001]"]), {"0001": True}, {"0001"})
  assert ledger.planSection(finished, "In progress") == "None: every step is done."
  assert ledger.planSection(finished, "Next") == "None."


def test_theRepositoryPlanNamesItsFirstTwoUntickedStepsAndEveryChangeTheLedgerApplies():
  text = (realLedger / "plan.md").read_text(encoding="utf-8")
  steps = ledger.readPlanSteps(text)
  unticked = [step for step in steps if not step.ticked]
  assert ledger.planSection(text, "In progress") == unticked[0].describe()
  assert ledger.planSection(text, "Next") == (unticked[1].describe() if len(unticked) > 1 else "None.")
  named = {number for step in steps for number in step.changes}
  assert named == {change.number for change in ledger.loadChanges(realLedger) if change.kind != "lab"}
  assert ledger.rewritePlan(text, {number: True for number in named}, named) == text
