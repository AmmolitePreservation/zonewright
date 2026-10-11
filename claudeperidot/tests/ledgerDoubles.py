"""Stand-ins for what the ledger reaches over SSH (claudeperidot's MariaDB and shell) and for pushing, plus helpers that
build a ledger in a temporary git repository."""
import copy
import hashlib
import re
import subprocess
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import ledger


class FakeSQLError(Exception):
  pass


def encodeValue(value):
  return "N" if value is None else "V" + value.hex().upper()


@dataclass
class FakeTable:
  columns: list
  primaryKey: tuple
  rows: list = field(default_factory=list)

  def column(self, name):
    return next(column for column in self.columns if column[0] == name)

  def insert(self, row):
    key = tuple(row[name] for name in self.primaryKey)
    if any(tuple(existing[name] for name in self.primaryKey) == key for existing in self.rows):
      raise FakeSQLError(f"ERROR 1062 (23000): Duplicate entry {key!r} for key 'PRIMARY'")
    self.rows.append({name: row.get(name) for name, _, _ in self.columns})


def scriptStatements(script):
  """The statements the mysql client would send for script, honouring DELIMITER lines."""
  delimiter = ";"
  statements = []
  current = []
  for line in script.split("\n"):
    stripped = line.strip()
    if not current and stripped.upper().startswith("DELIMITER "):
      delimiter = stripped.split()[1]
      continue
    if not current and (not stripped or stripped.startswith("-- ")):
      continue
    current.append(line)
    if stripped.endswith(delimiter):
      text = "\n".join(current).strip()
      statements.append(text[:-len(delimiter)].strip())
      current = []
  if "".join(current).strip():
    raise AssertionError(f"an unterminated statement: {current!r}")
  return statements


literalPattern = re.compile(r"\s*(?:(NULL)|'((?:[^'\\]|'')*)'|_(\w+) X'([0-9A-F]*)'|X'([0-9A-F]*)'|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?))\s*")


def parseValueRows(text):
  """The rows of an INSERT's VALUES list as (kind, bytes or None, charset) tokens."""
  rows = []
  position = 0
  while True:
    opening = re.compile(r"\s*\(").match(text, position)
    assert opening, f"expected ( at {text[position:position + 40]!r}"
    position = opening.end()
    values = []
    while True:
      match = literalPattern.match(text, position)
      assert match, f"not a literal the fake knows: {text[position:position + 60]!r}"
      if match.group(1):
        values.append(("null", None, None))
      elif match.group(2) is not None:
        values.append(("quoted", match.group(2).replace("''", "'").encode("ascii"), None))
      elif match.group(3):
        values.append(("introduced", bytes.fromhex(match.group(4)), match.group(3)))
      elif match.group(5) is not None:
        values.append(("hex", bytes.fromhex(match.group(5)), None))
      else:
        values.append(("number", match.group(6).encode("ascii"), None))
      position = match.end()
      if text.startswith(",", position):
        position += 1
        continue
      assert text.startswith(")", position), f"expected , or ) at {text[position:position + 40]!r}"
      position += 1
      break
    rows.append(values)
    rest = text[position:].strip()
    if not rest:
      return rows
    assert rest.startswith(","), f"expected , between rows at {rest[:40]!r}"
    position = text.index(",", position) + 1


def storedValue(column, token):
  """What MariaDB stores for token in column: the literal kinds the ledger may write for each column type."""
  name, dataType, charset = column
  kind, data, introducer = token
  if kind == "null":
    return None
  if dataType in ledger.binaryTypes:
    assert kind == "hex", f"{name} ({dataType}) was given a {kind} literal"
  elif dataType in ledger.textTypes:
    assert kind in ("quoted", "introduced"), f"{name} ({dataType}) was given a {kind} literal"
    assert kind == "quoted" or introducer == charset, f"{name} ({charset}) was given a _{introducer} literal"
  elif dataType in ledger.temporalTypes:
    assert kind == "quoted", f"{name} ({dataType}) was given a {kind} literal"
  else:
    assert kind == "number", f"{name} ({dataType}) was given a {kind} literal"
  return data


class FakeMariaDB:
  """Stands in for claudeperidot behind the SSH connection. Its MariaDB answers the ledger's queries from in-memory tables
  whose values are the bytes the server would show (text in the column's character set, numbers and times as text,
  binary as is). Each test gives predicates, apply statements and checks as Python; the statements the ledger writes
  itself (revert's DELETE and INSERT, the guards) are parsed and run here."""

  def __init__(self):
    self.tables = {}
    self.predicates = {}
    self.mutations = {}
    self.checks = {}
    self.createStatements = {}
    self.masterStatus = [["binlog.000002", "4321"]]
    self.dbVersion = ["9325", "0", "0"]
    self.binaryLog = ""
    self.shell = []
    self.calls = []
    self.writes = []
    self.observers = []
    self.domain = 0

  def execute(self, command, standardInput=b"", timeoutSeconds=60):
    script = standardInput.decode("utf-8")
    self.calls.append((command, script))
    for observer in self.observers:
      observer(command, script)
    if command == ledger.mysqlCommand:
      try:
        return 0, self.runScript(script).encode("utf-8"), b""
      except FakeSQLError as error:
        return 1, b"", str(error).encode("utf-8")
    if "mysqlbinlog" in command:
      return 0, self.binaryLog.encode("utf-8"), b""
    for pattern, respond in self.shell:
      if re.search(pattern, command):
        output = respond(command)
        return (1, b"", output[1].encode("utf-8")) if isinstance(output, tuple) else (0, output.encode("utf-8"), b"")
    raise AssertionError(f"the fake server does not know the command {command!r}")

  @property
  def sqlScripts(self):
    return [script for command, script in self.calls if command == ledger.mysqlCommand]

  @property
  def writeScripts(self):
    return [script for script in self.sqlScripts if "gtid_domain_id" in script]

  def matching(self, tableName, where):
    return [row for row in self.tables[tableName].rows if self.predicates[where](row)]

  def runScript(self, script):
    output = []
    snapshot = None
    self.domain = 0
    try:
      for statement in scriptStatements(script):
        upper = statement.upper()
        if upper.startswith("SET "):
          domain = re.match(r"^SET SESSION gtid_domain_id = (\d+)$", statement)
          if domain:
            self.domain = int(domain.group(1))
        elif upper == "START TRANSACTION":
          snapshot = copy.deepcopy(self.tables)
        elif upper == "COMMIT":
          snapshot = None
        elif upper.startswith("BEGIN NOT ATOMIC"):
          self.guard(statement)
        elif upper.startswith(("SELECT", "CHECKSUM", "SHOW")):
          output.extend(self.select(statement))
        else:
          self.write(statement)
    except FakeSQLError:
      if snapshot is not None:
        self.tables = snapshot
      raise
    if snapshot is not None:
      self.tables = snapshot
    return "".join(line + "\n" for line in output)

  def write(self, statement):
    self.writes.append((self.domain, statement))
    if statement in self.mutations:
      self.mutations[statement](self)
      return
    delete = re.match(r"^DELETE FROM `(\w+)` WHERE \((.*)\)$", statement, re.DOTALL)
    if delete:
      table = self.tables[delete.group(1)]
      predicate = self.predicates[delete.group(2)]
      table.rows = [row for row in table.rows if not predicate(row)]
      return
    insert = re.match(r"^INSERT INTO `(\w+)` \((.*?)\) VALUES\n(.*)$", statement, re.DOTALL)
    if insert:
      table = self.tables[insert.group(1)]
      names = [name.strip().strip("`") for name in insert.group(2).split(",")]
      for tokens in parseValueRows(insert.group(3)):
        assert len(tokens) == len(names)
        table.insert({name: storedValue(table.column(name), token) for name, token in zip(names, tokens)})
      return
    raise AssertionError(f"the fake does not know the statement {statement!r}")

  def guard(self, statement):
    condition = re.search(r"IF \((.*)\) IS NOT TRUE THEN", statement, re.DOTALL).group(1)
    message = re.search(r"MESSAGE_TEXT = '([^']*)'", statement).group(1)
    if not self.evaluate(condition):
      raise FakeSQLError(f"ERROR 1644 (45000) at line 1: {message}")

  def evaluate(self, condition):
    digest = re.match(r"^\(SELECT SHA2\(GROUP_CONCAT\(ledgerRow ORDER BY BINARY ledgerRow SEPARATOR '\|'\), 256\) FROM "
      r"\(SELECT CONCAT_WS\(',', (.*)\) AS ledgerRow FROM `(\w+)` WHERE \((.*)\)\) AS ledgerRows\) <=> (NULL|'[0-9a-f]{64}')$",
      condition, re.DOTALL)
    if digest:
      columns = re.findall(r"IF\(`([^`]+)` IS NULL", digest.group(1))
      texts = sorted(",".join(encodeValue(row[name]) for name in columns) for row in self.matching(digest.group(2),
        digest.group(3)))
      actual = hashlib.sha256("|".join(texts).encode("ascii")).hexdigest() if texts else None
      return actual == (None if digest.group(4) == "NULL" else digest.group(4).strip("'"))
    check = re.match(r"^\(SELECT applied FROM \((.*)\) AS ledgerCheck\) = (\d+)$", condition, re.DOTALL)
    if check:
      return self.checks[check.group(1)](self) == int(check.group(2))
    raise AssertionError(f"the fake cannot evaluate {condition!r}")

  def tableChecksum(self, name):
    table = self.tables[name]
    names = [column[0] for column in table.columns]
    text = ",".join(names) + "\n" + "\n".join(sorted("\t".join(encodeValue(row[column]) for column in names)
      for row in table.rows))
    return zlib.crc32(text.encode("ascii"))

  def select(self, statement):
    columns = re.match(r"^SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IFNULL\(CHARACTER_SET_NAME, ''\), EXTRA FROM "
      r"information_schema\.COLUMNS WHERE TABLE_SCHEMA = DATABASE\(\) AND TABLE_NAME IN \((.*)\) ORDER BY TABLE_NAME, "
      r"ORDINAL_POSITION$", statement)
    if columns:
      return [f"{name}\t{column[0]}\t{column[1]}\t{column[2]}\t" for name in sorted(re.findall(r"'(\w+)'", columns.group(1)))
        if name in self.tables for column in self.tables[name].columns]
    rows = re.match(r"^SELECT '(\d+)', (.*) FROM `(\w+)` WHERE \((.*)\)$", statement, re.DOTALL)
    if rows:
      names = re.findall(r"IF\(`([^`]+)` IS NULL", rows.group(2))
      return [rows.group(1) + "\t" + "\t".join(encodeValue(row[name]) for name in names)
        for row in self.matching(rows.group(3), rows.group(4))]
    numbered = re.match(r"^SELECT '(\d{4})', applied FROM \((.*)\) AS ledgerCheck$", statement, re.DOTALL)
    if numbered:
      return [f"{numbered.group(1)}\t{self.checks[numbered.group(2)](self)}"]
    plain = re.match(r"^SELECT applied FROM \((.*)\) AS ledgerCheck$", statement, re.DOTALL)
    if plain:
      return [str(self.checks[plain.group(1)](self))]
    checksum = re.match(r"^CHECKSUM TABLE (.*) EXTENDED$", statement)
    if checksum:
      return [f"peq.{name}\t{self.tableChecksum(name) if name in self.tables else 'NULL'}"
        for name in re.findall(r"`(\w+)`", checksum.group(1))]
    if statement == ("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = "
        "'BASE TABLE' ORDER BY TABLE_NAME"):
      return sorted(self.tables)
    existing = re.match(r"^SELECT TABLE_NAME FROM information_schema\.TABLES WHERE TABLE_SCHEMA = DATABASE\(\) AND "
      r"TABLE_NAME IN \((.*)\)$", statement)
    if existing:
      return [name for name in re.findall(r"'(\w+)'", existing.group(1)) if name in self.tables]
    create = re.match(r"^SHOW CREATE TABLE `(\w+)`$", statement)
    if create:
      text = self.createStatements[create.group(1)]
      return [f"{create.group(1)}\t" + text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")]
    if statement == "SELECT version, bots_version, custom_version FROM db_version":
      return ["\t".join(self.dbVersion)]
    if statement == "SHOW MASTER STATUS":
      return ["\t".join(row) for row in self.masterStatus]
    raise AssertionError(f"the fake does not know the query {statement!r}")


def git(folder, *arguments):
  return subprocess.run(["git", "-C", str(folder), *arguments], check=True, capture_output=True, text=True).stdout


def gitHead(folder):
  return git(folder, "rev-parse", "HEAD").strip()


def committedFiles(folder, revision="HEAD"):
  return set(git(folder, "ls-tree", "-r", "--name-only", revision).split())


def changedInCommit(folder, revision="HEAD"):
  return set(git(folder, "show", "--name-only", "--format=", revision).split())


def planText(steps, inProgress=None, nextStep=None):
  """A plan.md holding steps (lines like '- [ ] 1. Text [0001]') whose In progress and Next sections are given, or the
  first two unticked steps."""
  parsed = ledger.readPlanSteps("\n".join(steps))
  unticked = [step.describe() for step in parsed if not step.ticked]
  inProgress = inProgress if inProgress is not None else (unticked[0] if unticked else "None: every step is done.")
  nextStep = nextStep if nextStep is not None else (unticked[1] if len(unticked) > 1 else "None.")
  return ("# test plan\n\n## Goal\n\nTest the ledger.\n\n## In progress\n\n" + inProgress + "\n\n## Next\n\n" + nextStep
    + "\n\n## Steps\n\n" + "\n".join(steps) + "\n")


def writeChange(folder, name, fields, files=None):
  changeFolder = folder / "changes" / name
  changeFolder.mkdir(parents=True)
  (changeFolder / "change.yaml").write_text(yaml.safe_dump(fields, sort_keys=False), encoding="utf-8")
  for fileName, text in (files or {}).items():
    (changeFolder / fileName).write_text(text, encoding="utf-8")
  return changeFolder


def rowChangeFields(**overrides):
  fields = {
    "title": "Expansion:CurrentExpansion is 19 in ruleset 1",
    "why": "Testing.",
    "kind": "db",
    "scope": [{"table": "rule_values", "where": "ruleset_id = 1"}],
    "disposition": "exploration",
    "significance": "minor",
    "ownerClearance": "pending",
    "activation": "#reload rules",
    "production": "none",
    "commits": [],
  }
  fields.update(overrides)
  return fields


def questFields(**overrides):
  fields = rowChangeFields(title="The quest is fixed", kind="quest", scope=["global/spells/17779.lua"],
    repository="/opt/eqemu/akk-stack/server/quests", commits=["aaaa111", "bbbb222"])
  fields.update(overrides)
  return fields


def clientChangeFields(**overrides):
  fields = {
    "title": "A test file exists",
    "why": "Testing.",
    "kind": "client",
    "scope": ["C:\\test"],
    "disposition": "fixture",
    "significance": "minor",
    "ownerClearance": "cleared",
    "activation": "none",
    "production": "none",
    "commits": [],
    "steps": ["Set-Content -LiteralPath 'C:\\test' -Value 'x'"],
    "undo": ["Remove-Item -LiteralPath 'C:\\test'"],
    "checks": [{"path": "C:\\test", "exists": "file"}],
  }
  fields.update(overrides)
  return fields


@dataclass
class Harness:
  folder: Path
  fake: FakeMariaDB
  events: list
  ledger: ledger.Ledger
  connections: list

  def commitAll(self, message="test setup"):
    git(self.folder, "add", "-A")
    git(self.folder, "commit", "-q", "-m", message)


def makeHarness(folder, fake=None):
  fake = fake if fake is not None else FakeMariaDB()
  events = []
  connections = []

  def openServer():
    connections.append(fake)
    return ledger.Server(fake)

  def push(pushedFolder):
    events.append(("push", gitHead(pushedFolder)))

  fake.observers.append(lambda command, script: events.append(("write" if "gtid_domain_id" in script else "read",
    gitHead(folder))))
  return Harness(folder, fake, events, ledger.Ledger(folder, openServer, push), connections)


ruleColumns = [("ruleset_id", "tinyint", ""), ("rule_name", "varchar", "latin1"), ("rule_value", "varchar", "latin1"),
  ("notes", "text", "latin1")]
expansionUpdate = "UPDATE rule_values SET rule_value = '19' WHERE ruleset_id = 1 AND rule_name = 'Expansion:CurrentExpansion'"
testInsert = "INSERT INTO rule_values (ruleset_id, rule_name, rule_value, notes) VALUES (1, 'Test:New', 'y', NULL)"
testDelete = "DELETE FROM rule_values WHERE ruleset_id = 1 AND rule_name = 'Test:Gone'"
expansionCheck = ("SELECT (SELECT rule_value FROM rule_values WHERE ruleset_id = 1 AND rule_name = "
  "'Expansion:CurrentExpansion') = '19' AS applied")


def ruleRow(rulesetID, name, value, notes):
  return {"ruleset_id": str(rulesetID).encode("ascii"), "rule_name": name, "rule_value": value, "notes": notes}


def expansionValue(fake):
  return next(row["rule_value"] for row in fake.tables["rule_values"].rows if row["ruleset_id"] == b"1"
    and row["rule_name"] == b"Expansion:CurrentExpansion")


def setRule(fake, rulesetID, name, value):
  for row in fake.tables["rule_values"].rows:
    if row["ruleset_id"] == str(rulesetID).encode("ascii") and row["rule_name"] == name:
      row["rule_value"] = value


def ruleFake():
  """rule_values with ruleset 1's rows in the scope ruleset_id = 1 and one row of ruleset 2 outside it, plus the apply
  statements of the test change (an update, an insert and a delete) and its check."""
  fake = FakeMariaDB()
  fake.tables["rule_values"] = FakeTable(ruleColumns, ("ruleset_id", "rule_name"), [
    ruleRow(1, b"Expansion:CurrentExpansion", b"4", b"The current expansion"),
    ruleRow(1, b"Test:Gone", b"x", None),
    ruleRow(1, b"Test:Quoted", b"O'Brien \\ caf\xe9", b""),
    ruleRow(2, b"Expansion:CurrentExpansion", b"7", None),
  ])
  fake.predicates["ruleset_id = 1"] = lambda row: row["ruleset_id"] == b"1"
  fake.predicates["ruleset_id = 1 AND rule_name = 'Expansion:CurrentExpansion'"] = lambda row: row["ruleset_id"] == b"1" \
    and row["rule_name"] == b"Expansion:CurrentExpansion"
  fake.mutations[expansionUpdate] = lambda fake: setRule(fake, 1, b"Expansion:CurrentExpansion", b"19")
  fake.mutations[testInsert] = lambda fake: fake.tables["rule_values"].insert(ruleRow(1, b"Test:New", b"y", None))
  fake.mutations[testDelete] = lambda fake: fake.tables["rule_values"].rows.remove(next(row for row in
    fake.tables["rule_values"].rows if row["rule_name"] == b"Test:Gone"))
  fake.checks[expansionCheck] = lambda fake: int(expansionValue(fake) == b"19")
  return fake


schemaSteps = ["ALTER TABLE items ADD COLUMN a INT", "ALTER TABLE items ADD COLUMN b INT"]
schemaReverts = ["ALTER TABLE items DROP COLUMN b", "ALTER TABLE items DROP COLUMN a"]
schemaCheck = ("SELECT COUNT(*) AS applied FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = "
  "'items' AND COLUMN_NAME IN ('a', 'b')")
itemsCreate = "CREATE TABLE `items` (\n  `id` int(11) NOT NULL,\n  `name` varchar(64) NOT NULL{extra},\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB AUTO_INCREMENT={next} DEFAULT CHARSET=latin1"


def itemsFake():
  """items with two rows, and a two-step schema change that adds columns a and b; failSteps holds statements that fail
  once."""
  fake = FakeMariaDB()
  fake.tables["items"] = FakeTable([("id", "int", ""), ("name", "varchar", "latin1")], ("id",), [{"id": b"1", "name": b"Sword"},
    {"id": b"2", "name": b"Shield"}])
  fake.failSteps = set()

  def render(fake):
    extra = "".join(f",\n  `{column[0]}` int(11) DEFAULT NULL" for column in fake.tables["items"].columns[2:])
    fake.createStatements["items"] = itemsCreate.format(extra=extra, next=len(fake.calls) + 3)

  def addColumn(name, statement):
    def run(fake):
      if statement in fake.failSteps:
        fake.failSteps.discard(statement)
        raise FakeSQLError(f"ERROR 1205 (HY000): Lock wait timeout exceeded running {statement}")
      fake.tables["items"].columns.append((name, "int", ""))
      for row in fake.tables["items"].rows:
        row[name] = None
      render(fake)
    return run

  def dropColumn(name):
    def run(fake):
      fake.tables["items"].columns = [column for column in fake.tables["items"].columns if column[0] != name]
      for row in fake.tables["items"].rows:
        del row[name]
      render(fake)
    return run

  for name, statement in zip("ab", schemaSteps):
    fake.mutations[statement] = addColumn(name, statement)
  for name, statement in zip("ba", schemaReverts):
    fake.mutations[statement] = dropColumn(name)
  fake.checks[schemaCheck] = lambda fake: sum(column[0] in ("a", "b") for column in fake.tables["items"].columns)
  render(fake)
  return fake


def schemaChangeFields(**overrides):
  fields = rowChangeFields(title="items has columns a and b", scope=[{"table": "items"}], schemaSteps=2)
  fields.update(overrides)
  return fields


def schemaFiles():
  return {"apply.sql": "".join(f"{statement};\n" for statement in schemaSteps), "revert.sql": "".join(f"{statement};\n"
    for statement in schemaReverts), "check.sql": schemaCheck + ";\n"}


def rowFiles():
  return {"apply.sql": f"{expansionUpdate};\n{testInsert};\n{testDelete};\n", "check.sql": expansionCheck + ";\n"}


def rowsOf(fake, tableName):
  return sorted((tuple(sorted(row.items())) for row in fake.tables[tableName].rows), key=repr)
