"""zoneDeploy: a zone's server side deployed, redeployed and taken back through the ledger, against real git repositories, the
MariaDB stand-in and the host stand-in's real shell (with a docker stand-in for the EQEmu container's launcher)."""
import copy
import datetime
import hashlib
import json

import pytest

import ledger
import zoneDeploy
from ledgerDoubles import FakeMariaDB, FakeTable, git, gitHead, makeHarness, planText, posixPath, remoteHead

maps = "/opt/eqemu/akk-stack/server/maps"
shortName = "testzone"
zoneColumns = [("zoneidnumber", "int"), ("version", "int"), ("short_name", "varchar", "latin1"), ("long_name", "varchar", "latin1"),
  ("safe_x", "float"), ("ruleset", "int"), ("note", "varchar", "latin1")]
pointColumns = [("zone", "varchar", "latin1"), ("version", "int"), ("number", "int"), ("x", "float"), ("target_zone_id", "int")]
doorColumns = [("zone", "varchar", "latin1"), ("version", "int"), ("doorid", "int"), ("name", "varchar", "latin1"), ("pos_x", "float")]
objectColumns = [("zoneid", "int"), ("version", "int"), ("objectname", "varchar", "latin1"), ("xpos", "float")]
keyOf = {"zone": ("short_name", "version"), "zone_points": ("zone", "number", "version"), "doors": ("zone", "doorid", "version"),
  "object": ("zoneid", "objectname", "xpos")}
author = {"session": "session_0121Qm1vvZgAKaiV6dKmANxe", "agent": "a0206fa5316c5360e"}
dockerShim = """#!/bin/sh
state="{root}/processes"
case "$*" in
  *"launcher stop"*) : > "$state"; echo stop >> "{root}/launcherLog" ;;
  *"launcher start"*) printf '%s\\n' './bin/spire eqemu-server:launcher start' '/home/eqemu/server/bin/world' \\
    '/home/eqemu/server/bin/zone poknowledge' > "$state"; echo start >> "{root}/launcherLog" ;;
  *"ps -eo args="*) cat "$state" ;;
  *) echo "docker stand-in: $*" >&2; exit 9 ;;
esac
"""


def sha(content):
  return hashlib.sha256(content).hexdigest()


def text(value):
  return value.encode("latin-1") if isinstance(value, str) else value


def zoneFake():
  fake = FakeMariaDB()
  fake.tables["zone"] = FakeTable(zoneColumns, keyOf["zone"], [
    {"zoneidnumber": 407, "version": 0, "short_name": b"highpasshold", "long_name": b"Highpass Hold", "safe_x": 1.0, "ruleset": 3,
      "note": b"the template's note"},
    {"zoneidnumber": 2, "version": 0, "short_name": b"qeynos", "long_name": b"South Qeynos", "safe_x": 2.0, "ruleset": 0, "note": None},
  ])
  fake.tables["zone_points"] = FakeTable(pointColumns, keyOf["zone_points"], [
    {"zone": b"highpasshold", "version": 0, "number": 10, "x": 5.0, "target_zone_id": 407}])
  fake.tables["doors"] = FakeTable(doorColumns, keyOf["doors"], [
    {"zone": b"highpasshold", "version": 0, "doorid": 1, "name": b"POKCAB500", "pos_x": 7.0}])
  fake.tables["object"] = FakeTable(objectColumns, keyOf["object"], [
    {"zoneid": 407, "version": 0, "objectname": b"IT10801_ACTORDEF", "xpos": 3.0}])
  fake.predicates[f"short_name = '{shortName}'"] = lambda row: row["short_name"] == shortName.encode()
  fake.predicates[f"zone = '{shortName}'"] = lambda row: row["zone"] == shortName.encode()
  fake.predicates["zoneid = 900"] = lambda row: row["zoneid"] == 900
  fake.predicates[f"zoneidnumber = 900 OR short_name = '{shortName}'"] = lambda row: row["zoneidnumber"] == 900 or row["short_name"] == shortName.encode()
  return fake


def request(tmp_path, doors=None, objects=None, mapContent=b"the map"):
  staging = tmp_path / "staging"
  staging.mkdir(exist_ok=True)
  files = []
  for kind, extension, content in (("base", "map", mapContent), ("water", "wtr", b"WATR"), ("nav", "nav", b"the nav")):
    path = staging / f"{shortName}.{extension}"
    path.write_bytes(content)
    files.append({"path": f"{maps}/{kind}/{shortName}.{extension}", "source": str(path), "sha256": sha(content)})
  return {
    "command": "deploy", "target": "claudeperidot",
    "zone": {"shortName": shortName, "zoneId": 900, "longName": "The Test Zone", "template": "highpasshold"},
    "rows": {
      "zone": {"written": {"zoneidnumber": 900, "version": 0, "short_name": shortName, "long_name": "The Test Zone", "safe_x": 4.5},
        "copied": ["ruleset", "note"]},
      "zonePoints": [{"zone": shortName, "version": 0, "number": 10, "x": 1.5, "target_zone_id": 407}],
      "doors": doors if doors is not None else [{"zone": shortName, "version": 0, "doorid": 1, "name": "OBJ_CRDOOR", "pos_x": 2.5},
        {"zone": shortName, "version": 0, "doorid": 2, "name": "OBJ_POK_BOOK_", "pos_x": 6.25}],
      "objects": objects if objects is not None else [{"zoneid": 900, "version": 0, "objectname": "IT10801_ACTORDEF", "xpos": 8.0}],
    },
    "files": files, "export": {"manifest": str(staging / f"{shortName}_export.json"), "archiveSHA256": "a" * 64},
    "planStep": 2, "author": author, "ownerClearance": "pending", "disposition": "exploration",
  }


def zoneRows(fake, table):
  where = {"zone": "short_name", "zone_points": "zone", "doors": "zone"}
  if table == "object":
    return [row for row in fake.tables[table].rows if row["zoneid"] == 900]
  return [row for row in fake.tables[table].rows if row[where[table]] == shortName.encode()]


def expectedRows(fake, deployed):
  """The zone's rows as the deploy's statements leave them, the zone row's copied columns the template's."""
  template = next(row for row in fake.tables["zone"].rows if row["short_name"] == b"highpasshold")
  rows = deployed["rows"]
  zone = {column: text(value) for column, value in rows["zone"]["written"].items()} | {column: template[column] for column in rows["zone"]["copied"]}
  return {
    "zone": [zone], "zone_points": [{column: text(value) for column, value in row.items()} for row in rows["zonePoints"]],
    "doors": [{column: text(value) for column, value in row.items()} for row in rows["doors"]],
    "object": [{column: text(value) for column, value in row.items()} for row in rows["objects"]],
  }


def teach(fake, deployed):
  """The stand-in runs the deploy's zone row insert (an INSERT ... SELECT from the template) and answers its check."""
  def insertZone(fake):
    fake.tables["zone"].insert(expectedRows(fake, deployed)["zone"][0])

  def check(fake):
    expected = expectedRows(fake, deployed)
    return int(all(sorted(map(repr, zoneRows(fake, table))) == sorted(map(repr, [{column.name: row.get(column.name) for column in
      fake.tables[table].columns} for row in rows])) for table, rows in expected.items()))

  fake.mutations[zoneDeploy.zoneInsert(deployed["zone"], deployed["rows"]["zone"])] = insertZone
  fake.checks[zoneDeploy.deployCheck(deployed)] = check


class Clock:
  def __init__(self):
    self.moment = datetime.datetime(2026, 10, 13, 1, 0, 0, tzinfo=datetime.UTC)

  def __call__(self):
    self.moment += datetime.timedelta(minutes=10)
    return self.moment


@pytest.fixture
def setup(repository, host, tmp_path):
  for kind in ("base", "water", "nav"):
    host.path(f"{maps}/{kind}").mkdir(parents=True)
  (host.shims / "docker").write_text(dockerShim.format(root=posixPath(host.root)), encoding="utf-8", newline="\n")
  fake = zoneFake()
  fake.host = host
  harness = makeHarness(repository, fake)
  (repository / "plan.md").write_text(planText(["- [x] 1. Set up", "- [ ] 2. The test zone on claudeperidot", "- [ ] 3. Later"]),
    encoding="utf-8")
  harness.commitAll()
  emitted = []
  return harness, zoneDeploy.Deployment(harness.ledger, emitted.append, Clock()), emitted, host


def launches(host):
  log = host.root / "launcherLog"
  return log.read_text().split() if log.exists() else []


def test_aDeployInstallsTheMapsAndMakesTheZonesRowsTheExportsThenRestartsTheServer(setup, tmp_path, repository):
  harness, deployment, emitted, host = setup
  deployed = request(tmp_path)
  teach(harness.fake, deployed)
  outside = {table: [row for row in harness.fake.tables[table].rows if row not in zoneRows(harness.fake, table)] for table in keyOf}
  result = deployment.deploy(deployed)
  assert result["deployed"] == ["20261013-011000-deployTestzoneMaps", "20261013-011001-deployTestzoneRows"]
  assert result["unchanged"] == [] and result["reverted"] == []
  assert result["restart"] == {"stopExit": "0", "running": {"launcher": 1, "world": 1, "zone": 1}}
  assert launches(host) == ["stop", "start"]
  for table, rows in expectedRows(harness.fake, deployed).items():
    assert sorted(map(repr, zoneRows(harness.fake, table))) == sorted(map(repr, rows)), table
  for table in keyOf:
    assert [row for row in harness.fake.tables[table].rows if row not in zoneRows(harness.fake, table)] == outside[table]
  for entry in deployed["files"]:
    assert host.path(entry["path"]).read_bytes() == open(entry["source"], "rb").read()
  plan = (repository / "plan.md").read_text()
  assert "- [x] 2. The test zone on claudeperidot [20261013-011000, 20261013-011001]" in plan
  assert ledger.planSection(plan, "In progress") == "3. Later"
  assert remoteHead(harness.remote) == gitHead(repository.parent)
  assert git(repository.parent, "status", "--porcelain").strip() == ""
  rowsChange = harness.ledger.find("20261013-011001")
  assert rowsChange.fields["scope"] == [{"table": "zone", "where": "short_name = 'testzone'"}, {"table": "zone_points", "where": "zone = 'testzone'"},
    {"table": "doors", "where": "zone = 'testzone'"}, {"table": "object", "where": "zoneid = 900"}]
  assert rowsChange.fields["author"] == author and rowsChange.fields["disposition"] == "exploration"
  assert [line["message"] for line in emitted][-1] == "restarting the EQEmu server so world and the zone processes load the zone"
  assert all(line["total"] == 8 for line in emitted)


def test_theChangesAreCommittedAndPushedBeforeTheServerIsWritten(setup, tmp_path, repository):
  harness, deployment, _, _ = setup
  deployed = request(tmp_path)
  teach(harness.fake, deployed)
  deployment.deploy(deployed)
  writes = [event for event in harness.events if event[0] == "write"]
  assert writes and all(local == remote for _, local, remote in writes)
  written = git(repository.parent, "log", "--format=%s").splitlines()
  assert written[:6] == [
    "Apply 20261013-011001-deployTestzoneRows: testzone (zone 900) holds exactly its game export's zone row and 1 zone_points, 2 doors and 1 object rows",
    "Applying 20261013-011001-deployTestzoneRows: testzone (zone 900) holds exactly its game export's zone row and 1 zone_points, 2 doors and 1 object rows",
    "Stage 20261013-011001-deployTestzoneRows: testzone (zone 900) holds exactly its game export's zone row and 1 zone_points, 2 doors and 1 object rows",
    "Apply 20261013-011000-deployTestzoneMaps: claudeperidot's maps folder holds testzone's server maps from its game export",
    "Applying 20261013-011000-deployTestzoneMaps: claudeperidot's maps folder holds testzone's server maps from its game export",
    "Stage 20261013-011000-deployTestzoneMaps: claudeperidot's maps folder holds testzone's server maps from its game export",
  ]
  assert written[6] == "deployZone testzone: write 20261013-011000-deployTestzoneMaps, 20261013-011001-deployTestzoneRows on plan step 2"


def test_aRedeployOfTheSameExportChangesNothingAndDoesNotRestart(setup, tmp_path, repository):
  harness, deployment, _, host = setup
  deployed = request(tmp_path)
  teach(harness.fake, deployed)
  deployment.deploy(deployed)
  head = gitHead(repository.parent)
  result = deployment.deploy(copy.deepcopy(deployed))
  assert result == {"deployed": [], "unchanged": ["Maps", "Rows"], "reverted": [], "restart": None}
  assert gitHead(repository.parent) == head
  assert sorted(path.name for path in (repository / "changes").iterdir()) == ["20261013-011000-deployTestzoneMaps", "20261013-011001-deployTestzoneRows"]
  assert launches(host) == ["stop", "start"]


def test_aRedeployRevertsTheEarlierDeployAndLeavesOnlyTheNewExportsRows(setup, tmp_path, repository):
  harness, deployment, _, host = setup
  first = request(tmp_path)
  teach(harness.fake, first)
  deployment.deploy(first)
  second = request(tmp_path, doors=[{"zone": shortName, "version": 0, "doorid": 1, "name": "OBJ_CRDOOR", "pos_x": 3.5}],
    objects=[], mapContent=b"the reshaped map")
  teach(harness.fake, second)
  result = deployment.deploy(second)
  assert result["reverted"] == ["20261013-011001-deployTestzoneRows", "20261013-011000-deployTestzoneMaps"]
  assert result["deployed"] == ["20261013-012000-deployTestzoneMaps", "20261013-012001-deployTestzoneRows"]
  for table, rows in expectedRows(harness.fake, second).items():
    assert sorted(map(repr, zoneRows(harness.fake, table))) == sorted(map(repr, rows)), table
  assert zoneRows(harness.fake, "object") == []
  assert host.path(f"{maps}/base/{shortName}.map").read_bytes() == b"the reshaped map"
  plan = (repository / "plan.md").read_text()
  assert "- [x] 2. The test zone on claudeperidot [20261013-012000, 20261013-012001]" in plan
  assert harness.ledger.state(harness.ledger.find("20261013-011001")).label == "not applied"
  assert launches(host) == ["stop", "start", "stop", "start"]


def test_undeployRevertsTheZonesDeploysAndRestarts(setup, tmp_path, repository):
  harness, deployment, emitted, host = setup
  deployed = request(tmp_path)
  teach(harness.fake, deployed)
  before = {table: copy.deepcopy(harness.fake.tables[table].rows) for table in keyOf}
  deployment.deploy(deployed)
  undeploy = {"command": "undeploy", "target": "claudeperidot", "zone": {"shortName": shortName, "zoneId": 900}}
  result = deployment.undeploy(undeploy)
  assert result["reverted"] == ["20261013-011001-deployTestzoneRows", "20261013-011000-deployTestzoneMaps"]
  for table in keyOf:
    assert sorted(map(repr, harness.fake.tables[table].rows)) == sorted(map(repr, before[table])), table
  assert not any(host.path(entry["path"]).exists() for entry in deployed["files"])
  assert "- [ ] 2. The test zone on claudeperidot [20261013-011000, 20261013-011001]" in (repository / "plan.md").read_text()
  assert launches(host) == ["stop", "start", "stop", "start"]
  with pytest.raises(zoneDeploy.DeployError, match="no deployZone change of testzone is applied on claudeperidot"):
    deployment.undeploy(undeploy)


def test_aDeployRefusesAnIdAnotherZoneHoldsAndWritesNothing(setup, tmp_path, repository):
  harness, deployment, _, host = setup
  harness.fake.tables["zone"].rows.append({"zoneidnumber": 900, "version": 0, "short_name": b"someother", "long_name": b"Other",
    "safe_x": 0.0, "ruleset": 0, "note": None})
  head = gitHead(repository.parent)
  with pytest.raises(zoneDeploy.DeployError, match=r"claudeperidot's zone table holds \[\{'zoneidnumber': 900, 'version': 0, 'short_name': 'someother'\}\]"):
    deployment.deploy(request(tmp_path))
  assert gitHead(repository.parent) == head
  assert list((repository / "changes").iterdir()) == []
  assert launches(host) == []


@pytest.mark.parametrize("change, message", [
  ({"target": "peridot"}, r"a deploy's target is one of \['claudeperidot'\] \(Peridot is never written\)"),
  ({"zone": {"shortName": shortName, "zoneId": 767, "longName": "x", "template": "highpasshold"}}, "a deployed zone's id is a new zone's: 787 to 999"),
  ({"zone": {"shortName": shortName, "zoneId": 997, "longName": "x", "template": "highpasshold"}}, "a deployed zone's id is a new zone's"),
  ({"disposition": "production"}, "a deploy's disposition is fixture or exploration"),
  ({"planStep": 9}, "plan.md has no step 9"),
])
def test_aDeployRefusesWhatItCannotDoBeforeWritingAnything(setup, tmp_path, repository, change, message):
  harness, deployment, _, _ = setup
  with pytest.raises(zoneDeploy.DeployError, match=message):
    deployment.deploy(request(tmp_path) | change)
  assert list((repository / "changes").iterdir()) == []


def test_aDeployThatFailsBeforeItsChangesAreRecordedLeavesNoChangeBehind(setup, tmp_path, repository):
  harness, deployment, _, _ = setup
  broken = request(tmp_path)
  broken["rows"]["zone"]["written"]["short_name"] = "otherzone"
  with pytest.raises(zoneDeploy.DeployError, match="the zone row's short_name, zoneidnumber, version and long_name are not the zone's own"):
    deployment.deploy(broken)
  assert list((repository / "changes").iterdir()) == []
  assert git(repository.parent, "status", "--porcelain").strip() == ""


def test_editPlanAddsToTheNamedStepAndTakesSupersededChangesOutOfEveryStep():
  text = planText(["- [x] 1. Old [20261013-010000]", "- [x] 2. Zone [20261013-010000, 0004]", "- [ ] 3. Next"])
  edited = zoneDeploy.editPlan(text, 2, ["20261013-020000"], {"20261013-010000"})
  steps = {step.number: step for step in ledger.readPlanSteps(edited)}
  assert (steps[1].ticked, steps[1].changes) == (True, ())
  assert (steps[2].ticked, steps[2].changes) == (False, ("0004", "20261013-020000"))
  assert ledger.planSection(edited, "In progress") == "2. Zone [0004, 20261013-020000]"
  assert ledger.planSection(edited, "Next") == "3. Next"


def test_slotReadsEachServersZoneRowsAndNamesTheConflicts():
  servers = {"claudeperidot": zoneFake(), "peridot": zoneFake()}
  servers["peridot"].tables["zone"].rows.append({"zoneidnumber": 900, "version": 0, "short_name": b"otherzone", "long_name": b"Other",
    "safe_x": 0.0, "ruleset": 0, "note": None})
  result = zoneDeploy.slot({"command": "slot", "hosts": ["claudeperidot", "peridot"], "zone": {"shortName": shortName, "zoneId": 900}},
    lambda host: ledger.Server(servers[host]))
  assert result == {
    "claudeperidot": {"rows": [], "conflicts": []},
    "peridot": {"rows": [{"zoneidnumber": 900, "version": 0, "short_name": "otherzone"}],
      "conflicts": [{"zoneidnumber": 900, "version": 0, "short_name": "otherzone"}]},
  }
  assert all("gtid_domain_id" not in script for fake in servers.values() for script in fake.sqlScripts)
  with pytest.raises(zoneDeploy.DeployError, match="hosts is a list of distinct servers from"):
    zoneDeploy.slot({"command": "slot", "hosts": ["olivine"], "zone": {"shortName": shortName, "zoneId": 900}}, None)


def test_theRowsChangesSQLDeletesTheZonesRowsAndInsertsTheExports(tmp_path):
  deployed = request(tmp_path)
  statements = zoneDeploy.deployStatements(deployed)
  assert statements[0] == "DELETE FROM `zone` WHERE (short_name = 'testzone')"
  assert statements[1] == ("INSERT INTO `zone` (`zoneidnumber`, `version`, `short_name`, `long_name`, `safe_x`, `ruleset`, `note`) SELECT 900, 0,"
    " 'testzone', 'The Test Zone', 4.5, templateRow.ruleset, templateRow.note FROM `zone` AS templateRow WHERE templateRow.short_name ="
    " 'highpasshold' AND templateRow.version = 0")
  assert statements[2:4] == ["DELETE FROM `zone_points` WHERE (zone = 'testzone')",
    "INSERT INTO `zone_points` (`zone`, `version`, `number`, `x`, `target_zone_id`) VALUES\n  ('testzone', 0, 10, 1.5, 407)"]
  assert statements[6] == "DELETE FROM `object` WHERE (zoneid = 900)"
  assert len(statements) == 8
  check = zoneDeploy.deployCheck(deployed)
  assert "(SELECT COUNT(*) FROM doors WHERE zone = 'testzone') = 2" in check
  assert "(SELECT COUNT(*) FROM doors WHERE zone = 'testzone' AND version = 0 AND doorid = 2 AND name = 'OBJ_POK_BOOK_' AND ABS(pos_x - (6.25)) < 0.01) >= 1" in check
  assert zoneDeploy.literal("O'Brien \\ x") == "'O''Brien \\\\ x'"
