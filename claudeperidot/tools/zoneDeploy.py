"""A zone's server side on claudeperidot, deployed and taken back through the ledger: zonewright's deployZone and undeployZone
hand it a request (JSON on standard input) and read its progress and result (JSON lines on standard output).

deploy writes two changes for the zone, each recorded before it runs: a files change installing its server maps, and a db change
whose scope is every row of the zone in zone, zone_points, doors and object, so the rows the export holds replace them all (rows
of the zone the export does not hold are removed; nothing outside the zone is written). A part the server already holds as the
export has it is left alone. An earlier deploy of the zone still applied is reverted first, so each part of a zone has one applied
deploy, whose saved state is what was there before the zone's first deploy. Both are staged and applied with the ledger, the
plan step the caller names lists them, and the EQEmu server restarts so world reads the zone table and the zone processes load
the maps and rows. undeploy reverts the zone's applied deploys, newest first, and restarts. slot reads, never writes, the zone
rows holding a zone's id or short name on each named server."""
import datetime
import json
import re
import sys

import yaml

import ledger

deployTargets = ("claudeperidot",)
slotHosts = ("claudeperidot", "peridot")
serverContainer = "akk-stack-eqemu-server-1"
shortNamePattern = re.compile(r"^[a-z0-9]{1,31}$")
# EQEmu's new-zone process: a zoneidnumber unused above 786 (eqemu-docs-v2 client/guides/custom-zone-editing.md); the client's
# AddZone takes ids up to 999 and keeps 997 for its run-time zone.
lowestNewZoneID = 787
highestZoneID = 999
runtimeZoneID = 997
rowTables = (("zone", "zone"), ("zonePoints", "zone_points"), ("doors", "doors"), ("objects", "object"))
floatTolerance = 0.01
startWait = 120
stopWait = 60


class DeployError(Exception):
  pass


def literal(value):
  if value is None:
    return "NULL"
  if isinstance(value, bool) or not isinstance(value, (int, float, str)):
    raise DeployError(f"a row value is a number, a string or null, got {value!r}")
  if isinstance(value, str):
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
  return repr(value)


def matches(column, value):
  """A condition true where column holds value as the table stores it: FLOAT columns give a written value back only within
  half a float32 step."""
  if value is None:
    return f"{column} IS NULL"
  if isinstance(value, float):
    return f"ABS({column} - ({literal(value)})) < {floatTolerance}"
  return f"{column} = {literal(value)}"


def validZone(zone, keys=("shortName", "zoneId", "longName", "template")):
  if not isinstance(zone, dict) or set(zone) != set(keys):
    raise DeployError(f"the request's zone is {{{', '.join(keys)}}}, got {zone!r}")
  if not (isinstance(zone["shortName"], str) and shortNamePattern.match(zone["shortName"])):
    raise DeployError(f"shortName is 1 to 31 lowercase letters and digits, got {zone['shortName']!r}")
  zoneID = zone["zoneId"]
  if isinstance(zoneID, bool) or not isinstance(zoneID, int) or not lowestNewZoneID <= zoneID <= highestZoneID or zoneID == runtimeZoneID:
    raise DeployError(f"a deployed zone's id is a new zone's: {lowestNewZoneID} to {highestZoneID} but not {runtimeZoneID}, got {zoneID!r}")
  if "template" in zone and (not (isinstance(zone["template"], str) and shortNamePattern.match(zone["template"])) or zone["template"] == zone["shortName"]):
    raise DeployError(f"template is another zone's short name, got {zone['template']!r}")
  return zone


def zoneScope(zone):
  """Every row of the zone in the four tables: the db change's scope, each where matching them before and after apply."""
  short, zoneID = literal(zone["shortName"]), zone["zoneId"]
  return [
    {"table": "zone", "where": f"short_name = {short}"},
    {"table": "zone_points", "where": f"zone = {short}"},
    {"table": "doors", "where": f"zone = {short}"},
    {"table": "object", "where": f"zoneid = {zoneID}"},
  ]


def zoneInsert(zone, row):
  """The zone row, its written columns as the export gives them and every other column copied from the template's version-0
  row, as the export's own SQL inserts a new zone's row."""
  written, copied = row["written"], row["copied"]
  if set(written) & set(copied) or not written:
    raise DeployError("the zone row's written and copied columns overlap or it writes none")
  if written.get("short_name") != zone["shortName"] or written.get("zoneidnumber") != zone["zoneId"] or written.get("version") != 0 \
      or written.get("long_name") != zone["longName"]:
    raise DeployError("the zone row's short_name, zoneidnumber, version and long_name are not the zone's own, version 0")
  columns = list(written) + list(copied)
  selected = [literal(written[column]) for column in written] + [f"templateRow.{column}" for column in copied]
  return (f"INSERT INTO `zone` ({', '.join(f'`{column}`' for column in columns)}) SELECT {', '.join(selected)} FROM `zone` AS "
    f"templateRow WHERE templateRow.short_name = {literal(zone['template'])} AND templateRow.version = 0")


def valuesInsert(table, rows):
  columns = list(rows[0])
  if any(list(row) != columns for row in rows):
    raise DeployError(f"the {table} rows do not all hold the same columns")
  values = ",\n".join("  (" + ", ".join(literal(row[column]) for column in columns) + ")" for row in rows)
  return f"INSERT INTO `{table}` ({', '.join(f'`{column}`' for column in columns)}) VALUES\n{values}"


def deployStatements(request):
  """apply.sql's statements: each table's rows of the zone deleted, then the export's inserted."""
  zone, rows = request["zone"], request["rows"]
  statements = []
  for (key, table), entry in zip(rowTables, zoneScope(zone)):
    statements.append(f"DELETE FROM `{table}` WHERE ({entry['where']})")
    if key == "zone":
      statements.append(zoneInsert(zone, rows["zone"]))
    elif rows[key]:
      statements.append(valuesInsert(table, rows[key]))
  return statements


def deployCheck(request):
  """check.sql: 1 exactly when the zone's rows are the export's: one zone row holding every written column, and per table as
  many rows as the export has, each found by every column it writes."""
  zone, rows = request["zone"], request["rows"]
  scope = {entry["table"]: entry["where"] for entry in zoneScope(zone)}
  conditions = [f"(SELECT COUNT(*) FROM zone WHERE {scope['zone']}) = 1"]
  conditions.append(f"(SELECT COUNT(*) FROM zone WHERE {' AND '.join(matches(column, value) for column, value in rows['zone']['written'].items())}) = 1")
  for key, table in rowTables[1:]:
    conditions.append(f"(SELECT COUNT(*) FROM {table} WHERE {scope[table]}) = {len(rows[key])}")
    conditions += [f"(SELECT COUNT(*) FROM {table} WHERE {' AND '.join(matches(column, value) for column, value in row.items())}) >= 1"
      for row in rows[key]]
  return "SELECT (" + "\n  AND ".join(conditions) + ") AS applied"


def changeName(short, part):
  return "deploy" + short[0].upper() + short[1:] + part


def stamp(moment):
  return moment.strftime("%Y%m%d-%H%M%S")


def commonFields(request, title, why, activation):
  fields = {
    "title": title, "why": why, "disposition": request["disposition"], "significance": "minor", "ownerClearance": request["ownerClearance"],
    "activation": activation, "production": "none", "commits": [], "author": request["author"],
  }
  return fields


def rowsChange(request):
  zone = request["zone"]
  rows = request["rows"]
  counts = f"{len(rows['zonePoints'])} zone_points, {len(rows['doors'])} doors and {len(rows['objects'])} object rows"
  fields = commonFields(
    request, f"{zone['shortName']} (zone {zone['zoneId']}) holds exactly its game export's zone row and {counts}",
    f"zonewright's deployZone: the server side of {zone['shortName']} as its game export ({request['export']['manifest']}, archive "
    f"SHA-256 {request['export']['archiveSHA256']}) writes it. The scope is every row of the zone in zone, zone_points, doors and "
    f"object; apply deletes them and inserts the export's (the zone row copying every column the export does not write from "
    f"{zone['template']}'s version-0 row), so rows of the zone the export does not hold are removed and nothing outside the zone is "
    "written; the export's own guards are the ledger's scope guards here.",
    "restart world and the zone processes (world reads the zone table at boot; a zone process loads its zone points, doors and objects at boot)",
  )
  fields |= {"kind": "db", "scope": zoneScope(zone)}
  files = {
    "apply.sql": f"-- deployZone: {zone['shortName']}'s rows from its game export.\n" + "".join(f"{statement};\n" for statement in deployStatements(request)),
    "check.sql": deployCheck(request) + ";\n",
  }
  return fields, files


def mapsChange(request):
  zone = request["zone"]
  entries = request["files"]
  fields = commonFields(
    request, f"claudeperidot's maps folder holds {zone['shortName']}'s server maps from its game export",
    f"zonewright's deployZone: the zone process for {zone['shortName']} loads its collision (.map), water regions (.wtr) and NPC nav "
    f"(.nav) from the maps folder by the lowercase short name; these are the game export's server\\maps ({request['export']['manifest']}), "
    "each installed owned as its folder is, mode 644.",
    "restart the zone processes (a zone process loads its maps when it boots)",
  )
  fields |= {"kind": "files", "scope": [entry["path"] for entry in entries], "files": entries}
  return fields, {}


def writeChange(folder, fields, files):
  problems = ledger.validateChangeFields(fields)
  if problems:
    raise DeployError(f"{folder.name}: " + "; ".join(problems))
  folder.mkdir()
  (folder / "change.yaml").write_text(yaml.safe_dump(fields, sort_keys=False, width=120), encoding="utf-8")
  for name, text in files.items():
    (folder / name).write_text(text, encoding="utf-8", newline="\n")


def deployChanges(theLedger, short):
  """The zone's deploy changes in the ledger, by part (Rows, Maps), oldest first."""
  found = {"Rows": [], "Maps": []}
  for change in ledger.loadChanges(theLedger.folder):
    for part in found:
      if change.name == changeName(short, part):
        found[part].append(change)
  return found


def editPlan(text, stepNumber, added, removed):
  """plan.md with stepNumber's bracket naming added after its own changes, removed taken out of every step, each edited step
  unticked unless all its changes are applied (added never are yet), and In progress and Next naming the first two unticked steps."""
  steps = ledger.readPlanSteps(text)
  if not any(step.number == stepNumber for step in steps):
    raise DeployError(f"plan.md has no step {stepNumber}")
  lines = text.split("\n")
  for step in steps:
    changes = tuple(number for number in step.changes if number not in removed)
    if step.number == stepNumber:
      changes += tuple(number for number in added if number not in changes)
    if changes != step.changes:
      edited = ledger.PlanStep(step.line, step.number, step.ticked and not (set(changes) & set(added)), step.text, changes)
      lines[step.line] = f"- [{'x' if edited.ticked else ' '}] " + edited.describe()
  return ledger.rewritePlan("\n".join(lines), {}, set())


def zoneSlotRows(server, zone):
  """The version rows of zone holding the zone's id or its short name: [{zoneidnumber, version, short_name}]."""
  rows = server.sql(f"SELECT zoneidnumber, version, short_name FROM zone WHERE (zoneidnumber = {zone['zoneId']} OR short_name = "
    f"{literal(zone['shortName'])});\n")
  found = []
  for row in rows:
    if len(row) != 3:
      raise DeployError(f"the zone table gave the row {row!r}")
    found.append({"zoneidnumber": int(row[0]), "version": int(row[1]), "short_name": row[2]})
  return found


def slotConflicts(rows, zone):
  return [row for row in rows if (row["zoneidnumber"] == zone["zoneId"]) != (row["short_name"] == zone["shortName"])]


def processes(server):
  return server.run(f"sudo -n docker exec {serverContainer} ps -eo args=").splitlines()


def running(lines):
  return {
    "launcher": sum("eqemu-server:launcher" in line for line in lines), "world": sum(line.endswith("/bin/world") for line in lines),
    "zone": sum("/bin/zone" in line for line in lines),
  }


def restartServer(server):
  """Restarts EQEmu on claudeperidot with Spire's launcher, as the host's processes run it: stop, wait until no world or zone
  process is left, start detached, and wait for world and a zone process. Returns what runs afterwards."""
  stopped = server.run(f"sudo -n docker exec {serverContainer} sh -c 'cd ~/server && timeout 120 ./bin/spire "
    "eqemu-server:launcher stop' >/dev/null 2>&1; echo $?").strip()
  server.run(f"for i in $(seq 1 {stopWait}); do sudo -n docker exec {serverContainer} ps -eo args= | grep -q "
    "'/bin/\\(world\\|zone\\)' || break; sleep 1; done; true")
  left = running(processes(server))
  if left["world"] or left["zone"]:
    raise DeployError(f"the launcher's stop (exit {stopped}) left {left['world']} world and {left['zone']} zone processes running")
  server.run(f"sudo -n docker exec -d {serverContainer} sh -c 'cd ~/server && ./bin/spire eqemu-server:launcher start'")
  server.run(f"for i in $(seq 1 {startWait}); do sudo -n docker exec {serverContainer} ps -eo args= > /tmp/zoneDeployProcesses.$$ && "
    "grep -q '/bin/world$' /tmp/zoneDeployProcesses.$$ && grep -q '/bin/zone' /tmp/zoneDeployProcesses.$$ && break; sleep 1; done; "
    "rm -f /tmp/zoneDeployProcesses.$$; true")
  after = running(processes(server))
  if not after["world"] or not after["zone"]:
    raise DeployError(f"the launcher started, but after {startWait} s {after['world']} world and {after['zone']} zone processes run")
  return {"stopExit": stopped, "running": after}


class Deployment:
  def __init__(self, theLedger, emit, now=None):
    self.ledger = theLedger
    self.emit = emit
    self.now = now or (lambda: datetime.datetime.now(datetime.UTC))
    self.step = 0

  def progress(self, total, message):
    self.step += 1
    self.emit({"progress": self.step, "total": total, "message": message})

  def requireTarget(self, request):
    if request.get("target") not in deployTargets:
      raise DeployError(f"a deploy's target is one of {list(deployTargets)} (Peridot is never written), got {request.get('target')!r}")

  def deploy(self, request):
    requireKeys(request, deployKeys)
    self.requireTarget(request)
    zone = validZone(request["zone"])
    short = zone["shortName"]
    total = 8
    self.progress(total, f"reading {short}'s id and short name in claudeperidot's zone table")
    conflicts = slotConflicts(zoneSlotRows(self.ledger.server, zone), zone)
    if conflicts:
      raise DeployError(f"claudeperidot's zone table holds {conflicts}: {short} (zone {zone['zoneId']}) needs an id and short name no other zone holds")
    planText = self.ledger.planPath.read_text(encoding="utf-8")
    editPlan(planText, request["planStep"], (), ())
    moment = self.now().replace(microsecond=0)
    folders = {
      "Maps": self.ledger.folder / "changes" / f"{stamp(moment)}-{changeName(short, 'Maps')}",
      "Rows": self.ledger.folder / "changes" / f"{stamp(moment + datetime.timedelta(seconds=1))}-{changeName(short, 'Rows')}",
    }
    for folder in folders.values():
      if folder.exists():
        raise DeployError(f"{folder.name} exists already")
    previous = deployChanges(self.ledger, short)
    self.progress(total, "writing the changes and reading what the server holds now")
    superseded = []
    try:
      writeChange(folders["Maps"], *mapsChange(request))
      writeChange(folders["Rows"], *rowsChange(request))
      needed = [part for part in ("Maps", "Rows") if not self.ledger.state(ledger.loadChange(folders[part])).isApplied]
      for part in ("Maps", "Rows"):
        if part not in needed:
          removeFolder(folders[part])
      if not needed:
        return {"deployed": [], "unchanged": ["Maps", "Rows"], "reverted": [], "restart": None}
      self.progress(total, "reverting the zone's earlier deploys the new ones replace")
      for part in reversed(needed):
        for change in reversed(previous[part]):
          if self.ledger.state(change).isApplied:
            self.ledger.revert(change.number)
            superseded.append(change)
      changes = [ledger.loadChange(folders[part]) for part in needed]
      self.ledger.planPath.write_text(editPlan(self.ledger.planPath.read_text(encoding="utf-8"), request["planStep"],
        [change.number for change in changes], {change.number for change in superseded}), encoding="utf-8")
      self.ledger.record([change.folder for change in changes] + [self.ledger.planPath],
        f"deployZone {short}: write {', '.join(change.identifier for change in changes)} on plan step {request['planStep']}")
    except BaseException:
      # Changes never committed never ran: take them back so the ledger holds only what was recorded.
      for folder in folders.values():
        if folder.exists() and not ledger.git(self.ledger.folder, "ls-files", "--", self.ledger.relative(folder)).strip():
          removeFolder(folder)
      if ledger.git(self.ledger.folder, "status", "--porcelain", "--", "plan.md").strip():
        ledger.git(self.ledger.folder, "checkout", "--", "plan.md")
      raise
    for change in changes:
      self.progress(total, f"staging {change.identifier}")
      self.ledger.stage(change.number)
      self.progress(total, f"applying {change.identifier}")
      self.ledger.apply(change.number)
    self.progress(total, "restarting the EQEmu server so world and the zone processes load the zone")
    restart = restartServer(self.ledger.server)
    return {"deployed": [change.identifier for change in changes], "unchanged": [part for part in ("Maps", "Rows") if part not in needed],
      "reverted": [change.identifier for change in superseded], "restart": restart}

  def undeploy(self, request):
    requireKeys(request, undeployKeys)
    self.requireTarget(request)
    zone = validZone(request["zone"], ("shortName", "zoneId"))
    found = deployChanges(self.ledger, zone["shortName"])
    applied = sorted((change for part in found.values() for change in part if self.ledger.state(change).isApplied),
      key=lambda change: change.number, reverse=True)
    if not applied:
      raise DeployError(f"no deployZone change of {zone['shortName']} is applied on claudeperidot")
    total = len(applied) + 1
    for change in applied:
      self.progress(total, f"reverting {change.identifier}")
      self.ledger.revert(change.number)
    self.progress(total, "restarting the EQEmu server so world and the zone processes drop the zone")
    return {"reverted": [change.identifier for change in applied], "restart": restartServer(self.ledger.server)}


deployKeys = {"command", "target", "zone", "rows", "files", "export", "planStep", "author", "ownerClearance", "disposition"}
undeployKeys = {"command", "target", "zone"}
slotKeys = {"command", "hosts", "zone"}


def requireKeys(request, keys):
  if not isinstance(request, dict) or set(request) != keys:
    raise DeployError(f"the request holds {sorted(keys)}, got {sorted(request) if isinstance(request, dict) else request!r}")
  if "rows" in keys:
    rows = request["rows"]
    if not isinstance(rows, dict) or set(rows) != {key for key, _ in rowTables} or set(rows["zone"]) != {"written", "copied"} \
        or not all(isinstance(rows[key], list) and all(isinstance(row, dict) and row for row in rows[key]) for key, _ in rowTables[1:]):
      raise DeployError("rows is {zone: {written, copied}, zonePoints, doors, objects}, each a list of rows by column")
    if request["disposition"] not in ("fixture", "exploration"):
      raise DeployError(f"a deploy's disposition is fixture or exploration, got {request['disposition']!r}")
    if type(request["planStep"]) is not int:
      raise DeployError(f"planStep is a plan step's number, got {request['planStep']!r}")
    if not isinstance(request["export"], dict) or set(request["export"]) != {"manifest", "archiveSHA256"}:
      raise DeployError("export is {manifest, archiveSHA256}")


def removeFolder(folder):
  for path in sorted(folder.iterdir(), reverse=True):
    path.unlink()
  folder.rmdir()


def slot(request, openServer):
  """Each named server's zone rows holding the zone's id or short name, read only, and the conflicts among them."""
  requireKeys(request, slotKeys)
  hosts = request.get("hosts")
  if not isinstance(hosts, list) or not hosts or any(host not in slotHosts for host in hosts) or len(set(hosts)) != len(hosts):
    raise DeployError(f"hosts is a list of distinct servers from {list(slotHosts)}, got {hosts!r}")
  zone = request["zone"]
  if not (isinstance(zone, dict) and isinstance(zone.get("shortName"), str) and shortNamePattern.match(zone["shortName"])
      and isinstance(zone.get("zoneId"), int) and not isinstance(zone.get("zoneId"), bool)):
    raise DeployError(f"slot reads a zone's {{shortName, zoneId}}, got {zone!r}")
  result = {}
  for host in hosts:
    rows = zoneSlotRows(openServer(host), zone)
    result[host] = {"rows": rows, "conflicts": slotConflicts(rows, zone)}
  return result


def main():
  def emit(line):
    print(json.dumps(line), flush=True)

  try:
    ledger.configureLogging()
    request = json.loads(sys.stdin.read())
    command = request.get("command")
    if command == "slot":
      result = slot(request, ledger.openServer)
    elif command in ("deploy", "undeploy"):
      deployment = Deployment(ledger.Ledger(ledger.ledgerFolder, ledger.openServer, ledger.pushBranch), emit)
      result = getattr(deployment, command)(request)
    else:
      raise DeployError(f"command is slot, deploy or undeploy, got {command!r}")
  except (DeployError, ledger.LedgerError, json.JSONDecodeError) as error:
    ledger.log.error("zoneDeploy failed: %s", error)
    emit({"error": f"{type(error).__name__}: {error}"})
    return 1
  emit({"result": result})
  return 0


if __name__ == "__main__":
  sys.exit(main())
