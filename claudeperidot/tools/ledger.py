"""The claudeperidot change ledger: every change to claudeperidot or to Claude's workstation side is written, staged and
committed before it runs, and its state is always read live, never stored."""
import argparse
import base64
import datetime
import hashlib
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ledgerFolder = Path(__file__).resolve().parent.parent
hostName = "claudeperidot"
mariadbContainer = "akk-stack-mariadb-1"
databaseName = "peq"
# Every write the ledger makes carries this GTID domain, so drift can tell the ledger's binary-log writes from all others.
ledgerGTIDDomain = 7
hostPaths = {
  "akkStack": "/opt/eqemu/akk-stack",
  "code": "/opt/eqemu/akk-stack/code",
  "quests": "/opt/eqemu/akk-stack/server/quests",
  "maps": "/opt/eqemu/akk-stack/server/maps",
  "dumps": "/opt/eqemu/baseline",
}
peridotQuestsPath = "/opt/eqemu/akk-stack/server/quests"

# EQEmu's player, state, login and bot groups (common/database_schema.h at ae4bd2833): play writes them constantly.
playTables = frozenset("""
  account account_ip account_flags account_rewards adventure_details adventure_stats buyer buyer_buy_lines buyer_trade_items
  char_recipe_list character_activities character_alt_currency character_alternate_abilities character_auras
  character_bandolier character_bind character_buffs character_corpse_items character_corpses character_currency
  character_data character_disciplines character_enabledtasks character_expedition_lockouts character_exp_modifiers
  character_evolving_items character_inspect_messages character_instance_safereturns character_item_recast
  character_languages character_leadership_abilities character_material character_memmed_spells character_parcels
  character_parcels_containers character_pet_buffs character_pet_info character_pet_inventory character_pet_name
  character_peqzone_flags character_potionbelt character_skills character_spells character_stats_record
  character_task_timers character_tasks character_tribute completed_tasks data_buckets discovered_items faction_values
  friends guild_bank guild_members guild_permissions guild_ranks guild_relations guild_tributes guilds instance_list_player
  inventory inventory_snapshots keyring mail petitions player_titlesets quest_globals sharedbank spell_buckets spell_globals
  timers trader trader_audit zone_flags
  adventure_members banned_ips bug_reports bugs completed_shared_task_activity_state completed_shared_task_members
  completed_shared_tasks discord_webhooks dynamic_zone_lockouts dynamic_zone_members dynamic_zones gm_ips group_id
  group_leaders instance_list ip_exemptions lfguild merc_buffs merchantlist_temp mercs object_contents raid_details
  raid_leaders raid_members reports respawn_times saylink server_scheduled_events spawn2_disabled player_event_aa_purchase
  player_event_killed_npc player_event_killed_named_npc player_event_killed_raid_npc player_event_log_settings
  player_event_logs player_event_loot_items player_event_merchant_purchase player_event_merchant_sell player_event_npc_handin
  player_event_npc_handin_entries player_event_speech player_event_trade player_event_trade_entries
  shared_task_activity_state shared_task_dynamic_zones shared_task_members shared_tasks zone_state_spawns
  login_accounts login_api_tokens login_server_admins login_server_list_types login_world_servers
  bot_blocked_buffs bot_buffs bot_command_settings bot_create_combinations bot_data bot_heal_rotation_members
  bot_heal_rotation_targets bot_heal_rotations bot_inspect_messages bot_inventories bot_owner_options bot_pet_buffs
  bot_pet_inventories bot_pets bot_settings bot_spell_casting_chances bot_spell_settings bot_spells_entries bot_stances
  bot_timers
""".split())
contentTables = frozenset("""
  aa_ability aa_rank_effects aa_rank_prereqs aa_ranks adventure_template adventure_template_entry
  adventure_template_entry_flavor alternate_currency auras base_data blocked_spells books char_create_combinations
  char_create_point_allocations damageshieldtypes doors dynamic_zone_templates faction_association faction_base_data
  faction_list faction_list_mod fishing forage global_loot graveyard grid grid_entries ground_spawns horses items
  items_evolving_details ldon_trap_entries ldon_trap_templates lootdrop lootdrop_entries loottable loottable_entries
  merchantlist npc_emotes npc_faction npc_faction_entries npc_scale_global_base npc_spells npc_spells_effects
  npc_spells_effects_entries npc_spells_entries npc_types npc_types_tint object pets pets_beastlord_data pets_equipmentset
  pets_equipmentset_entries skill_caps spawn2 spawn_conditions spawnentry spawngroup spells_new start_zones starting_items
  task_activities tasks tasksets tradeskill_recipe tradeskill_recipe_entries traps tribute_levels tributes
  veteran_reward_templates zone zone_points
""".split())

changeFolderPattern = re.compile(r"^(\d{4})-([a-z][A-Za-z0-9]*)$")
templateFolderName = "template"
commonFields = ("title", "why", "kind", "scope", "disposition", "significance", "ownerClearance", "activation", "production",
  "commits")
kindFields = {
  "db": (set(), {"schemaSteps"}),
  "quest": ({"repository"}, set()),
  "config": ({"repository"}, set()),
  "client": ({"steps", "undo", "checks"}, set()),
}
choices = {
  "disposition": ("fixture", "exploration", "production"),
  "significance": ("minor", "significant"),
  "ownerClearance": ("pending", "cleared"),
}
productionForms = {"rows": ("contentSQL",), "schema": ("customMigration",), "quest": ("questCommit",),
  "config": ("runbook", "provisioner"), "client": ()}
tableNamePattern = re.compile(r"^[A-Za-z0-9_]+$")
commitPattern = re.compile(r"^[0-9a-f]{7,40}$")
hostFilePattern = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
hostFolderPattern = re.compile(r"^/[A-Za-z0-9_./-]+$")
clientPathPattern = re.compile(r"^([A-Za-z]:\\|~\\)")
hashPattern = re.compile(r"^[0-9a-f]{64}$")
encodedValuePattern = re.compile(r"^(N|V(?:[0-9A-F]{2})*)$")
stepPattern = re.compile(r"^- \[( |x)\] (\d+)\. (.+?)(?: \[(\d{4}(?:, \d{4})*)\])?$")

integerTypes = {"tinyint", "smallint", "mediumint", "int", "bigint"}
decimalTypes = {"decimal"}
floatTypes = {"float", "double"}
bitTypes = {"bit"}
binaryTypes = {"binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob"}
textTypes = {"char", "varchar", "tinytext", "text", "mediumtext", "longtext", "enum", "set"}
temporalTypes = {"date", "datetime", "timestamp", "time", "year"}
asciiCompatibleCharsets = {"ascii", "latin1", "utf8", "utf8mb3", "utf8mb4"}
numberPatterns = {
  "integer": re.compile(r"^-?\d+$"),
  "decimal": re.compile(r"^-?\d+(\.\d+)?$"),
  "float": re.compile(r"^-?\d+(\.\d+)?(e[+-]?\d+)?$", re.IGNORECASE),
}
rowTransactionKeywords = {"INSERT", "UPDATE", "DELETE", "REPLACE"}
log = logging.getLogger("ledger")


class LedgerError(Exception):
  pass


class uniqueKeyLoader(yaml.SafeLoader):
  pass


def constructUniqueMapping(loader, node, deep=False):
  seenKeys = set()
  for keyNode, _ in node.value:
    key = loader.construct_object(keyNode, deep=deep)
    if key in seenKeys:
      raise LedgerError(f"duplicate key {key!r} on line {keyNode.start_mark.line + 1}")
    seenKeys.add(key)
  return loader.construct_mapping(node, deep)


uniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, constructUniqueMapping)


def readYAML(path):
  try:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=uniqueKeyLoader)
  except yaml.YAMLError as error:
    raise LedgerError(f"{path} is not valid YAML: {error}") from error


def isOneLine(value):
  return isinstance(value, str) and bool(value.strip()) and "\n" not in value


def changeForm(fields):
  if fields.get("kind") == "db":
    return "schema" if "schemaSteps" in fields else "rows"
  return fields.get("kind")


def clientCheckProblem(check):
  if not isinstance(check, dict) or not isOneLine(check.get("path")) or not clientPathPattern.match(check["path"]):
    return f"a check needs a path that is absolute or starts with ~\\: {check!r}"
  keys = set(check)
  if keys == {"path", "exists"} and check["exists"] in ("file", "directory"):
    return None
  if keys == {"path", "exists", "target"} and check["exists"] == "junction" and isOneLine(check["target"]) \
      and clientPathPattern.match(check["target"]):
    return None
  if keys == {"path", "sha256"} and isinstance(check["sha256"], str) and hashPattern.match(check["sha256"]):
    return None
  if keys == {"path", "section", "key", "value"} and all(isinstance(check[name], str) and check[name] != ""
      for name in ("section", "key")) and isinstance(check["value"], str):
    return None
  return (f"check {check!r} is not one of: exists file or directory; exists junction with a target; a lowercase sha256; "
    "an ini section, key and value")


def scopeProblems(fields, form):
  scope = fields["scope"]
  if not isinstance(scope, list) or not scope:
    return ["scope must be a non-empty list"]
  problems = []
  for entry in scope:
    if form == "rows":
      if not isinstance(entry, dict) or set(entry) != {"table", "where"} or not isinstance(entry["table"], str) \
          or not tableNamePattern.match(entry["table"]) or not isinstance(entry["where"], str) or not entry["where"].strip():
        problems.append(f"a db scope entry is a table name and a where predicate: {entry!r}")
    elif form == "schema":
      if not isinstance(entry, dict) or set(entry) != {"table"} or not isinstance(entry["table"], str) \
          or not tableNamePattern.match(entry["table"]):
        problems.append(f"a schema scope entry is a table name: {entry!r}")
    elif form in ("quest", "config"):
      if not isinstance(entry, str) or not hostFilePattern.match(entry) or ".." in entry.split("/"):
        problems.append(f"a {form} scope entry is a file path inside the repository: {entry!r}")
    elif not isinstance(entry, str) or not clientPathPattern.match(entry):
      problems.append(f"a client scope entry is a path that is absolute or starts with ~\\: {entry!r}")
  return problems


def validateChangeFields(fields):
  """The problems with a change.yaml's fields; an empty list means it is valid."""
  if not isinstance(fields, dict):
    return ["change.yaml must be a mapping of fields"]
  kind = fields.get("kind")
  problems = []
  if kind in kindFields:
    required, optional = kindFields[kind]
    allowed = set(commonFields) | required | optional
  else:
    problems.append(f"kind must be one of {', '.join(kindFields)}, not {kind!r}")
    required = set()
    allowed = set(commonFields).union(*(set(needed) | extra for needed, extra in kindFields.values()))
  missing = [name for name in (*commonFields, *sorted(required)) if name not in fields]
  if missing:
    problems.append(f"missing {', '.join(missing)}")
  unknown = sorted(set(fields) - allowed)
  if unknown:
    problems.append(f"unknown fields {', '.join(unknown)}")
  for name in ("title", "activation"):
    if name in fields and not isOneLine(fields[name]):
      problems.append(f"{name} must be one non-empty line")
  if "why" in fields and not (isinstance(fields["why"], str) and fields["why"].strip()):
    problems.append("why must be non-empty text")
  for name, allowedValues in choices.items():
    if name in fields and fields[name] not in allowedValues:
      problems.append(f"{name} must be one of {', '.join(allowedValues)}, not {fields[name]!r}")
  if kind not in kindFields or problems:
    return problems
  form = changeForm(fields)
  if fields["disposition"] in ("fixture", "exploration") and fields["production"] != "none":
    problems.append(f"{fields['disposition']} changes never reach Peridot: production must be none")
  if fields["disposition"] == "production":
    if form == "client":
      problems.append("a client change stays on the workstation and cannot be production")
    elif fields["production"] not in productionForms[form]:
      problems.append(f"a production {'row' if form == 'rows' else form} change reaches Peridot as "
        f"{' or '.join(productionForms[form])}")
  commits = fields["commits"]
  if not isinstance(commits, list) or not all(isinstance(commit, str) and commitPattern.match(commit) for commit in commits):
    problems.append("commits must be a list of lowercase commit SHAs")
  elif kind in ("db", "client") and commits:
    problems.append(f"a {kind} change has no commits")
  elif kind in ("quest", "config") and not commits:
    problems.append(f"a {kind} change names its commits, oldest first")
  if form == "schema" and (type(fields["schemaSteps"]) is not int or fields["schemaSteps"] < 1):
    problems.append("schemaSteps must be a whole number of steps, at least 1")
  if kind in ("quest", "config") and not (isinstance(fields["repository"], str) and hostFolderPattern.match(fields["repository"])):
    problems.append("repository must be the checkout's absolute path on the host")
  if kind == "client":
    for name in ("steps", "undo"):
      if not isinstance(fields[name], list) or not fields[name] or not all(isinstance(step, str) and step.strip()
          for step in fields[name]):
        problems.append(f"{name} must be a non-empty list of PowerShell scripts")
    if not isinstance(fields["checks"], list) or not fields["checks"]:
      problems.append("checks must be a non-empty list")
    else:
      problems.extend(problem for problem in map(clientCheckProblem, fields["checks"]) if problem)
  problems.extend(scopeProblems(fields, form))
  return problems


@dataclass(frozen=True)
class Change:
  folder: Path
  fields: dict

  @property
  def identifier(self):
    return self.folder.name

  @property
  def number(self):
    return self.folder.name[:4]

  @property
  def name(self):
    return self.folder.name[5:]

  @property
  def kind(self):
    return self.fields["kind"]

  @property
  def form(self):
    return changeForm(self.fields)

  @property
  def steps(self):
    return self.fields["schemaSteps"] if self.form == "schema" else 1

  @property
  def tables(self):
    return [entry["table"] for entry in self.fields["scope"]]


def loadChange(folder):
  if not changeFolderPattern.match(folder.name):
    raise LedgerError(f"{folder.name} is not a change folder name (NNNN-drinkingCamelCaseName)")
  path = folder / "change.yaml"
  if not path.is_file():
    raise LedgerError(f"{folder.name} has no change.yaml")
  fields = readYAML(path)
  problems = validateChangeFields(fields)
  if problems:
    raise LedgerError(f"{folder.name}/change.yaml: " + "; ".join(problems))
  return Change(folder, fields)


def loadChanges(folder):
  changes = {}
  for changeFolder in sorted((folder / "changes").iterdir()):
    if changeFolder.name == templateFolderName:
      continue
    if not changeFolder.is_dir():
      raise LedgerError(f"changes/{changeFolder.name} is not a change folder")
    change = loadChange(changeFolder)
    if change.number in changes:
      raise LedgerError(f"{changes[change.number].identifier} and {change.identifier} share number {change.number}")
    changes[change.number] = change
  return list(changes.values())


def sqlPieces(text):
  """(kind, text) pieces of SQL in order: code, quoted, comment, or separator (a semicolon)."""
  index = 0
  length = len(text)
  codeStart = 0
  while index < length:
    character = text[index]
    end = None
    kind = None
    if character in "'\"`":
      kind = "quoted"
      end = index + 1
      while True:
        if end >= length:
          raise LedgerError("an SQL quoted string or name is never closed")
        if text[end] == "\\" and character != "`":
          end += 2
        elif text[end] == character and text.startswith(character * 2, end):
          end += 2
        elif text[end] == character:
          end += 1
          break
        else:
          end += 1
    elif text.startswith("--", index) and (index + 2 == length or text[index + 2] in " \t\r\n") or character == "#":
      kind = "comment"
      end = text.find("\n", index)
      end = length if end == -1 else end
    elif text.startswith("/*", index):
      kind = "comment"
      end = text.find("*/", index + 2)
      if end == -1:
        raise LedgerError("an SQL comment is never closed")
      end += 2
    elif character == ";":
      kind = "separator"
      end = index + 1
    if kind is None:
      index += 1
      continue
    if codeStart < index:
      yield "code", text[codeStart:index]
    yield kind, text[index:end]
    index = codeStart = end
  if codeStart < length:
    yield "code", text[codeStart:]


def firstKeyword(statement):
  for kind, text in sqlPieces(statement):
    if kind == "code" and text.strip():
      match = re.match(r"\s*([A-Za-z]+)", text)
      return match.group(1).upper() if match else ""
    if kind == "quoted":
      return ""
  return ""


def splitStatements(text):
  """The statements of an SQL script, without their semicolons or comments, so each can be embedded or re-terminated."""
  statements = []
  current = []
  for kind, piece in sqlPieces(text):
    if kind == "separator":
      statements.append("".join(current).strip())
      current = []
    else:
      current.append(" " if kind == "comment" else piece)
  statements.append("".join(current).strip())
  statements = [statement for statement in statements if statement]
  for statement in statements:
    if firstKeyword(statement) == "DELIMITER":
      raise LedgerError("DELIMITER is not supported in a change's SQL; write one statement per semicolon")
  return statements


def quoteIdentifier(name):
  return "`" + name.replace("`", "``") + "`"


@dataclass(frozen=True)
class Column:
  name: str
  dataType: str
  charset: str

  def asRecord(self):
    return {"name": self.name, "type": self.dataType, "charset": self.charset}


def columnFromRecord(record):
  return Column(record["name"], record["type"], record["charset"])


def fetchExpression(column):
  """An expression giving the column's value as N for NULL or V and the hex of its bytes (text and binary) or text form."""
  name = quoteIdentifier(column.name)
  if column.dataType in binaryTypes or column.dataType in textTypes - {"enum", "set"}:
    inner = f"HEX({name})"
  elif column.dataType in {"enum", "set"}:
    inner = f"HEX(CONCAT({name}))"
  elif column.dataType in bitTypes:
    inner = f"HEX(CAST({name} + 0 AS CHAR))"
  elif column.dataType in integerTypes | decimalTypes | floatTypes | temporalTypes:
    inner = f"HEX(CAST({name} AS CHAR))"
  else:
    raise LedgerError(f"column {column.name} has type {column.dataType}, which the ledger cannot save exactly")
  return f"IF({name} IS NULL, 'N', CONCAT('V', {inner}))"


def literal(column, encoded):
  """The SQL literal that stores exactly the value saved as encoded."""
  if encoded == "N":
    return "NULL"
  data = bytes.fromhex(encoded[1:])
  if column.dataType in binaryTypes:
    return f"X'{data.hex().upper()}'"
  if column.dataType in textTypes:
    if not column.charset:
      raise LedgerError(f"text column {column.name} has no character set")
    if column.charset in asciiCompatibleCharsets and all(0x20 <= byte <= 0x7E and byte != 0x5C for byte in data):
      return "'" + data.decode("ascii").replace("'", "''") + "'"
    return f"_{column.charset} X'{data.hex().upper()}'"
  try:
    text = data.decode("ascii")
  except UnicodeDecodeError as error:
    raise LedgerError(f"column {column.name} ({column.dataType}) holds non-ASCII text {data!r}") from error
  if column.dataType in integerTypes | bitTypes:
    pattern = numberPatterns["integer"]
  elif column.dataType in decimalTypes:
    pattern = numberPatterns["decimal"]
  elif column.dataType in floatTypes:
    pattern = numberPatterns["float"]
  elif column.dataType in temporalTypes:
    if not all(character not in "'\\" and " " <= character <= "~" for character in text):
      raise LedgerError(f"column {column.name} ({column.dataType}) holds {text!r}, not a date or time")
    return f"'{text}'"
  else:
    raise LedgerError(f"column {column.name} has type {column.dataType}, which the ledger cannot save exactly")
  if not pattern.match(text):
    raise LedgerError(f"column {column.name} ({column.dataType}) holds {text!r}, not a number")
  return text


def rowText(row):
  return ",".join(row)


def tableDigest(rows):
  """What SHA2(GROUP_CONCAT(row ORDER BY BINARY row SEPARATOR '|'), 256) gives on the server: NULL for no rows."""
  if not rows:
    return None
  return hashlib.sha256("|".join(sorted(rowText(row) for row in rows)).encode("ascii")).hexdigest()


def scopeChecksum(tables):
  lines = [f"{table['table']}\t{','.join(column['name'] for column in table['columns'])}\t{tableDigest(table['rows']) or 'NULL'}"
    for table in tables]
  return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def digestCondition(table, where, columns, digest):
  rowExpression = "CONCAT_WS(',', " + ", ".join(fetchExpression(column) for column in columns) + ")"
  expected = "NULL" if digest is None else f"'{digest}'"
  return (f"(SELECT SHA2(GROUP_CONCAT(ledgerRow ORDER BY BINARY ledgerRow SEPARATOR '|'), 256) FROM (SELECT {rowExpression} "
    f"AS ledgerRow FROM {quoteIdentifier(table)} WHERE ({where})) AS ledgerRows) <=> {expected}")


def guardBlock(condition, message):
  """A compound statement that fails the script, and so its open transaction, unless condition is true."""
  return ("DELIMITER //\nBEGIN NOT ATOMIC\n"
    f"  IF ({condition}) IS NOT TRUE THEN\n"
    f"    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}';\n"
    "  END IF;\nEND//\nDELIMITER ;\n")


def rowsGuards(tables, digests, message):
  return "".join(guardBlock(digestCondition(table["table"], table["where"], [columnFromRecord(column) for column in
    table["columns"]], digest), message) for table, digest in zip(tables, digests))


def checkQuery(checkStatement):
  return f"SELECT applied FROM ({checkStatement}) AS ledgerCheck"


def revertScript(tables):
  lines = ["-- Written by ledger.py stage from the rows in saved.json; ledger.py revert runs it to put back exactly those rows."]
  for table in tables:
    columns = [columnFromRecord(record) for record in table["columns"]]
    lines.append(f"DELETE FROM {quoteIdentifier(table['table'])} WHERE ({table['where']});")
    if table["rows"]:
      names = ", ".join(quoteIdentifier(column.name) for column in columns)
      values = ",\n".join("  (" + ", ".join(literal(column, value) for column, value in zip(columns, row)) + ")"
        for row in table["rows"])
      lines.append(f"INSERT INTO {quoteIdentifier(table['table'])} ({names}) VALUES\n{values};")
  return "\n".join(lines) + "\n"


def unescapeBatchField(text):
  replacements = {"n": "\n", "t": "\t", "0": "\0", "\\": "\\"}
  return re.sub(r"\\(.)", lambda match: replacements.get(match.group(1), match.group(0)), text)


def containerShell(command):
  return f"sudo -n docker exec -i {mariadbContainer} sh -c '{command}'"


mysqlCommand = containerShell('export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql --user=root --batch --skip-column-names '
  f'--default-character-set=utf8mb4 {databaseName}')


def binaryLogCommand(logFile, position):
  if not re.match(r"^[A-Za-z0-9_.-]+$", logFile) or type(position) is not int:
    raise LedgerError(f"baseline.yaml's binary log {logFile!r} at {position!r} is not a file name and a position")
  return containerShell('export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysqlbinlog --read-from-remote-server '
    f'--host=localhost --user=root --base64-output=decode-rows --verbose --start-position={position} --to-last-log {logFile}')


def asRepositoryOwner(repository, command):
  return f"sudo -n -u \"$(stat -c %U {repository})\" sh -c 'cd {repository} && {command}'"


class Server:
  """claudeperidot, reached over an SSH connection whose execute(command, standardInput, timeoutSeconds) returns
  (exitCode, stdoutBytes, stderrBytes)."""

  def __init__(self, connection):
    self.connection = connection

  def run(self, command, standardInput="", timeoutSeconds=120):
    exitCode, output, errors = self.connection.execute(command, standardInput.encode("utf-8"), timeoutSeconds)
    if exitCode != 0:
      raise LedgerError(f"{hostName}: {command[:100]} exited {exitCode}: {errors.decode('utf-8', 'replace').strip()[-2000:]}")
    return output.decode("utf-8")

  def sql(self, script, writes=False, timeoutSeconds=120):
    preamble = ["SET NAMES utf8mb4", "SET time_zone = '+00:00'", "SET SESSION group_concat_max_len = 4294967295"]
    if writes:
      preamble.append(f"SET SESSION gtid_domain_id = {ledgerGTIDDomain}")
    output = self.run(mysqlCommand, "".join(f"{statement};\n" for statement in preamble) + script, timeoutSeconds)
    return [line.split("\t") for line in output.splitlines()]


def runPowerShell(script):
  # Errors are written to the console directly: PowerShell serializes its own error stream as CLIXML when it is redirected.
  wrapped = ("$ErrorActionPreference = 'Stop'\n$ProgressPreference = 'SilentlyContinue'\ntry {\n" + script + "\n} catch {\n"
    "  [Console]::Error.WriteLine(($_ | Out-String).Trim())\n  exit 1\n}\n")
  encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
  result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
    encoded], capture_output=True, text=True)
  if result.returncode != 0:
    raise LedgerError(f"a PowerShell step failed (exit {result.returncode}): {(result.stderr or result.stdout).strip()[-2000:]}")
  return result.stdout


def expandClientPath(path):
  return str(Path.home()) + path[1:] if path.startswith("~\\") else path


def normalizedWindowsPath(path):
  for prefix in ("\\\\?\\", "\\??\\"):
    if path.startswith(prefix):
      path = path[len(prefix):]
  return os.path.normcase(os.path.normpath(path))


def iniValue(path, section, key):
  if not path.is_file():
    return None
  currentSection = None
  for line in path.read_bytes().decode("latin-1").splitlines():
    sectionMatch = re.match(r"^\[(.+)\]\s*$", line)
    if sectionMatch:
      currentSection = sectionMatch.group(1)
    elif currentSection is not None and currentSection.lower() == section.lower() and "=" in line:
      name, value = line.split("=", 1)
      if name.strip().lower() == key.lower():
        return value
  return None


def clientCheckPasses(check):
  path = Path(expandClientPath(check["path"]))
  if check.get("exists") == "file":
    return path.is_file() and not path.is_junction()
  if check.get("exists") == "directory":
    return path.is_dir() and not path.is_junction()
  if check.get("exists") == "junction":
    return path.is_junction() and normalizedWindowsPath(os.readlink(path)) == normalizedWindowsPath(expandClientPath(check["target"]))
  if "sha256" in check:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == check["sha256"]
  return iniValue(path, check["section"], check["key"]) == check["value"]


@dataclass(frozen=True)
class ChangeState:
  change: Change
  state: str
  done: int
  total: int
  note: str = ""

  @property
  def isApplied(self):
    return self.state == "applied"

  @property
  def label(self):
    text = {"applied": "applied", "notApplied": "not applied", "partlyApplied": f"partly applied ({self.done} of {self.total})"}
    return text[self.state] + (f"; {self.note}" if self.note else "")


def stateFromCount(change, done, total, note=""):
  if not 0 <= done <= total:
    raise LedgerError(f"{change.identifier}'s check reports {done} of {total}")
  state = "applied" if done == total else "notApplied" if done == 0 else "partlyApplied"
  return ChangeState(change, state, done, total, note)


@dataclass(frozen=True)
class PlanStep:
  line: int
  number: int
  ticked: bool
  text: str
  changes: tuple

  def describe(self):
    return f"{self.number}. {self.text}" + (f" [{', '.join(self.changes)}]" if self.changes else "")


def readPlanSteps(text):
  steps = []
  for index, line in enumerate(text.split("\n")):
    match = stepPattern.match(line)
    if match:
      changes = tuple(match.group(4).split(", ")) if match.group(4) else ()
      steps.append(PlanStep(index, int(match.group(2)), match.group(1) == "x", match.group(3), changes))
  return steps


def sectionSpan(lines, heading):
  try:
    start = lines.index(f"## {heading}")
  except ValueError as error:
    raise LedgerError(f"plan.md has no '## {heading}' section") from error
  end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
  return start, end


def planSection(text, heading):
  lines = text.split("\n")
  start, end = sectionSpan(lines, heading)
  return "\n".join(lines[start + 1:end]).strip()


def replaceSection(text, heading, body):
  lines = text.split("\n")
  start, end = sectionSpan(lines, heading)
  return "\n".join(lines[:start + 1] + ["", body, ""] + lines[end:])


def rewritePlan(text, appliedByNumber, affectedNumbers):
  """plan.md with each step that names an affected change ticked when all its changes are applied, and the In progress
  and Next sections naming the first two unticked steps."""
  lines = text.split("\n")
  for step in readPlanSteps(text):
    if affectedNumbers & set(step.changes):
      ticked = all(appliedByNumber[number] for number in step.changes)
      lines[step.line] = f"- [{'x' if ticked else ' '}] " + step.describe()
  text = "\n".join(lines)
  remaining = [step for step in readPlanSteps(text) if not step.ticked]
  text = replaceSection(text, "In progress", remaining[0].describe() if remaining else "None: every step is done.")
  return replaceSection(text, "Next", remaining[1].describe() if len(remaining) > 1 else "None.")


@dataclass
class BinaryLogTransaction:
  gtid: str
  domain: int
  time: str
  position: str
  writes: dict = field(default_factory=dict)
  statements: list = field(default_factory=list)


eventHeaderPattern = re.compile(r"^#(\d{6}\s+\d{1,2}:\d{2}:\d{2})\s+server id \d+\s+end_log_pos \d+\b(.*)$")
gtidPattern = re.compile(r"\sGTID (\d+)-(\d+)-(\d+)\b")
queryEventPattern = re.compile(r"\sQuery\s+thread_id=")
rowEventPattern = re.compile(r"^### (INSERT INTO|UPDATE|DELETE FROM) `([^`]+)`\.`([^`]+)`$")
ignoredQueryStatements = re.compile(r"^(SET\b|use\b|BEGIN\b|COMMIT\b|START TRANSACTION\b|ROLLBACK\b|XA\b|/\*!)", re.IGNORECASE)


def parseBinaryLog(text):
  """The transactions in mysqlbinlog --base64-output=decode-rows --verbose output, with their row writes per table and
  their statements (DDL and anything else written as a statement)."""
  transactions = []
  position = ""
  inQuery = False
  queryLines = []
  for line in text.splitlines():
    if line.startswith("# at "):
      position = line[5:].strip()
      inQuery = False
      queryLines = []
      continue
    header = eventHeaderPattern.match(line)
    if header:
      gtid = gtidPattern.search(header.group(2))
      if gtid:
        transactions.append(BinaryLogTransaction(f"{gtid.group(1)}-{gtid.group(2)}-{gtid.group(3)}", int(gtid.group(1)),
          header.group(1), position))
      inQuery = bool(queryEventPattern.search(header.group(2)))
      queryLines = []
      continue
    rowEvent = rowEventPattern.match(line)
    if rowEvent and transactions:
      key = (rowEvent.group(2), rowEvent.group(3), rowEvent.group(1).split()[0])
      transactions[-1].writes[key] = transactions[-1].writes.get(key, 0) + 1
      continue
    if inQuery and transactions and not line.startswith("#"):
      if line.endswith("/*!*/;"):
        statement = "\n".join(queryLines + [line[:-len("/*!*/;")]]).strip()
        queryLines = []
        if statement and not ignoredQueryStatements.match(statement):
          transactions[-1].statements.append(statement)
      else:
        queryLines.append(line)
  return transactions


def unaccountedTransactions(transactions):
  """The transactions no ledger change made that write tables drift watches, each with only those writes."""
  unaccounted = []
  for transaction in transactions:
    if transaction.domain == ledgerGTIDDomain:
      continue
    writes = {key: count for key, count in transaction.writes.items() if key[0] == databaseName and key[1] not in playTables}
    if writes or transaction.statements:
      unaccounted.append(BinaryLogTransaction(transaction.gtid, transaction.domain, transaction.time, transaction.position, writes,
        transaction.statements))
  return unaccounted


def describeTransaction(transaction):
  parts = [f"{operation} {table} x{count}" for (_, table, operation), count in sorted(transaction.writes.items())]
  parts += [" ".join(statement.split())[:200] for statement in transaction.statements]
  return f"{transaction.time} GTID {transaction.gtid} at {transaction.position}: " + "; ".join(parts)


def git(folder, *arguments):
  result = subprocess.run(["git", "-C", str(folder), *arguments], capture_output=True, text=True, encoding="utf-8")
  if result.returncode != 0:
    raise LedgerError(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
  return result.stdout


def pushBranch(folder):
  branch = git(folder, "rev-parse", "--abbrev-ref", "HEAD").strip()
  if branch in ("HEAD", "main", "master"):
    raise LedgerError(f"the ledger pushes only a working branch such as claude, and HEAD is {branch}")
  git(folder, "push", "origin", branch)


def writeJSON(path, value):
  path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def readJSON(path, missing):
  if not path.is_file():
    raise LedgerError(missing)
  return json.loads(path.read_text(encoding="utf-8"))


class Ledger:
  def __init__(self, folder, openServer, push):
    """openServer() returns the Server; push(folder) publishes the branch's commits."""
    self.folder = folder
    self.openServer = openServer
    self.push = push
    self.connectedServer = None

  @property
  def server(self):
    if self.connectedServer is None:
      self.connectedServer = self.openServer()
    return self.connectedServer

  @property
  def planPath(self):
    return self.folder / "plan.md"

  @property
  def baselinePath(self):
    return self.folder / "baseline.yaml"

  def find(self, identifier):
    matches = [change for change in loadChanges(self.folder) if identifier in (change.number, change.identifier)]
    if not matches:
      raise LedgerError(f"no change {identifier} in {self.folder / 'changes'}")
    return matches[0]

  def relative(self, path):
    return path.relative_to(self.folder).as_posix()

  def record(self, paths, message):
    """Commits paths (and only them) and pushes; nothing to commit is not an error, so a re-run converges."""
    names = [self.relative(path) for path in paths]
    git(self.folder, "add", "-A", "--", *names)
    if git(self.folder, "status", "--porcelain", "--", *names).strip():
      git(self.folder, "commit", "-m", message, "--", *names)
    self.push(self.folder)
    log.info("recorded and pushed: %s", message)

  def requireCommitted(self, change):
    pending = git(self.folder, "status", "--porcelain", "--", self.relative(change.folder), self.relative(self.planPath)).strip()
    if pending:
      raise LedgerError(f"{change.identifier} or plan.md has uncommitted edits; stage the change (and commit plan.md) first, so "
        f"what runs is what the ledger holds:\n{pending}")

  def applyStatements(self, change):
    statements = splitStatements(self.readFile(change, "apply.sql"))
    if not statements:
      raise LedgerError(f"{change.identifier}/apply.sql holds no statements")
    if change.form == "rows":
      outside = [statement for statement in statements if firstKeyword(statement) not in rowTransactionKeywords]
      if outside:
        raise LedgerError(f"{change.identifier} changes rows in one transaction, so apply.sql holds only INSERT, UPDATE, "
          f"DELETE and REPLACE; it also has: {outside[0][:120]}")
    elif len(statements) != change.steps:
      raise LedgerError(f"{change.identifier} has schemaSteps {change.steps} but apply.sql holds {len(statements)} statements")
    return statements

  def checkStatement(self, change):
    statements = splitStatements(self.readFile(change, "check.sql"))
    if len(statements) != 1 or firstKeyword(statements[0]) not in ("SELECT", "WITH"):
      raise LedgerError(f"{change.identifier}/check.sql must be one SELECT giving one row with a column named applied")
    return statements[0]

  def revertStatements(self, change):
    statements = splitStatements(self.readFile(change, "revert.sql"))
    if change.form == "schema" and len(statements) != change.steps:
      raise LedgerError(f"{change.identifier}/revert.sql must hold {change.steps} statements, the inverse of each apply step in "
        f"reverse order; it holds {len(statements)}")
    return statements

  def readFile(self, change, name):
    path = change.folder / name
    if not path.is_file():
      raise LedgerError(f"{change.identifier} has no {name}")
    return path.read_text(encoding="utf-8")

  def readSaved(self, change):
    return readJSON(change.folder / "saved.json", f"{change.identifier} is not staged: run stage {change.number} first")

  def readApplied(self, change):
    return readJSON(change.folder / "applied.json", f"{change.identifier} has no applied.json: it was not applied through the "
      "ledger, or apply stopped before its commit")

  def databaseStates(self, changes):
    if not changes:
      return {}
    script = "".join(f"SELECT '{change.number}', applied FROM ({self.checkStatement(change)}) AS ledgerCheck;\n"
      for change in changes)
    results = {}
    for row in self.server.sql(script):
      if len(row) != 2 or not numberPatterns["integer"].match(row[1]) or row[0] in results:
        raise LedgerError(f"a check gave {row!r}; each check.sql must give one row with an integer applied")
      results[row[0]] = int(row[1])
    states = {}
    for change in changes:
      if change.number not in results:
        raise LedgerError(f"{change.identifier}/check.sql gave no row")
      note = "" if (change.folder / "saved.json").is_file() else "not staged"
      states[change.number] = stateFromCount(change, results[change.number], change.steps, note)
    return states

  def repositoryHashes(self, change, commit=None):
    """Each scope file's SHA-256 in the host checkout, or at commit when given; None for a file that is absent."""
    repository = change.fields["repository"]
    files = " ".join(change.fields["scope"])
    if commit is None:
      loop = (f'for f in {files}; do if [ -f "$f" ]; then sha256sum -- "$f"; else echo "absent  $f"; fi; done')
    else:
      loop = (f'for f in {files}; do if git cat-file -e {commit}:"$f" 2>/dev/null; then '
        f'echo "$(git show {commit}:"$f" | sha256sum | cut -d" " -f1)  $f"; else echo "absent  $f"; fi; done')
    hashes = {}
    for line in self.server.run(asRepositoryOwner(repository, loop)).splitlines():
      digest, path = line.split("  ", 1)
      hashes[path] = None if digest == "absent" else digest
    if set(hashes) != set(change.fields["scope"]):
      raise LedgerError(f"{change.identifier}: the host reported files {sorted(hashes)}, not the scope")
    return hashes

  def repositoryState(self, change):
    savedPath = change.folder / "saved.json"
    if not savedPath.is_file():
      return ChangeState(change, "notApplied", 0, len(change.fields["scope"]), "not staged, so its files cannot be judged")
    saved = json.loads(savedPath.read_text(encoding="utf-8"))
    current = self.repositoryHashes(change)
    done = sum(current[path] == saved["after"][path] for path in current)
    if done == len(current):
      return ChangeState(change, "applied", done, done)
    if all(current[path] == saved["before"][path] for path in current):
      return ChangeState(change, "notApplied", 0, len(current))
    return ChangeState(change, "partlyApplied", done, len(current), "some files are neither as committed nor as staged")

  def clientState(self, change):
    checks = change.fields["checks"]
    return stateFromCount(change, sum(clientCheckPasses(check) for check in checks), len(checks))

  def states(self, changes):
    states = self.databaseStates([change for change in changes if change.kind == "db"])
    for change in changes:
      if change.kind in ("quest", "config"):
        states[change.number] = self.repositoryState(change)
      elif change.kind == "client":
        states[change.number] = self.clientState(change)
    return {change.number: states[change.number] for change in changes}

  def state(self, change):
    return self.states([change])[change.number]

  def fetchScope(self, change):
    """The scope's columns and rows, each value encoded as N (NULL) or V and the hex of its bytes or text form."""
    tables = change.tables
    names = ", ".join(f"'{table}'" for table in sorted(set(tables)))
    columns = {table: [] for table in tables}
    for row in self.server.sql("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IFNULL(CHARACTER_SET_NAME, ''), EXTRA FROM "
        f"information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({names}) "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION;\n"):
      if len(row) != 5:
        raise LedgerError(f"information_schema gave the column row {row!r}")
      if "GENERATED" not in row[4].upper():
        columns[row[0]].append(Column(row[1], row[2].lower(), row[3]))
    missing = [table for table in tables if not columns[table]]
    if missing:
      raise LedgerError(f"{change.identifier}: {', '.join(missing)} is not a table in {databaseName}")
    script = "".join(f"SELECT '{index}', {', '.join(fetchExpression(column) for column in columns[entry['table']])} FROM "
      f"{quoteIdentifier(entry['table'])} WHERE ({entry['where']});\n" for index, entry in enumerate(change.fields["scope"]))
    rows = {index: [] for index in range(len(tables))}
    for row in self.server.sql(script):
      index = int(row[0])
      values = row[1:]
      if len(values) != len(columns[tables[index]]) or not all(encodedValuePattern.match(value) for value in values):
        raise LedgerError(f"{change.identifier}: a row of {tables[index]} came back as {row!r}")
      rows[index].append(values)
    return [{"table": entry["table"], "where": entry["where"], "columns": [column.asRecord() for column in
      columns[entry["table"]]], "rows": sorted(rows[index], key=rowText)} for index, entry in enumerate(change.fields["scope"])]

  def tableChecksums(self, tables):
    if not tables:
      return {}
    checksums = {}
    for row in self.server.sql(f"CHECKSUM TABLE {', '.join(quoteIdentifier(table) for table in tables)} EXTENDED;\n",
        timeoutSeconds=1800):
      if len(row) != 2 or not numberPatterns["integer"].match(row[1]):
        raise LedgerError(f"CHECKSUM TABLE gave {row!r}")
      checksums[row[0].split(".", 1)[1]] = int(row[1])
    if set(checksums) != set(tables):
      raise LedgerError(f"CHECKSUM TABLE answered for {sorted(checksums)}, not {sorted(tables)}")
    return checksums

  def schemaFingerprint(self, change, createFolder=None):
    """Each scope table's definition (without its AUTO_INCREMENT counter) and checksum, or absent."""
    tables = change.tables
    names = ", ".join(f"'{table}'" for table in sorted(set(tables)))
    existing = {row[0] for row in self.server.sql("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = "
      f"DATABASE() AND TABLE_NAME IN ({names});\n")}
    present = [table for table in tables if table in existing]
    rows = self.server.sql("".join(f"SHOW CREATE TABLE {quoteIdentifier(table)};\n" for table in present)) if present else []
    if len(rows) != len(present):
      raise LedgerError(f"SHOW CREATE TABLE gave {len(rows)} rows for {len(present)} tables")
    checksums = self.tableChecksums(present)
    fingerprint = {}
    for table, row in zip(present, rows):
      create = re.sub(r" AUTO_INCREMENT=\d+", "", unescapeBatchField(row[1]))
      fingerprint[table] = {"create": hashlib.sha256(create.encode("utf-8")).hexdigest(), "checksum": checksums[table]}
      if createFolder is not None:
        createFolder.mkdir(exist_ok=True)
        (createFolder / f"{table}.sql").write_text(create + ";\n", encoding="utf-8")
    for table in tables:
      fingerprint.setdefault(table, "absent")
    return fingerprint

  def status(self):
    changes = loadChanges(self.folder)
    states = self.states(changes)
    planText = self.planPath.read_text(encoding="utf-8")
    steps = readPlanSteps(planText)
    lines = [f"In progress (plan.md): {planSection(planText, 'In progress')}", f"Next (plan.md): {planSection(planText, 'Next')}",
      ""]
    unticked = [step for step in steps if not step.ticked]
    derived = unticked[0].describe() if unticked else "None: every step is done."
    if planSection(planText, "In progress") != derived:
      lines.append(f"plan.md: the In progress section does not name the first unticked step, {derived}")
    for step in steps:
      unknown = [number for number in step.changes if number not in states]
      if unknown:
        lines.append(f"plan.md: step {step.number} names {', '.join(unknown)}, which is not in the ledger")
        continue
      if not step.changes:
        continue
      allApplied = all(states[number].isApplied for number in step.changes)
      if allApplied and not step.ticked:
        lines.append(f"plan.md: step {step.number}'s changes are applied but it is not ticked: an apply stopped before its "
          "commit; check the change and record it")
      elif step.ticked and not allApplied:
        lines.append(f"plan.md: step {step.number} is ticked but not all of its changes are applied")
    width = max((len(change.identifier) for change in changes), default=0)
    lines += [f"{change.identifier:<{width}}  {change.kind:<6}  {change.fields['disposition']:<11}  {states[change.number].label}"
      for change in changes]
    fixtures = [change.number for change in changes if change.fields["disposition"] == "fixture"]
    explorations = [change.number for change in changes if change.fields["disposition"] == "exploration"
      and states[change.number].state != "notApplied"]
    uncleared = [change.number for change in changes if change.fields["disposition"] == "production"
      and change.fields["ownerClearance"] != "cleared"]
    lines += ["", "Closing a session:", f"  fixtures (kept): {', '.join(fixtures) or 'none'}",
      f"  exploration changes still applied: {', '.join(explorations) or 'none'}",
      f"  production changes the owner has not cleared: {', '.join(uncleared) or 'none'}",
      "  drift: run drift; it must be clean"]
    return lines

  def stage(self, identifier):
    change = self.find(identifier)
    if change.kind == "db":
      self.applyStatements(change)
      self.checkStatement(change)
      if change.form == "schema":
        self.revertStatements(change)
      state = self.state(change)
      if state.state != "notApplied":
        raise LedgerError(f"{change.identifier} is {state.label}; stage saves the rows of a change that has not run")
      if change.form == "rows":
        tables = self.fetchScope(change)
        writeJSON(change.folder / "saved.json", {"checksum": scopeChecksum(tables), "tables": tables})
        (change.folder / "revert.sql").write_text(revertScript(tables), encoding="utf-8")
      else:
        fingerprint = self.schemaFingerprint(change, change.folder / "showCreate")
        writeJSON(change.folder / "saved.json", {"checksum": hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode(
          "utf-8")).hexdigest(), "tables": fingerprint})
    elif change.kind in ("quest", "config"):
      before = self.repositoryHashes(change)
      after = self.repositoryHashes(change, change.fields["commits"][-1])
      unchanged = [path for path in before if before[path] == after[path]]
      if unchanged:
        raise LedgerError(f"{change.identifier}: the commits leave {', '.join(unchanged)} as it is on the host now; either the "
          "change is applied already or those files are not its scope")
      writeJSON(change.folder / "saved.json", {"before": before, "after": after})
    self.record([change.folder], f"Stage {change.identifier}: {change.fields['title']}")
    return [f"staged {change.identifier}: committed and pushed before it runs"]

  def apply(self, identifier):
    change = self.find(identifier)
    self.requireCommitted(change)
    self.push(self.folder)
    log.info("applying %s", change.identifier)
    if change.form == "rows":
      self.applyRows(change)
    elif change.form == "schema":
      self.applySchema(change)
    elif change.kind in ("quest", "config"):
      self.requireState(change, "notApplied", "apply")
      self.server.run(asRepositoryOwner(change.fields["repository"], f"git merge --ff-only {change.fields['commits'][-1]}"))
      self.requireState(change, "applied", "after its merge, apply")
    else:
      self.requireState(change, "notApplied", "apply")
      for step in change.fields["steps"]:
        runPowerShell(step)
      self.requireState(change, "applied", "after its steps, apply")
    self.updatePlan(change)
    self.record([change.folder, self.planPath], f"Apply {change.identifier}: {change.fields['title']}")
    return [f"applied {change.identifier}", f"activation: {change.fields['activation']}"]

  def requireState(self, change, expected, action):
    state = self.state(change)
    if state.state != expected:
      raise LedgerError(f"{action} needs {change.identifier} {'applied' if expected == 'applied' else 'not applied'}; "
        f"it is {state.label}")
    return state

  def applyRows(self, change):
    saved = self.readSaved(change)
    self.requireState(change, "notApplied", "apply")
    if scopeChecksum(self.fetchScope(change)) != saved["checksum"]:
      raise LedgerError(f"the rows of {change.identifier} changed since stage, so revert.sql would put back stale rows: "
        f"stage it again")
    script = ("START TRANSACTION;\n"
      + rowsGuards(saved["tables"], [tableDigest(table["rows"]) for table in saved["tables"]], f"ledger: {change.number} rows "
        "changed since stage")
      + "".join(f"{statement};\n" for statement in self.applyStatements(change))
      + guardBlock(f"({checkQuery(self.checkStatement(change))}) = 1", f"ledger: {change.number} check is not 1 after apply")
      + "COMMIT;\n")
    self.server.sql(script, writes=True, timeoutSeconds=600)
    after = self.fetchScope(change)
    writeJSON(change.folder / "applied.json", {"checksum": scopeChecksum(after), "digests": [tableDigest(table["rows"])
      for table in after], "tableChecksums": self.tableChecksums(sorted(set(change.tables)))})

  def applySchema(self, change):
    statements = self.applyStatements(change)
    state = self.state(change)
    if state.isApplied:
      raise LedgerError(f"{change.identifier} is applied already")
    if state.done == 0:
      saved = self.readSaved(change)
      if self.schemaFingerprint(change) != saved["tables"]:
        raise LedgerError(f"the tables of {change.identifier} changed since stage: stage it again")
    failure = None
    try:
      self.server.sql("".join(f"{statement};\n" for statement in statements[state.done:]), writes=True, timeoutSeconds=3600)
    except LedgerError as error:
      failure = error
    after = self.state(change)
    if failure is not None or not after.isApplied:
      raise LedgerError(f"{change.identifier}: {after.done} of {after.total} steps are done; fix the cause and run apply again "
        "to resume from the next step" + (f" ({failure})" if failure else "")) from failure
    fingerprint = self.schemaFingerprint(change)
    writeJSON(change.folder / "applied.json", {"checksum": hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode(
      "utf-8")).hexdigest(), "tables": fingerprint, "tableChecksums": {table: value["checksum"] for table, value in
      fingerprint.items() if value != "absent"}})

  def revert(self, identifier):
    change = self.find(identifier)
    self.requireCommitted(change)
    self.push(self.folder)
    log.info("reverting %s", change.identifier)
    if change.form == "rows":
      self.revertRows(change)
    elif change.form == "schema":
      self.revertSchema(change)
    elif change.kind in ("quest", "config"):
      self.requireState(change, "applied", "revert")
      commits = " ".join(reversed(change.fields["commits"]))
      self.server.run(asRepositoryOwner(change.fields["repository"],
        f"git -c user.name=Claude -c user.email=claude@{hostName} revert --no-edit {commits}"))
      self.requireState(change, "notApplied", "after git revert, revert")
    else:
      state = self.state(change)
      if not state.isApplied:
        raise LedgerError(f"revert needs every check of {change.identifier} passing, as apply left it; it is {state.label}, so "
          "what it touched changed since apply: put that right by hand")
      for step in change.fields["undo"]:
        runPowerShell(step)
      self.requireState(change, "notApplied", "after its undo, revert")
    (change.folder / "applied.json").unlink(missing_ok=True)
    self.updatePlan(change)
    self.record([change.folder, self.planPath], f"Revert {change.identifier}: {change.fields['title']}")
    return [f"reverted {change.identifier}", f"activation: {change.fields['activation']}"]

  def revertRows(self, change):
    saved = self.readSaved(change)
    self.requireState(change, "applied", "revert")
    applied = self.readApplied(change)
    current = self.fetchScope(change)
    if scopeChecksum(current) != applied["checksum"]:
      changed = [table["table"] for table, digest in zip(current, applied["digests"]) if tableDigest(table["rows"]) != digest]
      raise LedgerError(f"the rows of {change.identifier} changed since apply ({', '.join(changed) or 'columns'}); revert "
        "refuses to overwrite them")
    message = f"ledger: {change.number} "
    script = ("START TRANSACTION;\n"
      + rowsGuards(saved["tables"], applied["digests"], message + "rows changed since apply")
      + "".join(f"{statement};\n" for statement in self.revertStatements(change))
      + rowsGuards(saved["tables"], [tableDigest(table["rows"]) for table in saved["tables"]], message + "revert did not "
        "restore the saved rows")
      + guardBlock(f"({checkQuery(self.checkStatement(change))}) = 0", message + "check is not 0 after revert")
      + "COMMIT;\n")
    self.server.sql(script, writes=True, timeoutSeconds=600)
    if scopeChecksum(self.fetchScope(change)) != saved["checksum"]:
      raise LedgerError(f"{change.identifier}: revert committed, but the rows differ from those saved at stage")

  def revertSchema(self, change):
    saved = self.readSaved(change)
    state = self.state(change)
    if state.state == "notApplied":
      raise LedgerError(f"{change.identifier} is not applied")
    if state.isApplied and self.schemaFingerprint(change) != self.readApplied(change)["tables"]:
      raise LedgerError(f"the tables of {change.identifier} changed since apply; revert refuses to overwrite them")
    statements = self.revertStatements(change)
    failure = None
    try:
      self.server.sql("".join(f"{statement};\n" for statement in statements[change.steps - state.done:]), writes=True,
        timeoutSeconds=3600)
    except LedgerError as error:
      failure = error
    after = self.state(change)
    if failure is not None or after.state != "notApplied":
      raise LedgerError(f"{change.identifier}: {after.done} of {after.total} steps are still done; fix the cause and run "
        "revert again to undo the rest" + (f" ({failure})" if failure else "")) from failure
    if self.schemaFingerprint(change) != saved["tables"]:
      raise LedgerError(f"{change.identifier}: after revert.sql the tables are not as saved at stage")

  def updatePlan(self, change):
    text = self.planPath.read_text(encoding="utf-8")
    named = sorted({number for step in readPlanSteps(text) if change.number in step.changes for number in step.changes})
    changes = {candidate.number: candidate for candidate in loadChanges(self.folder)}
    unknown = [number for number in named if number not in changes]
    if unknown:
      raise LedgerError(f"plan.md names {', '.join(unknown)}, which is not in the ledger")
    states = self.states([changes[number] for number in named]) if named else {}
    applied = {number: state.isApplied for number, state in states.items()}
    self.planPath.write_text(rewritePlan(text, applied, {change.number}), encoding="utf-8")

  def readBaseline(self):
    if not self.baselinePath.is_file():
      raise LedgerError("there is no baseline.yaml yet: seed claudeperidot first (plan step 7)")
    baseline = readYAML(self.baselinePath)
    missing = [name for name in ("dump", "dbVersion", "binaryLog", "deployed", "tableChecksums") if name not in baseline]
    if missing:
      raise LedgerError(f"baseline.yaml lacks {', '.join(missing)}")
    return baseline

  def baseTables(self):
    return [row[0] for row in self.server.sql("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
      "AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME;\n")]

  def drift(self):
    baseline = self.readBaseline()
    changes = [change for change in loadChanges(self.folder) if change.kind == "db"]
    states = self.databaseStates(changes)
    lines = []
    expected = {table: (value, "the baseline") for table, value in baseline["tableChecksums"].items() if table not in playTables}
    for change in changes:
      if states[change.number].state == "notApplied":
        continue
      if not (change.folder / "applied.json").is_file():
        lines.append(f"{change.identifier} is {states[change.number].label} but has no applied.json: its apply stopped before "
          "the commit, or it ran outside the ledger")
        continue
      for table, value in self.readApplied(change)["tableChecksums"].items():
        expected[table] = (value, change.identifier)
    current = self.tableChecksums([table for table in self.baseTables() if table not in playTables])
    for table in sorted(set(expected) | set(current)):
      if table not in current:
        lines.append(f"{table}: in {expected[table][1]} but gone now")
      elif table not in expected:
        lines.append(f"{table}: new since the baseline, and no change accounts for it")
      elif expected[table][0] != current[table]:
        lines.append(f"{table}: checksum {current[table]}, expected {expected[table][0]} from {expected[table][1]}")
    binaryLog = baseline["binaryLog"]
    transactions = parseBinaryLog(self.server.run(binaryLogCommand(binaryLog["file"], binaryLog["position"]),
      timeoutSeconds=600))
    lines += [f"unaccounted write: {describeTransaction(transaction)}" for transaction in unaccountedTransactions(transactions)]
    return lines or ["drift: clean"]

  def promote(self, identifier, manifestVersion=None):
    change = self.find(identifier)
    if change.fields["disposition"] != "production":
      raise LedgerError(f"{change.identifier} is {change.fields['disposition']}; only a production change is promoted")
    pending = [candidate.identifier for candidate in loadChanges(self.folder) if candidate.fields["disposition"] == "production"
      and candidate.fields["significance"] == "significant" and candidate.fields["ownerClearance"] == "pending"]
    if pending:
      raise LedgerError(f"promote waits for the owner to clear the significant changes {', '.join(pending)}")
    self.requireState(change, "applied", "promote")
    production = change.fields["production"]
    if production == "contentSQL":
      saved = self.readSaved(change)
      check = self.checkStatement(change)
      header = (f"-- {change.identifier}: {change.fields['title']}\n-- Written by ledger.py promote. Fails and rolls back unless "
        "Peridot still holds the rows the change was made against, and unless its check gives 1 after it.\n")
      outputs = {"content.sql": header + "SET NAMES utf8mb4;\nSET time_zone = '+00:00';\n"
        "SET SESSION group_concat_max_len = 4294967295;\nSTART TRANSACTION;\n"
        + rowsGuards(saved["tables"], [tableDigest(table["rows"]) for table in saved["tables"]], f"{change.number}: Peridot rows "
          "differ from those the change was made against")
        + "".join(f"{statement};\n" for statement in self.applyStatements(change))
        + guardBlock(f"({checkQuery(check)}) = 1", f"{change.number}: check is not 1 after the content SQL")
        + "COMMIT;\n", "check.sql": checkQuery(check) + ";\n"}
    elif production == "customMigration":
      outputs = {"customMigration.cpp": self.manifestEntry(change, manifestVersion)}
    elif production == "questCommit":
      outputs = {"cherryPick.sh": f"#!/bin/sh\nset -e\ncd {peridotQuestsPath}\ngit cherry-pick {' '.join(change.fields['commits'])}\n"}
    else:
      outputs = {"edit.md": f"# {change.identifier}: carry into the {production}\n\n{change.fields['title']}\n\n"
        f"{change.fields['why']}\n\n- Files: {', '.join(change.fields['scope'])}\n- Commits: {', '.join(change.fields['commits'])}"
        f"\n- Activation: {change.fields['activation']}\n"}
    outputFolder = change.folder / "peridot"
    outputFolder.mkdir(exist_ok=True)
    for name, text in outputs.items():
      (outputFolder / name).write_text(text, encoding="utf-8", newline="\n")
    self.record([change.folder], f"Promote {change.identifier}: {change.fields['title']}")
    return [f"promoted {change.identifier}: {self.relative(outputFolder)}"]

  def manifestEntry(self, change, manifestVersion):
    if type(manifestVersion) is not int or manifestVersion < 1:
      raise LedgerError("a custom migration needs --manifestVersion, the next version in Peridot's "
        "database_update_manifest_custom.cpp")
    statements = self.applyStatements(change)
    if any(')"' in statement or not statement.isascii() for statement in statements):
      raise LedgerError(f"{change.identifier}: a statement holds )\" (which would end a C++ raw string) or non-ASCII text")
    check = " ".join(f"{checkQuery(self.checkStatement(change))} WHERE applied = {change.steps}".split())
    check = check.replace("\\", "\\\\").replace('"', '\\"')
    pieces = "\n\t\t\t".join(f'R"({statement};\n)"' for statement in statements)
    contentUpdate = "true" if all(table in contentTables for table in change.tables) else "false"
    return ("\tManifestEntry{\n"
      f"\t\t.version = {manifestVersion},\n"
      f"\t\t.description = \"{datetime.date.today():%Y_%m_%d}_{change.name}\",\n"
      f"\t\t.check = \"{check}\",\n"
      "\t\t.condition = \"empty\",\n"
      "\t\t.match = \"\",\n"
      f"\t\t.sql =\n\t\t\t{pieces},\n"
      f"\t\t.content_schema_update = {contentUpdate},\n"
      "\t},\n")

  def seedCopy(self, copyScript):
    if self.baselinePath.is_file():
      raise LedgerError("baseline.yaml exists: claudeperidot is seeded; to seed again, remove it in a commit first")
    script = Path(copyScript)
    if not script.is_file():
      raise LedgerError(f"{script} is not a file")
    commands = {".ps1": ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
      ".py": [sys.executable, str(script)]}
    if script.suffix.lower() not in commands:
      raise LedgerError(f"the copy script must be .ps1 or .py, not {script.name}")
    log.info("seeding with %s", script)
    result = subprocess.run(commands[script.suffix.lower()])
    if result.returncode != 0:
      raise LedgerError(f"the copy script {script.name} failed with exit code {result.returncode}")
    return ["copied: boot world once (its migrations and rule injection), then run seed --baseline"]

  def seedBaseline(self):
    if self.baselinePath.is_file():
      raise LedgerError("baseline.yaml exists already")
    dumps = [name for name in self.server.run(f"ls -1t {hostPaths['dumps']}").split() if re.match(r"^peq-[\w.-]+\.sql\.gz$", name)]
    if not dumps:
      raise LedgerError(f"no peq-*.sql.gz in {hostPaths['dumps']}: run seed with the copy script first")
    dumpPath = f"{hostPaths['dumps']}/{dumps[0]}"
    dumpTime = datetime.datetime.fromtimestamp(int(self.server.run(f"stat -c %Y {dumpPath}").strip()), datetime.UTC)
    versionRows = self.server.sql("SELECT version, bots_version, custom_version FROM db_version;\n")
    masterRows = self.server.sql("SHOW MASTER STATUS;\n")
    if len(versionRows) != 1 or len(masterRows) != 1:
      raise LedgerError(f"db_version gave {versionRows!r} and SHOW MASTER STATUS gave {masterRows!r}; the baseline needs one "
        "db_version row and the binary log on")
    binaryLog = {"file": masterRows[0][0], "position": int(masterRows[0][1])}
    deployed = {name: self.server.run(f"sudo -n git -c safe.directory='*' -C {hostPaths[name]} rev-parse HEAD").strip()
      for name in ("akkStack", "code", "quests", "maps")}
    baseline = {
      "dump": {"path": dumpPath, "sha256": self.server.run(f"sha256sum {dumpPath}").split()[0],
        "time": dumpTime.strftime("%Y-%m-%dT%H:%M:%SZ")},
      "dbVersion": {"version": int(versionRows[0][0]), "botsVersion": int(versionRows[0][1]),
        "customVersion": int(versionRows[0][2])},
      "binaryLog": binaryLog,
      "deployed": deployed,
      "tableChecksums": self.tableChecksums([table for table in self.baseTables() if table not in playTables]),
    }
    self.baselinePath.write_text(yaml.safe_dump(baseline, sort_keys=False), encoding="utf-8")
    self.record([self.baselinePath], f"Baseline: dump {baseline['dump']['sha256'][:12]}, db_version {versionRows[0][0]}, "
      f"binary log {binaryLog['file']}:{binaryLog['position']}")
    return [f"baseline written: {len(baseline['tableChecksums'])} table checksums; now take the Proxmox baseline snapshot"]


def openServer():
  from facetClient.inventory import InventoryError, loadInventory
  from facetClient.sshConnection import SSHConnection
  inventoryPath = os.environ.get("facetInventory")
  if not inventoryPath:
    raise LedgerError("set facetInventory to facet's inventory (operations hosts\\inventory.yaml), as the facet MCP server has it")
  try:
    hosts = loadInventory(inventoryPath)
  except (InventoryError, OSError) as error:
    raise LedgerError(f"facet's inventory {inventoryPath} cannot be used: {error}") from error
  if hostName not in hosts:
    raise LedgerError(f"{hostName} is not in {inventoryPath}: enroll it in facet first (plan step 6)")
  return Server(SSHConnection(hosts[hostName]))


def configureLogging():
  localAppData = os.environ.get("LOCALAPPDATA")
  if not localAppData:
    raise LedgerError("LOCALAPPDATA is not set, so the ledger has nowhere to log")
  logFolder = Path(localAppData) / "claudeperidot" / "logs"
  logFolder.mkdir(parents=True, exist_ok=True)
  handler = logging.handlers.TimedRotatingFileHandler(logFolder / "ledger.log", when="midnight", backupCount=90,
    encoding="utf-8")
  handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
  logging.getLogger().addHandler(handler)
  logging.getLogger().setLevel(logging.INFO)
  logging.getLogger("paramiko").setLevel(logging.WARNING)


def main(arguments=None):
  parser = argparse.ArgumentParser(description="The claudeperidot change ledger.")
  commands = parser.add_subparsers(dest="command", required=True)
  commands.add_parser("status", help="every change's state, read live, beside plan.md's step in progress")
  for name, text in (("stage", "save the rows or files a change touches and commit and push it before it runs"),
      ("apply", "run a staged change, check it, rewrite plan.md, commit and push"),
      ("revert", "undo an applied change unless what it touched changed since apply")):
    commands.add_parser(name, help=text).add_argument("change", help="the change's number or folder name")
  commands.add_parser("drift", help="table checksums against the baseline plus applied changes, and unaccounted binary-log "
    "writes")
  promote = commands.add_parser("promote", help="write a production change's Peridot form")
  promote.add_argument("change")
  promote.add_argument("--manifestVersion", type=int, help="the custom migration version, for a schema change")
  seed = commands.add_parser("seed", help="stream Peridot's database into claudeperidot, then write baseline.yaml")
  seedMode = seed.add_mutually_exclusive_group(required=True)
  seedMode.add_argument("--copyScript", help="the operations repository's database copy script (.ps1 or .py)")
  seedMode.add_argument("--baseline", action="store_true", help="after world has booted once, write baseline.yaml")
  options = parser.parse_args(arguments)
  ledger = Ledger(ledgerFolder, openServer, pushBranch)
  try:
    configureLogging()
    if options.command == "status":
      lines = ledger.status()
    elif options.command in ("stage", "apply", "revert"):
      lines = getattr(ledger, options.command)(options.change)
    elif options.command == "drift":
      lines = ledger.drift()
    elif options.command == "promote":
      lines = ledger.promote(options.change, options.manifestVersion)
    else:
      lines = ledger.seedBaseline() if options.baseline else ledger.seedCopy(options.copyScript)
  except LedgerError as error:
    log.error("%s failed: %s", options.command, error)
    print(f"ledger: {error}", file=sys.stderr)
    return 1
  print("\n".join(lines))
  return 0


if __name__ == "__main__":
  sys.exit(main())
