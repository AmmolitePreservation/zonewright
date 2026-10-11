"""Quest and config changes, against real git repositories: a source clone on the workstation, its bare remote, and the host's
checkout of that remote, reached through the host stand-in's real shell."""
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import ledger
from ledgerDoubles import (FakeMariaDB, committedFiles, git, gitHead, makeHarness, makeRepository, planText, posixPath,
  questFields, remoteHead, writeChange)

hostQuests = "/opt/eqemu/akk-stack/server/quests"


@dataclass
class Setup:
  harness: object
  source: Path
  checkout: Path
  remote: Path
  commits: list


def commitFile(folder, path, content, message):
  target = folder / path
  target.parent.mkdir(parents=True, exist_ok=True)
  target.write_bytes(content)
  git(folder, "add", "--", path)
  git(folder, "commit", "-q", "-m", message)
  return gitHead(folder)


def hostRepository(host, hostPath, files):
  """A source clone (on branch claude) with files committed and pushed, and the host's checkout of its remote at hostPath."""
  source = host.root.parent / ("source" + hostPath.replace("/", "_"))
  remote = makeRepository(source)
  for path, content in files.items():
    commitFile(source, path, content, f"base {path}")
  ledger.pushBranch(source)
  checkout = host.path(hostPath)
  checkout.parent.mkdir(parents=True, exist_ok=True)
  subprocess.run(["git", "clone", "-q", "-c", "core.autocrlf=false", "-b", "claude", str(remote), str(checkout)], check=True,
    capture_output=True)
  return source, remote, checkout


def questSetup(repository, host, fields=None):
  """The quests checkout with global/global_player.pl, and a change of two commits made in the source: one adds
  global/spells/17779.lua, one edits global/global_player.pl."""
  source, remote, checkout = hostRepository(host, hostQuests, {"global/global_player.pl": b"sub EVENT_SAY { }\n"})
  commits = [commitFile(source, "global/spells/17779.lua", b"-- the spell\n", "Add the spell script"),
    commitFile(source, "global/global_player.pl", b"sub EVENT_SAY { quest::say('fixed'); }\n", "Fix the player script")]
  fake = FakeMariaDB()
  fake.host = host
  harness = makeHarness(repository, fake)
  fields = dict(fields or questFields(), scope=["global/global_player.pl", "global/spells/17779.lua"], repository=hostQuests,
    source=str(source), commits=commits)
  writeChange(repository, "0001-questFix", fields)
  (repository / "plan.md").write_text(planText(["- [ ] 1. Fix the quest [0001]", "- [ ] 2. By hand"]), encoding="utf-8")
  harness.commitAll()
  return Setup(harness, source, checkout, remote, commits)


def sha(content):
  return hashlib.sha256(content).hexdigest()


def test_aQuestChangeAppliesByFastForwardAndRevertsByARevertCommitFromTheSource(repository, host):
  setup = questSetup(repository, host)
  setup.harness.ledger.stage("0001")
  assert remoteHead(setup.remote) == setup.commits[-1]
  assert json.loads((repository / "changes/0001-questFix/saved.json").read_text()) == {
    "before": {"global/global_player.pl": sha(b"sub EVENT_SAY { }\n"), "global/spells/17779.lua": None},
    "after": {"global/global_player.pl": sha(b"sub EVENT_SAY { quest::say('fixed'); }\n"),
      "global/spells/17779.lua": sha(b"-- the spell\n")}}
  assert setup.harness.ledger.state(setup.harness.ledger.find("0001")).label == "not applied"
  setup.harness.ledger.apply("0001")
  assert gitHead(setup.checkout) == setup.commits[-1]
  assert (setup.checkout / "global/spells/17779.lua").read_bytes() == b"-- the spell\n"
  assert "- [x] 1. Fix the quest [0001]" in (repository / "plan.md").read_text()
  assert setup.harness.ledger.revert("0001") == ["reverted 0001-questFix", "activation: #reload rules"]
  sourceRevert = gitHead(setup.source)
  assert git(setup.source, "log", "-1", "--format=%s").strip() == "Revert 0001-questFix: The quest is fixed"
  assert git(repository, "log", "-1", "--format=%s").strip() == ("Revert 0001-questFix: The quest is fixed (source revert "
    f"{sourceRevert[:12]})")
  assert gitHead(setup.checkout) == sourceRevert == remoteHead(setup.remote)
  assert not (setup.checkout / "global/spells/17779.lua").exists()
  assert (setup.checkout / "global/global_player.pl").read_bytes() == b"sub EVENT_SAY { }\n"
  assert "- [ ] 1. Fix the quest [0001]" in (repository / "plan.md").read_text()


def test_aLaterQuestChangeStillFastForwardsAfterARevert(repository, host):
  setup = questSetup(repository, host)
  setup.harness.ledger.stage("0001")
  setup.harness.ledger.apply("0001")
  setup.harness.ledger.revert("0001")
  later = commitFile(setup.source, "global/global_player.pl", b"sub EVENT_SAY { quest::say('later'); }\n", "A later fix")
  writeChange(repository, "0002-laterFix", questFields(title="A later fix", scope=["global/global_player.pl"],
    repository=hostQuests, source=str(setup.source), commits=[later]))
  setup.harness.commitAll("the later change")
  setup.harness.ledger.stage("0002")
  setup.harness.ledger.apply("0002")
  assert gitHead(setup.checkout) == later
  assert (setup.checkout / "global/global_player.pl").read_bytes() == b"sub EVENT_SAY { quest::say('later'); }\n"


def test_applyRefusesAnUnstagedQuestChangeBeforeTouchingTheHost(repository, host):
  setup = questSetup(repository, host)
  before = gitHead(setup.checkout)
  with pytest.raises(ledger.LedgerError, match="0001-questFix is not staged: run stage 0001 first"):
    setup.harness.ledger.apply("0001")
  assert gitHead(setup.checkout) == before
  assert host.commands == []


def test_aCommitTheHostCannotFetchFailsLoud(repository, host):
  setup = questSetup(repository, host)
  stale = host.root.parent / "staleRemote.git"
  subprocess.run(["git", "clone", "-q", "--bare", str(setup.remote), str(stale)], check=True, capture_output=True)
  git(setup.checkout, "remote", "set-url", "origin", str(stale))
  with pytest.raises(ledger.LedgerError, match=f"0001-questFix: {setup.commits[0]}, {setup.commits[1]} is not in {hostQuests} "
      "on claudeperidot even after fetching its origin: push it from the source repository"):
    setup.harness.ledger.stage("0001")
  assert not (repository / "changes/0001-questFix/saved.json").exists()


def test_aQuestCommitThatIsNotOnTheSourcesBranchIsRefused(repository, host):
  setup = questSetup(repository, host)
  git(setup.source, "checkout", "-q", "-b", "elsewhere", setup.commits[0] + "~1")
  with pytest.raises(ledger.LedgerError, match=f"0001-questFix: {setup.commits[0]} is not on .*'s checked-out branch"):
    setup.harness.ledger.stage("0001")


def test_applyRefusesHostFilesThatAreNotAsStaged(repository, host):
  setup = questSetup(repository, host)
  setup.harness.ledger.stage("0001")
  (setup.checkout / "global/global_player.pl").write_bytes(b"edited on the host\n")
  with pytest.raises(ledger.LedgerError, match="apply needs 0001-questFix not applied; it is partly applied"):
    setup.harness.ledger.apply("0001")
  assert gitHead(setup.checkout) != setup.commits[-1]


def test_revertRefusesHostFilesChangedSinceApply(repository, host):
  setup = questSetup(repository, host)
  setup.harness.ledger.stage("0001")
  setup.harness.ledger.apply("0001")
  (setup.checkout / "global/global_player.pl").write_bytes(b"edited on the host\n")
  sourceHead = gitHead(setup.source)
  with pytest.raises(ledger.LedgerError, match="revert needs 0001-questFix applied; it is partly applied"):
    setup.harness.ledger.revert("0001")
  assert gitHead(setup.source) == sourceHead


def test_stageRefusesQuestCommitsThatLeaveAScopeFileAsItIs(repository, host):
  setup = questSetup(repository, host)
  writeChange(repository, "0002-oneFile", questFields(scope=["global/global_player.pl", "global/spells/17779.lua"],
    repository=hostQuests, source=str(setup.source), commits=[setup.commits[0]]))
  setup.harness.commitAll("a change whose commit leaves a scope file alone")
  with pytest.raises(ledger.LedgerError, match="the commits leave global/global_player.pl as it is on the host now"):
    setup.harness.ledger.stage("0002")


def test_anInterruptedRevertReusesItsSourceRevertCommit(repository, host):
  setup = questSetup(repository, host)
  setup.harness.ledger.stage("0001")
  setup.harness.ledger.apply("0001")
  realRun = setup.harness.fake.host.run

  def run(command, standardInput=b""):
    if "merge --ff-only" in command:
      return 255, b"", b"ssh: connection reset"
    return realRun(command, standardInput)

  setup.harness.fake.host.run = run
  with pytest.raises(ledger.LedgerError, match="exited 255"):
    setup.harness.ledger.revert("0001")
  sourceRevert = gitHead(setup.source)
  assert json.loads(git(repository, "show", "HEAD:claudeperidot/changes/0001-questFix/intent.json")) == {"intent": "reverting",
    "sourceRevert": sourceRevert}
  setup.harness.fake.host.run = realRun
  setup.harness.ledger.revert("0001")
  assert gitHead(setup.source) == sourceRevert
  assert gitHead(setup.checkout) == sourceRevert
  assert "claudeperidot/changes/0001-questFix/intent.json" not in committedFiles(repository)


secret = "liveDatabasePassword-41f7"


def configSetup(repository, host):
  template = {"server": {"database": {"host": "mariadb", "password": ""}, "world": {"shortname": "claudeperidot"}}}
  source, remote, checkout = hostRepository(host, "/opt/eqemu/config", {"eqemu_config.json": json.dumps(template).encode()})
  template["server"]["world"]["shortname"] = "claudeperidotTest"
  commit = commitFile(source, "eqemu_config.json", json.dumps(template).encode(), "Rename the world")
  secrets = host.path("/opt/eqemu/secrets")
  secrets.mkdir(parents=True)
  (secrets / "databasePassword").write_text(secret + "\n", encoding="utf-8")
  live = host.path("/opt/eqemu/akk-stack/server/eqemu_config.json")
  live.parent.mkdir(parents=True, exist_ok=True)
  root = posixPath(host.root)
  render = host.path("/opt/eqemu/claudeperidot/render.sh")
  render.parent.mkdir(parents=True)
  render.write_text("#!/bin/sh\nset -e\npython3 -c 'import json, sys; template = json.load(open(sys.argv[1])); "
    "template[\"server\"][\"database\"][\"password\"] = open(sys.argv[2]).read().strip(); json.dump(template, "
    f"open(sys.argv[3], \"w\"), indent=1)' {root}/opt/eqemu/config/eqemu_config.json {root}/opt/eqemu/secrets/databasePassword "
    f"{root}/opt/eqemu/akk-stack/server/eqemu_config.json\n", encoding="utf-8", newline="\n")
  subprocess.run([str(host.shell), "-c", f"{root}/opt/eqemu/claudeperidot/render.sh"], check=True, capture_output=True,
    env=hostEnvironment(host))
  fake = FakeMariaDB()
  fake.host = host
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-worldName", questFields(title="The world is claudeperidotTest", kind="config",
    scope=["eqemu_config.json"], repository="/opt/eqemu/config", source=str(source), commits=[commit],
    rendered=[{"template": "eqemu_config.json", "file": "/opt/eqemu/akk-stack/server/eqemu_config.json",
      "filledKeys": ["server.database.password"]}], render="/opt/eqemu/claudeperidot/render.sh"))
  (repository / "plan.md").write_text(planText(["- [ ] 1. Rename the world [0001]"]), encoding="utf-8")
  harness.commitAll()
  return harness, live


def hostEnvironment(host):
  import os
  environment = dict(os.environ)
  environment["PATH"] = os.pathsep.join([str(host.shims), str(host.shell.parent), environment["PATH"]])
  return environment


def test_aConfigChangeIsJudgedByItsTemplateAndTheNamesOfTheKeysTheHostFills(repository, host):
  harness, live = configSetup(repository, host)
  assert harness.ledger.state(harness.ledger.find("0001")).label == "not applied; not staged, so its files cannot be judged"
  harness.ledger.stage("0001")
  assert harness.ledger.state(harness.ledger.find("0001")).label == "not applied"
  lines = harness.ledger.apply("0001")
  assert json.loads(live.read_text())["server"]["world"]["shortname"] == "claudeperidotTest"
  assert json.loads(live.read_text())["server"]["database"]["password"] == secret
  assert harness.ledger.state(harness.ledger.find("0001")).label == "applied"
  edited = json.loads(live.read_text())
  edited["server"]["database"]["host"] = "elsewhere"
  live.write_text(json.dumps(edited))
  assert harness.ledger.state(harness.ledger.find("0001")).label == ("partly applied (1 of 1); "
    "/opt/eqemu/akk-stack/server/eqemu_config.json does not follow its template")
  edited["server"]["database"]["host"] = "mariadb"
  edited["server"]["database"]["password"] = ""
  live.write_text(json.dumps(edited))
  assert harness.ledger.state(harness.ledger.find("0001")).label == ("partly applied (1 of 1); "
    "/opt/eqemu/akk-stack/server/eqemu_config.json lacks server.database.password")
  committed = "".join(git(repository, "show", f"{commit}") for commit in git(repository, "rev-list", "HEAD").split())
  assert secret not in committed
  assert secret not in "".join(lines)
  assert all(secret.encode() not in output for output in hostOutputs(harness))


def hostOutputs(harness):
  return [script.encode() for _, script in harness.fake.calls]
