"""Stand-ins for what the ledger reaches over SSH: claudeperidot's MariaDB, behind the mysql client and mysqlbinlog, and its
shell, a real POSIX shell over a temporary folder standing in for the host's /opt/eqemu. Plus helpers that build a ledger in a
temporary git repository whose pushes really reach a bare remote.

The MariaDB stand-in follows MariaDB's documented semantics on its own: it parses the expressions the ledger sends and
evaluates them per row with typed values (FLOAT printed to 6 digits, DOUBLE to every digit, ENUM as its member, BIT as a
number), applies the session's sql_mode, parses literals with MariaDB's escapes and character-set introducers, computes
SHA2, GROUP_CONCAT and SUM itself, splits scripts as the mysql client does (DELIMITER, --force, --comments), and writes a
binary log in mysqlbinlog's form from what each transaction changed. Tests give where predicates, the statements a change
runs and its checks as Python."""
import copy
import hashlib
import os
import re
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

import ledger


class FakeSQLError(Exception):
  def __init__(self, code, state, message):
    super().__init__(f"ERROR {code} ({state}): {message}")
    self.code = code
    self.state = state
    self.message = message


charsetCodecs = {"latin1": "latin-1", "ascii": "ascii", "utf8": "utf-8", "utf8mb3": "utf-8", "utf8mb4": "utf-8"}
integerTypes = ("tinyint", "smallint", "mediumint", "int", "bigint")
textTypes = ("char", "varchar", "tinytext", "text", "mediumtext", "longtext")
binaryTypes = ("binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob")
temporalPatterns = {
  "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
  "datetime": re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?$"),
  "timestamp": re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?$"),
  "time": re.compile(r"^-?\d{2,3}:\d{2}:\d{2}(\.\d{1,6})?$"),
  "year": re.compile(r"^\d{4}$"),
}
defaultSQLMode = "STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION"


def single(value):
  return struct.unpack("<f", struct.pack("<f", value))[0]


@dataclass
class FakeColumn:
  name: str
  dataType: str
  charset: str = ""
  members: tuple = ()
  width: int = 0
  scale: int = 0
  autoIncrement: bool = False
  generated: str = ""

  @property
  def extra(self):
    return self.generated or ("auto_increment" if self.autoIncrement else "")


def asColumn(column):
  return column if isinstance(column, FakeColumn) else FakeColumn(*column)


@dataclass
class FakeTable:
  columns: list
  primaryKey: tuple
  rows: list = field(default_factory=list)
  engine: str = "InnoDB"
  tableType: str = "BASE TABLE"

  def __post_init__(self):
    self.columns = [asColumn(column) for column in self.columns]

  def column(self, name):
    matches = [column for column in self.columns if column.name == name]
    if not matches:
      raise FakeSQLError(1054, "42S22", f"Unknown column '{name}' in 'field list'")
    return matches[0]

  def key(self, row):
    return tuple(row[name] for name in self.primaryKey)

  def insert(self, row):
    key = self.key(row)
    if any(self.key(existing) == key for existing in self.rows):
      raise FakeSQLError(1062, "23000", f"Duplicate entry '{'-'.join(map(str, key))}' for key 'PRIMARY'")
    self.rows.append({column.name: row.get(column.name) for column in self.columns})


# Values while an expression is evaluated: None, int, Decimal, Real, Text, Member (an ENUM or SET value), Bit, Temporal.
@dataclass(frozen=True)
class Real:
  value: float
  isSingle: bool = False


@dataclass(frozen=True)
class Text:
  data: bytes
  charset: str


@dataclass(frozen=True)
class Member:
  data: bytes
  number: int
  charset: str


@dataclass(frozen=True)
class Bit:
  value: int
  width: int


@dataclass(frozen=True)
class Temporal:
  text: str


def formatDouble(value):
  """A DOUBLE as MariaDB prints it: the fewest digits that read back as the same double."""
  if value == int(value) and abs(value) < 1e15:
    return ("-" if str(value).startswith("-") else "") + str(abs(int(value)))
  text = repr(value)
  mantissa, _, exponent = text.partition("e")
  return mantissa + (f"e{int(exponent)}" if exponent else "")


def formatSingle(value):
  """A FLOAT as MariaDB prints it: 6 significant digits."""
  text = f"{value:.6g}"
  mantissa, _, exponent = text.partition("e")
  return mantissa + (f"e{int(exponent)}" if exponent else "")


def stringBytes(value):
  if isinstance(value, Text):
    return value.data
  if isinstance(value, Member):
    return value.data
  if isinstance(value, Temporal):
    return value.text.encode("ascii")
  if isinstance(value, Real):
    return (formatSingle(value.value) if value.isSingle else formatDouble(value.value)).encode("ascii")
  if isinstance(value, Bit):
    return value.value.to_bytes(max(1, (value.width + 7) // 8), "big")
  return str(value).encode("ascii")


def charsetOf(value):
  return value.charset if isinstance(value, (Text, Member)) else "utf8mb4"


def convert(data, fromCharset, toCharset, replace=False):
  if fromCharset == "binary" or toCharset == "binary" or fromCharset == toCharset:
    return data
  text = data.decode(charsetCodecs[fromCharset])
  return text.encode(charsetCodecs[toCharset], errors="replace" if replace else "strict")


def number(value):
  if isinstance(value, (int, Decimal)):
    return value
  if isinstance(value, Real):
    return value.value
  if isinstance(value, Bit):
    return value.value
  if isinstance(value, Member):
    return value.number
  match = re.match(rb"^\s*-?\d+(\.\d+)?([eE][+-]?\d+)?", stringBytes(value))
  return float(match.group(0)) if match else 0.0


def arithmetic(left, right, operator):
  if left is None or right is None:
    return None
  a, b = number(left), number(right)
  if isinstance(a, float) or isinstance(b, float) or isinstance(left, Real) or isinstance(right, Real):
    a, b = float(a), float(b)
    return Real(a + b if operator == "+" else a * b)
  if isinstance(a, Decimal) or isinstance(b, Decimal):
    a, b = Decimal(a), Decimal(b)
  return a + b if operator == "+" else a * b


def roundedInteger(value):
  return int(Decimal(repr(value) if isinstance(value, float) else value).quantize(Decimal(1), ROUND_HALF_UP))


def hexOf(value):
  if isinstance(value, (Text, Member, Temporal)):
    return stringBytes(value).hex().upper()
  return format(roundedInteger(number(value)) & 0xFFFFFFFFFFFFFFFF, "X")


tokenPattern = re.compile(r"""\s*(?:
  (?P<string>'(?:[^'\\]|\\.|'')*')
  |(?P<quoted>`(?:[^`]|``)*`)
  |(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  |(?P<word>@@?[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*)
  |(?P<symbol><=>|[(),+*=])
  |(?P<other>\S)
)""", re.VERBOSE)


def tokenize(text):
  tokens = []
  position = 0
  while position < len(text):
    if not text[position:].strip():
      break
    match = tokenPattern.match(text, position)
    if not match:
      raise AssertionError(f"the stand-in cannot read {text[position:position + 60]!r}")
    kind = match.lastgroup
    tokens.append((kind, match.group(kind), match.start(kind), match.end(kind)))
    position = match.end()
  return tokens


def unquote(text):
  """A quoted literal's characters, with MariaDB's backslash escapes."""
  escapes = {"0": "\0", "'": "'", '"': '"', "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "\\": "\\", "%": "\\%",
    "_": "\\_"}
  body = text[1:-1]
  result = []
  index = 0
  while index < len(body):
    if body[index] == "\\":
      result.append(escapes.get(body[index + 1], body[index + 1]))
      index += 2
    elif body.startswith("''", index):
      result.append("'")
      index += 2
    else:
      result.append(body[index])
      index += 1
  return "".join(result)


class Parser:
  """Reads the expressions the ledger sends into nested tuples."""

  def __init__(self, text):
    self.text = text
    self.tokens = tokenize(text)
    self.index = 0

  def peek(self, offset=0):
    position = self.index + offset
    return self.tokens[position] if position < len(self.tokens) else (None, None, len(self.text), len(self.text))

  def isWord(self, word, offset=0):
    kind, value, _, _ = self.peek(offset)
    return kind == "word" and value.upper() == word

  def take(self, expected=None):
    token = self.peek()
    if token[0] is None or expected is not None and (token[1].upper() if token[0] == "word" else token[1]) != expected:
      raise AssertionError(f"the stand-in expected {expected!r} at {self.text[token[2]:token[2] + 60]!r}")
    self.index += 1
    return token

  def expression(self):
    left = self.additive()
    if self.peek()[1] in ("<=>", "="):
      operator = self.take()[1]
      return ("compare", operator, left, self.additive())
    return left

  def additive(self):
    left = self.multiplicative()
    while self.peek()[1] == "+":
      self.take()
      left = ("arithmetic", "+", left, self.multiplicative())
    return left

  def multiplicative(self):
    left = self.primary()
    while self.peek()[1] == "*":
      self.take()
      left = ("arithmetic", "*", left, self.primary())
    return left

  def rawUntilClose(self):
    """The source text up to the parenthesis that closes the one before it, which is consumed too."""
    depth = 1
    start = self.peek()[2]
    while True:
      kind, value, begin, _ = self.take()
      if value == "(":
        depth += 1
      elif value == ")":
        depth -= 1
        if depth == 0:
          return self.text[start:begin].strip()

  def subquery(self):
    self.take("SELECT")
    selected = self.expression()
    self.take("FROM")
    self.take("(")
    self.take("SELECT")
    inner = self.expression()
    self.take("AS")
    alias = self.take()[1]
    self.take("FROM")
    table = self.take()[1].strip("`")
    self.take("WHERE")
    where = self.rawUntilClose()
    self.take("AS")
    self.take()
    return ("subquery", selected, inner, alias, table, where)

  def primary(self):
    kind, value, start, end = self.take()
    if kind == "string":
      return ("literal", Text(unquote(value).encode("utf-8"), "utf8mb4"))
    if kind == "number":
      if re.search(r"[eE]", value):
        return ("literal", Real(float(value)))
      return ("literal", Decimal(value) if "." in value else int(value))
    if kind == "quoted":
      return ("column", value[1:-1].replace("``", "`"))
    if value == "(":
      if self.isWord("SELECT"):
        node = self.subquery()
      else:
        node = self.expression()
      self.take(")")
      return node
    if kind == "word" and value.startswith("@"):
      return ("variable", value)
    if kind == "word" and value.upper() == "NULL":
      return ("literal", None)
    if kind == "word" and self.peek()[1] == "(":
      return self.function(value.upper())
    if kind == "word":
      return ("column", value)
    raise AssertionError(f"the stand-in cannot read {self.text[start:start + 60]!r}")

  def function(self, name):
    self.take("(")
    if name == "IF":
      tested = self.expression()
      self.take("IS")
      negated = self.isWord("NOT")
      if negated:
        self.take()
      self.take("NULL")
      self.take(",")
      whenTrue = self.expression()
      self.take(",")
      whenFalse = self.expression()
      self.take(")")
      return ("ifNull", tested, negated, whenTrue, whenFalse)
    if name == "CAST":
      argument = self.expression()
      self.take("AS")
      target = self.take()[1].upper()
      self.take(")")
      return ("cast", argument, target)
    if name == "CONVERT":
      argument = self.expression()
      self.take("USING")
      target = self.take()[1].lower()
      self.take(")")
      return ("convert", argument, target)
    if name == "COUNT":
      self.take("*")
      self.take(")")
      return ("count",)
    if name == "GROUP_CONCAT":
      argument = self.expression()
      self.take("ORDER")
      self.take("BY")
      binary = self.isWord("BINARY")
      if binary:
        self.take()
      order = self.expression()
      self.take("SEPARATOR")
      separator = unquote(self.take()[1])
      self.take(")")
      return ("groupConcat", argument, order, separator)
    arguments = []
    if self.peek()[1] != ")":
      arguments.append(self.expression())
      while self.peek()[1] == ",":
        self.take()
        arguments.append(self.expression())
    self.take(")")
    return ("call", name, arguments)


def parseExpression(text):
  parser = Parser(text)
  node = parser.expression()
  if parser.peek()[0] is not None:
    raise AssertionError(f"the stand-in did not read all of {text!r}")
  return node


@dataclass
class Session:
  database: str
  sqlMode: str = defaultSQLMode
  groupConcatMaxLength: int = 1048576
  domain: int = 0
  variables: dict = field(default_factory=dict)
  snapshot: dict = None
  pending: list = field(default_factory=list)


@dataclass
class LoggedTransaction:
  domain: int
  sequence: int
  start: int
  end: int
  events: list


class FakeMariaDB:
  """Stands in for claudeperidot behind the SSH connection: MariaDB behind the mysql client and mysqlbinlog, and, through
  host, the rest of the host's shell."""

  def __init__(self):
    self.tables = {}
    self.predicates = {}
    self.mutations = {}
    self.checks = {}
    self.createStatements = {}
    self.dbVersion = ["9325", "0", "0"]
    self.serverSettings = ["1", "ROW", "1"]
    self.binaryLogOn = True
    self.log = []
    self.nextPosition = 4321
    self.nextSequence = 15
    self.host = None
    self.calls = []
    self.observers = []
    self.afterWrite = []

  def execute(self, command, standardInput=b"", timeoutSeconds=60):
    script = standardInput.decode("utf-8")
    self.calls.append((command, script))
    for observer in self.observers:
      observer(command, script)
    client = re.match(r"^sudo -n docker exec -i akk-stack-mariadb-1 sh -c 'export MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\"; "
      r"exec mysql (.*) (\w+)'$", command)
    if client:
      options = client.group(1).split()
      output, errors = self.runClient(script, Session(client.group(2)), "--force" in options, "--comments" in options)
      for hook in list(self.afterWrite):
        if "gtid_domain_id" in script:
          hook(self)
      return (1 if errors else 0), output.encode("utf-8"), "".join(errors).encode("utf-8")
    binaryLog = re.match(r"^sudo -n docker exec -i akk-stack-mariadb-1 sh -c 'export MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\"; exec "
      r"mysqlbinlog --read-from-remote-server --host=localhost --user=root --base64-output=decode-rows --verbose "
      r"--start-position=(\d+) --to-last-log ([\w.-]+)'$", command)
    if binaryLog:
      return 0, self.renderBinaryLog(int(binaryLog.group(1))).encode("utf-8"), b""
    if self.host is None:
      raise AssertionError(f"the stand-in has no host shell for {command!r}")
    return self.host.run(command, standardInput)

  def streamCommand(self, command, writeOutput, standardInput=b"", timeoutSeconds=60):
    exitCode, output, errors = self.execute(command, standardInput, timeoutSeconds)
    for start in range(0, len(output), 7):
      writeOutput(output[start:start + 7])
    return exitCode, errors

  @property
  def sqlScripts(self):
    return [script for command, script in self.calls if " exec mysql " in command]

  @property
  def writeScripts(self):
    return [script for script in self.sqlScripts if "gtid_domain_id" in script]

  def matching(self, tableName, where):
    return [row for row in self.tables[tableName].rows if self.predicate(where, row) is True]

  def predicate(self, where, row):
    """A where clause's truth for row (True, False or None for NULL), from the predicates the test gives, combined by OR
    and IS NOT TRUE as MariaDB combines them."""
    where = where.strip()
    if where in self.predicates:
      return self.predicates[where](row)
    if where.endswith(" IS NOT TRUE"):
      results = [self.predicate(stripOne(part), row) for part in splitTopLevel(stripOne(where[:-len(" IS NOT TRUE")]),
        " OR ")]
      return (True if True in results else None if None in results else False) is not True
    stripped = stripOne(where)
    if stripped == where:
      raise AssertionError(f"the test gives no predicate for {where!r}")
    return self.predicate(stripped, row)

  def runClient(self, script, session, force, keepComments):
    """What the mysql client prints for script, and its errors: statements split at the delimiter, DELIMITER honoured,
    comments removed unless kept, and the first error ending the run unless force."""
    output = []
    errors = []
    try:
      for line, statement in clientStatements(script):
        if not keepComments:
          statement = removeComments(statement)
        try:
          output.extend(self.runStatement(statement, session))
        except FakeSQLError as error:
          errors.append(f"ERROR {error.code} ({error.state}) at line {line}: {error.message}\n")
          if not force:
            break
    finally:
      if session.snapshot is not None:
        self.tables = session.snapshot
    return "".join(line + "\n" for line in output), errors

  def runStatement(self, statement, session):
    upper = statement.upper()
    if upper.startswith("SET "):
      self.runSet(statement, session)
      return []
    if upper == "START TRANSACTION":
      session.snapshot = copy.deepcopy(self.tables)
      session.pending = []
      return []
    if upper == "COMMIT":
      if session.snapshot is not None and session.pending:
        self.writeLog(session.domain, session.pending)
      session.snapshot = None
      session.pending = []
      return []
    if upper.startswith("BEGIN NOT ATOMIC"):
      self.guard(statement, session)
      return []
    if upper.startswith(("SELECT", "CHECKSUM", "SHOW")):
      return self.select(statement, session)
    self.write(statement, session)
    return []

  def runSet(self, statement, session):
    variable = re.match(r"^SET (@\w+) = (.*)$", statement, re.DOTALL)
    if variable:
      session.variables[variable.group(1)] = self.evaluate(parseExpression(variable.group(2)), session)
      return
    if statement == "SET SESSION sql_mode = CONCAT_WS(',', NULLIF(@@sql_mode, ''), 'NO_AUTO_VALUE_ON_ZERO')":
      session.sqlMode = ",".join(mode for mode in (session.sqlMode, "NO_AUTO_VALUE_ON_ZERO") if mode)
      return
    domain = re.match(r"^SET SESSION gtid_domain_id = (\d+)$", statement)
    if domain:
      session.domain = int(domain.group(1))
      return
    length = re.match(r"^SET SESSION group_concat_max_len = (\d+)$", statement)
    if length:
      session.groupConcatMaxLength = int(length.group(1))
      return
    if statement in ("SET NAMES utf8mb4", "SET time_zone = '+00:00'", "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE"):
      return
    raise AssertionError(f"the stand-in does not know {statement!r}")

  def guard(self, statement, session):
    match = re.match(r"^BEGIN NOT ATOMIC\s+IF \((.*)\) IS NOT TRUE THEN\s+SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
      r"'([^']*)';\s+END IF;\s+END$", statement, re.DOTALL)
    if not match:
      raise AssertionError(f"the stand-in cannot run the compound statement {statement[:200]!r}")
    if self.condition(match.group(1), session) is not True:
      raise FakeSQLError(1644, "45000", match.group(2))

  def condition(self, text, session):
    check = re.match(r"^\(SELECT applied FROM \((.*)\) AS ledgerCheck\) = (\d+)$", text, re.DOTALL)
    if check:
      value = self.runCheck(check.group(1))
      return None if value is None else value == int(check.group(2))
    return truth(self.evaluate(parseExpression(text), session))

  def runCheck(self, text):
    if text not in self.checks:
      raise AssertionError(f"the test gives no check for {text!r}")
    return self.checks[text](self)

  def columnValue(self, table, name, row):
    column = table.column(name)
    value = row[name]
    if value is None:
      return None
    if column.dataType in integerTypes:
      return value
    if column.dataType == "decimal":
      return value
    if column.dataType in ("float", "double"):
      return Real(value, column.dataType == "float")
    if column.dataType in textTypes:
      return Text(value, column.charset)
    if column.dataType in binaryTypes:
      return Text(value, "binary")
    if column.dataType == "enum":
      return Member(value, column.members.index(value.decode(charsetCodecs[column.charset])) + 1, column.charset)
    if column.dataType == "set":
      names = value.decode(charsetCodecs[column.charset]).split(",") if value else []
      return Member(value, sum(1 << column.members.index(name) for name in names), column.charset)
    if column.dataType == "bit":
      return Bit(value, column.width)
    if column.dataType in temporalPatterns:
      return Temporal(value)
    raise AssertionError(f"the stand-in has no type {column.dataType}")

  def evaluate(self, node, session, environment=None, group=None):
    kind = node[0]
    if kind == "literal":
      return node[1]
    if kind == "column":
      table, row, aliases = environment
      if node[1] in aliases:
        return aliases[node[1]]
      return self.columnValue(table, node[1], row)
    if kind == "variable":
      if node[1] == "@@sql_mode":
        return Text(session.sqlMode.encode("ascii"), "utf8mb4")
      return session.variables.get(node[1])
    if kind == "compare":
      left = self.evaluate(node[2], session, environment, group)
      right = self.evaluate(node[3], session, environment, group)
      if node[1] == "<=>":
        return int(comparable(left) == comparable(right))
      return None if left is None or right is None else int(comparable(left) == comparable(right))
    if kind == "arithmetic":
      return arithmetic(self.evaluate(node[2], session, environment, group), self.evaluate(node[3], session, environment,
        group), node[1])
    if kind == "ifNull":
      isNull = self.evaluate(node[1], session, environment, group) is None
      return self.evaluate(node[3] if isNull != node[2] else node[4], session, environment, group)
    if kind == "cast":
      value = self.evaluate(node[1], session, environment, group)
      if value is None:
        return None
      if node[2] == "CHAR":
        return Text(convert(stringBytes(value), charsetOf(value), "utf8mb4") if isinstance(value, (Text, Member))
          else stringBytes(value), "utf8mb4")
      if node[2] == "UNSIGNED":
        return int(number(value))
      raise AssertionError(f"the stand-in cannot cast to {node[2]}")
    if kind == "convert":
      value = self.evaluate(node[1], session, environment, group)
      return None if value is None else Text(convert(stringBytes(value), charsetOf(value), node[2], replace=True), node[2])
    if kind == "subquery":
      return self.subquery(node, session)
    if kind == "count":
      return len(group)
    if kind == "groupConcat":
      if not group:
        return None
      pieces = sorted(((stringBytes(self.evaluate(node[2], session, member)), stringBytes(self.evaluate(node[1], session,
        member))) for member in group))
      joined = node[3].encode("utf-8").join(piece for _, piece in pieces)
      return Text(joined[:session.groupConcatMaxLength], "utf8mb4")
    if kind == "call":
      return self.call(node[1], [lambda argument=argument: self.evaluate(argument, session, environment, group)
        for argument in node[2]], node[2], session, group)
    raise AssertionError(f"the stand-in cannot evaluate {node!r}")

  def call(self, name, arguments, nodes, session, group):
    if name == "SUM":
      values = [self.evaluate(nodes[0], session, member) for member in group]
      values = [Decimal(number(value)) for value in values if value is not None]
      return sum(values) if values else None
    values = [argument() for argument in arguments]
    if name == "CONCAT_WS":
      separator = stringBytes(values[0])
      return Text(separator.join(stringBytes(value) for value in values[1:] if value is not None), "utf8mb4")
    if any(value is None for value in values) and name != "NULLIF":
      return None
    if name == "HEX":
      return Text(hexOf(values[0]).encode("ascii"), "utf8mb4")
    if name == "CONCAT":
      charset = charsetOf(values[0]) if len(values) == 1 else "utf8mb4"
      return Text(b"".join(stringBytes(value) for value in values), charset)
    if name == "SHA2":
      return Text(hashlib.sha256(stringBytes(values[0])).hexdigest().encode("ascii"), "utf8mb4")
    if name == "CONV":
      return Text(str(int(stringBytes(values[0]).decode("ascii"), int(values[1]))).encode("ascii"), "utf8mb4")
    if name == "SUBSTRING":
      text = stringBytes(values[0]).decode("utf-8")
      start = int(values[1]) - 1
      return Text(text[start:start + int(values[2])].encode("utf-8"), "utf8mb4")
    if name == "LEFT":
      value = values[0]
      codec = charsetCodecs.get(charsetOf(value), "latin-1")
      return Text(stringBytes(value).decode(codec)[:int(values[1])].encode(codec), charsetOf(value))
    if name == "ROUND":
      if isinstance(values[0], Real):
        return Real(round(values[0].value, int(values[1])))
      return Decimal(number(values[0])).quantize(Decimal(1).scaleb(-int(values[1])), ROUND_HALF_UP)
    if name == "NULLIF":
      return None if values[0] is not None and stringBytes(values[0]) == stringBytes(values[1]) else values[0]
    raise AssertionError(f"the stand-in has no function {name}")

  def subquery(self, node, session):
    _, selected, inner, alias, tableName, where = node
    if tableName not in self.tables:
      raise FakeSQLError(1146, "42S02", f"Table 'peq.{tableName}' doesn't exist")
    table = self.tables[tableName]
    derived = []
    for row in table.rows:
      if self.predicate(where, row) is True:
        derived.append((table, row, {alias: self.evaluate(inner, session, (table, row, {}))}))
    return self.evaluate(selected, session, None, derived)

  def select(self, statement, session):
    special = self.selectSpecial(statement)
    if special is not None:
      return special
    numbered = re.match(r"^SELECT '(\d{4})', applied FROM \((.*)\) AS ledgerCheck$", statement, re.DOTALL)
    if numbered:
      value = self.runCheck(numbered.group(2))
      return [f"{numbered.group(1)}\t{'NULL' if value is None else value}"]
    parser = Parser(statement)
    parser.take("SELECT")
    nodes = [parser.expression()]
    while parser.peek()[1] == ",":
      parser.take()
      nodes.append(parser.expression())
    if parser.peek()[0] is None:
      return ["\t".join(batchField(self.evaluate(node, session)) for node in nodes)]
    parser.take("FROM")
    name = parser.take()[1].strip("`")
    parser.take("WHERE")
    where = statement[parser.peek()[2]:]
    if name not in self.tables:
      raise FakeSQLError(1146, "42S02", f"Table 'peq.{name}' doesn't exist")
    table = self.tables[name]
    return ["\t".join(batchField(self.evaluate(node, session, (table, row, {}))) for node in nodes)
      for row in table.rows if self.predicate(where, row) is True]

  def selectSpecial(self, statement):
    columns = re.match(r"^SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IFNULL\(CHARACTER_SET_NAME, ''\), EXTRA FROM "
      r"information_schema\.COLUMNS WHERE TABLE_SCHEMA = DATABASE\(\) AND TABLE_NAME IN \((.*)\) ORDER BY TABLE_NAME, "
      r"ORDINAL_POSITION$", statement)
    if columns:
      return [f"{name}\t{column.name}\t{column.dataType}\t{column.charset}\t{column.extra}"
        for name in sorted(re.findall(r"'(\w+)'", columns.group(1))) if name in self.tables
        for column in self.tables[name].columns]
    kinds = re.match(r"^SELECT TABLE_NAME, TABLE_TYPE, ENGINE FROM information_schema\.TABLES WHERE TABLE_SCHEMA = DATABASE\(\) "
      r"AND TABLE_NAME IN \((.*)\)$", statement)
    if kinds:
      return [f"{name}\t{self.tables[name].tableType}\t{self.tables[name].engine}" for name in re.findall(r"'(\w+)'",
        kinds.group(1)) if name in self.tables]
    existing = re.match(r"^SELECT TABLE_NAME FROM information_schema\.TABLES WHERE TABLE_SCHEMA = DATABASE\(\) AND "
      r"TABLE_NAME IN \((.*)\)$", statement)
    if existing:
      return [name for name in re.findall(r"'(\w+)'", existing.group(1)) if name in self.tables]
    if statement == ("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = "
        "'BASE TABLE' ORDER BY TABLE_NAME"):
      return sorted(name for name, table in self.tables.items() if table.tableType == "BASE TABLE")
    checksum = re.match(r"^CHECKSUM TABLE (.*) EXTENDED$", statement)
    if checksum:
      return [f"peq.{name}\t{self.tableChecksum(name) if name in self.tables else 'NULL'}"
        for name in re.findall(r"`(\w+)`", checksum.group(1))]
    create = re.match(r"^SHOW CREATE TABLE `(\w+)`$", statement)
    if create:
      text = self.createStatements[create.group(1)]
      return [f"{create.group(1)}\t" + text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")]
    if statement == "SELECT version, bots_version, custom_version FROM db_version":
      return ["\t".join(self.dbVersion)]
    if statement == "SHOW MASTER STATUS":
      return [f"binlog.000002\t{self.nextPosition}\t\t"] if self.binaryLogOn else []
    if statement == "SELECT @@log_bin, @@binlog_format, @@binlog_annotate_row_events":
      return ["\t".join(self.serverSettings)]
    return None

  def tableChecksum(self, name):
    table = self.tables[name]
    names = [column.name for column in table.columns]
    total = zlib.crc32(",".join(names).encode("ascii"))
    for row in table.rows:
      total += zlib.crc32(repr([row[column] for column in names]).encode("utf-8"))
    return total & 0xFFFFFFFF

  def write(self, statement, session):
    tag = re.match(r"^\s*/\*.*?\*/\s*", statement, re.DOTALL)
    bare = statement[tag.end():] if tag else statement
    before = copy.deepcopy(self.tables)
    try:
      if bare in self.mutations:
        self.mutations[bare](self)
      else:
        self.writeGenerated(bare, session)
    except FakeSQLError:
      self.tables = before
      raise
    if re.match(r"^(ALTER|CREATE|DROP|RENAME|TRUNCATE)\b", bare, re.IGNORECASE):
      self.writeLog(session.domain, [("query", statement)])
      return
    events = [("annotate", statement)] + rowEvents(before, self.tables)
    if session.snapshot is None:
      self.writeLog(session.domain, events)
    else:
      session.pending.extend(events)

  def writeGenerated(self, statement, session):
    delete = re.match(r"^DELETE FROM `(\w+)` WHERE \((.*)\)$", statement, re.DOTALL)
    if delete:
      table = self.tables[delete.group(1)]
      table.rows = [row for row in table.rows if self.predicate(delete.group(2), row) is not True]
      return
    insert = re.match(r"^INSERT INTO `(\w+)` \((.*?)\) VALUES\n(.*)$", statement, re.DOTALL)
    if insert:
      table = self.tables[insert.group(1)]
      names = [name.strip().strip("`") for name in insert.group(2).split(",")]
      for tokens in parseValueRows(insert.group(3)):
        if len(tokens) != len(names):
          raise FakeSQLError(1136, "21S01", "Column count doesn't match value count at row 1")
        row = {column.name: None for column in table.columns}
        for name, token in zip(names, tokens):
          row[name] = storedValue(table, table.column(name), token, session)
        table.insert(row)
      return
    raise AssertionError(f"the stand-in does not know the statement {statement!r}")

  def writeLog(self, domain, events):
    if not self.binaryLogOn:
      return
    start = self.nextPosition
    self.nextPosition += 100 * (len(events) + 2)
    self.log.append(LoggedTransaction(domain, self.nextSequence, start, self.nextPosition, events))
    self.nextSequence += 1

  def externalWrite(self, statement, mutation, domain=0):
    """A write by something other than the ledger: a GM command, Spire, a zone process."""
    before = copy.deepcopy(self.tables)
    mutation(self)
    if re.match(r"^(ALTER|CREATE|DROP)\b", statement, re.IGNORECASE):
      self.writeLog(domain, [("query", statement)])
    else:
      self.writeLog(domain, [("annotate", statement)] + rowEvents(before, self.tables))

  def renderBinaryLog(self, startPosition):
    lines = ["/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=1*/;", "/*!40019 SET @@session.max_insert_delayed_threads=0*/;",
      "/*!50003 SET @OLD_COMPLETION_TYPE=@@COMPLETION_TYPE,COMPLETION_TYPE=0*/;", "DELIMITER /*!*/;"]
    for transaction in self.log:
      if transaction.start < startPosition:
        continue
      lines += renderTransaction(transaction)
    lines += ["DELIMITER ;", "# End of log file", "ROLLBACK /* added by mysqlbinlog */;",
      "/*!50003 SET COMPLETION_TYPE=@OLD_COMPLETION_TYPE*/;", "/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=0*/;"]
    return "\n".join(lines) + "\n"


def truth(value):
  if value is None:
    return None
  return number(value) != 0


def comparable(value):
  if value is None:
    return None
  if isinstance(value, (Text, Member, Temporal)):
    return ("text", stringBytes(value))
  return ("number", Decimal(repr(number(value))) if isinstance(number(value), float) else Decimal(number(value)))


def batchField(value):
  if value is None:
    return "NULL"
  return stringBytes(value).decode("utf-8", "replace").replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def stripOne(text):
  """text without one pair of parentheses around all of it."""
  text = text.strip()
  if text.startswith("(") and text.endswith(")") and matchingClose(text, 0) == len(text) - 1:
    return text[1:-1].strip()
  return text


def matchingClose(text, opening):
  depth = 0
  quote = None
  for index in range(opening, len(text)):
    character = text[index]
    if quote:
      if character == quote:
        quote = None
    elif character in "'`\"":
      quote = character
    elif character == "(":
      depth += 1
    elif character == ")":
      depth -= 1
      if depth == 0:
        return index
  return -1


def splitTopLevel(text, separator):
  parts = []
  depth = 0
  start = 0
  index = 0
  while index < len(text):
    if text[index] == "(":
      depth += 1
    elif text[index] == ")":
      depth -= 1
    elif depth == 0 and text.startswith(separator, index):
      parts.append(text[start:index])
      index += len(separator)
      start = index
      continue
    index += 1
  parts.append(text[start:])
  return parts


def clientStatements(script):
  """(line, statement) for each statement the mysql client would send: split at the delimiter outside quotes and comments,
  with DELIMITER lines changing it."""
  delimiter = ";"
  statements = []
  current = []
  startLine = None
  quote = None
  inBlockComment = False
  for lineNumber, line in enumerate(script.split("\n"), start=1):
    if not "".join(current).strip() and not quote and not inBlockComment:
      current = []
      startLine = None
      stripped = line.strip()
      if stripped.upper().startswith("DELIMITER "):
        delimiter = stripped.split()[1]
        continue
      if not stripped or stripped.startswith("-- ") or stripped == "--":
        continue
    if startLine is None:
      startLine = lineNumber
    index = 0
    text = line + "\n"
    while index < len(text):
      if inBlockComment:
        end = text.find("*/", index)
        if end == -1:
          current.append(text[index:])
          index = len(text)
          continue
        current.append(text[index:end + 2])
        index = end + 2
        inBlockComment = False
        continue
      if quote:
        if text[index] == "\\" and quote != "`":
          current.append(text[index:index + 2])
          index += 2
          continue
        if text[index] == quote:
          quote = None
        current.append(text[index])
        index += 1
        continue
      if text.startswith("/*", index):
        inBlockComment = True
        current.append("/*")
        index += 2
        continue
      if text.startswith("-- ", index) or text[index] == "#":
        current.append(text[index:])
        index = len(text)
        continue
      if text[index] in "'\"`":
        quote = text[index]
        current.append(text[index])
        index += 1
        continue
      if text.startswith(delimiter, index):
        statement = "".join(current).strip()
        if statement:
          statements.append((startLine, statement))
        current = []
        startLine = None
        index += len(delimiter)
        continue
      current.append(text[index])
      index += 1
  if "".join(current).strip():
    raise AssertionError(f"an unterminated statement: {''.join(current)!r}")
  return statements


def removeComments(statement):
  """The statement as the mysql client sends it without --comments: plain comments gone, versioned ones kept."""
  result = []
  index = 0
  quote = None
  while index < len(statement):
    character = statement[index]
    if quote:
      result.append(character)
      if character == "\\" and quote != "`" and index + 1 < len(statement):
        result.append(statement[index + 1])
        index += 2
        continue
      if character == quote:
        quote = None
      index += 1
      continue
    if character in "'\"`":
      quote = character
    elif statement.startswith("/*", index) and not statement.startswith(("/*!", "/*M!"), index):
      index = statement.index("*/", index) + 2
      continue
    result.append(character)
    index += 1
  return "".join(result).strip()


def rowEvents(before, after):
  """The row events a statement's changes make: each table's rows inserted, updated and deleted, by primary key."""
  events = []
  for name, table in after.items():
    old = {table.key(row): row for row in before[name].rows} if name in before else {}
    new = {table.key(row): row for row in table.rows}
    counts = {"INSERT": sum(1 for key in new if key not in old), "DELETE": sum(1 for key in old if key not in new),
      "UPDATE": sum(1 for key in new if key in old and new[key] != old[key])}
    for operation in ("DELETE", "UPDATE", "INSERT"):
      if counts[operation]:
        events.append(("rows", name, operation, counts[operation]))
  return events


def renderTransaction(transaction):
  stamp = "261011 10:00:00"
  position = transaction.start
  lines = [f"# at {position}"]
  isQuery = transaction.events and transaction.events[0][0] == "query"
  lines.append(f"#{stamp} server id 1  end_log_pos {position + 42} CRC32 0x0000abcd \tGTID {transaction.domain}-1-"
    f"{transaction.sequence} {'ddl' if isQuery else 'trans'}")
  if transaction.domain:
    lines.append(f"/*!100001 SET @@session.gtid_domain_id={transaction.domain}*//*!*/;")
  lines.append(f"/*!100001 SET @@session.gtid_seq_no={transaction.sequence}*//*!*/;")
  if not isQuery:
    lines += ["START TRANSACTION", "/*!*/;"]
  position += 42
  tableNumber = 30
  for event in transaction.events:
    if event[0] == "query":
      lines += [f"# at {position}", f"#{stamp} server id 1  end_log_pos {position + 90} CRC32 0x0000abcd \tQuery\tthread_id=12\t"
        "exec_time=0\terror_code=0\txid=0", "use `peq`/*!*/;", "SET TIMESTAMP=1791626760/*!*/;",
        "SET @@session.pseudo_thread_id=12/*!*/;", "/*!\\C utf8mb4 *//*!*/;"]
      lines += event[1].split("\n")
      lines.append("/*!*/;")
      position += 90
    elif event[0] == "annotate":
      lines += [f"# at {position}", f"#{stamp} server id 1  end_log_pos {position + 60} CRC32 0x0000abcd \tAnnotate_rows:"]
      lines += [f"#Q> {line}" for line in event[1].split("\n")]
      position += 60
    else:
      _, name, operation, count = event
      tableNumber += 1
      eventName = {"INSERT": "Write_rows", "UPDATE": "Update_rows", "DELETE": "Delete_rows"}[operation]
      lines.append(f"#{stamp} server id 1  end_log_pos {position + 40} CRC32 0x0000abcd \tTable_map: `peq`.`{name}` mapped to "
        f"number {tableNumber}")
      lines += [f"# at {position + 40}", f"#{stamp} server id 1  end_log_pos {position + 80} CRC32 0x0000abcd \t{eventName}: "
        f"table id {tableNumber} flags: STMT_END_F"]
      for _ in range(count):
        statementName = {"INSERT": "INSERT INTO", "UPDATE": "UPDATE", "DELETE": "DELETE FROM"}[operation]
        lines.append(f"### {statementName} `peq`.`{name}`")
        lines += ["### WHERE", "###   @1=1"] if operation != "INSERT" else []
        lines += ["### SET", "###   @1=1"] if operation != "DELETE" else []
      position += 80
  if not isQuery:
    lines += [f"# at {position}", f"#{stamp} server id 1  end_log_pos {position + 31} CRC32 0x0000abcd \tXid = 120",
      "COMMIT/*!*/;"]
  return lines


literalPattern = re.compile(r"\s*(?:(NULL)|('(?:[^'\\]|\\.|'')*')|_(\w+) X'([0-9A-Fa-f]*)'|X'([0-9A-Fa-f]*)'|"
  r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?))\s*")


def parseValueRows(text):
  """The rows of an INSERT's VALUES list as (kind, payload, charset) tokens."""
  rows = []
  position = 0
  while True:
    opening = re.compile(r"\s*\(").match(text, position)
    if not opening:
      raise FakeSQLError(1064, "42000", f"You have an error in your SQL syntax near '{text[position:position + 40]}'")
    position = opening.end()
    values = []
    while True:
      match = literalPattern.match(text, position)
      if not match:
        raise FakeSQLError(1064, "42000", f"You have an error in your SQL syntax near '{text[position:position + 40]}'")
      if match.group(1):
        values.append(("null", None, None))
      elif match.group(2) is not None:
        values.append(("quoted", unquote(match.group(2)).encode("utf-8"), "utf8mb4"))
      elif match.group(3):
        values.append(("introduced", bytes.fromhex(match.group(4)), match.group(3)))
      elif match.group(5) is not None:
        values.append(("hex", bytes.fromhex(match.group(5)), "binary"))
      else:
        values.append(("number", match.group(6), None))
      position = match.end()
      if text.startswith(",", position):
        position += 1
        continue
      if not text.startswith(")", position):
        raise FakeSQLError(1064, "42000", f"You have an error in your SQL syntax near '{text[position:position + 40]}'")
      position += 1
      break
    rows.append(values)
    rest = text[position:].strip()
    if not rest:
      return rows
    if not rest.startswith(","):
      raise FakeSQLError(1064, "42000", f"You have an error in your SQL syntax near '{rest[:40]}'")
    position = text.index(",", position) + 1


def literalNumber(text):
  if re.search(r"[eE]", text):
    return float(text)
  return Decimal(text) if "." in text else int(text)


def literalDouble(text):
  """A number literal as a double: one with an exponent is a double literal already; one without is an exact integer or
  DECIMAL, which has no negative zero, so -0 is 0."""
  value = literalNumber(text)
  if isinstance(value, float):
    return value
  return float(value) if value != 0 else 0.0


def storedValue(table, column, token, session):
  """What MariaDB stores for a literal in column under the session's sql_mode (strict, as MariaDB 10.5 runs by default)."""
  kind, payload, charset = token
  if column.generated:
    raise FakeSQLError(1906, "HY000", f"The value specified for generated column '{column.name}' in table '{table}' has been "
      "ignored")
  if column.autoIncrement and (kind == "null" or kind == "number" and literalNumber(payload) == 0
      and "NO_AUTO_VALUE_ON_ZERO" not in session.sqlMode.split(",")):
    return max((row[column.name] for row in table.rows), default=0) + 1
  if kind == "null":
    return None
  if column.dataType in integerTypes:
    if kind != "number":
      raise FakeSQLError(1366, "22007", f"Incorrect integer value for column '{column.name}'")
    return roundedInteger(literalNumber(payload))
  if column.dataType == "decimal":
    return Decimal(payload).quantize(Decimal(1).scaleb(-column.scale), ROUND_HALF_UP)
  if column.dataType in ("float", "double"):
    value = literalDouble(payload) if kind == "number" else float(payload.decode("ascii"))
    return single(value) if column.dataType == "float" else value
  if column.dataType == "bit":
    return int(payload) if kind == "number" else int.from_bytes(payload, "big")
  if kind == "number":
    payload, charset = payload.encode("ascii"), "ascii"
  if column.dataType in binaryTypes:
    return payload
  if column.dataType in temporalPatterns:
    text = payload.decode("ascii")
    if not temporalPatterns[column.dataType].match(text):
      raise FakeSQLError(1292, "22007", f"Incorrect {column.dataType} value: '{text}' for column '{column.name}'")
    return text
  try:
    data = convert(payload, charset, column.charset)
  except UnicodeError as error:
    raise FakeSQLError(1366, "22007", f"Incorrect string value for column '{column.name}'") from error
  if column.dataType == "enum":
    text = data.decode(charsetCodecs[column.charset])
    if text not in column.members:
      raise FakeSQLError(1265, "01000", f"Data truncated for column '{column.name}' at row 1")
    return data
  if column.dataType == "set":
    names = data.decode(charsetCodecs[column.charset]).split(",") if data else []
    if any(name not in column.members for name in names):
      raise FakeSQLError(1265, "01000", f"Data truncated for column '{column.name}' at row 1")
    return ",".join(member for member in column.members if member in names).encode(charsetCodecs[column.charset])
  return data


def gitShell():
  """Git for Windows' POSIX sh and its usr/bin, which stand in for the host's shell and tools."""
  root = Path(subprocess.run(["git", "--exec-path"], check=True, capture_output=True, text=True).stdout.strip()).parents[2]
  shell = root / "usr" / "bin" / "sh.exe"
  if not shell.is_file():
    raise AssertionError(f"no POSIX sh beside git at {shell}")
  return shell


def posixPath(path):
  path = Path(path).resolve()
  return "/" + path.drive[0].lower() + path.as_posix()[2:]


class FakeHost:
  """claudeperidot's shell: commands run in a real POSIX sh, with /opt/eqemu standing for root/opt/eqemu, sudo dropping its
  options, python3 the test's Python, and git and sha256sum behaving as on Linux (no line-ending conversion, two spaces
  before a file name)."""

  def __init__(self, root):
    self.root = Path(root)
    (self.root / "opt" / "eqemu").mkdir(parents=True, exist_ok=True)
    self.shims = self.root / "shims"
    self.shims.mkdir(exist_ok=True)
    (self.shims / "sudo").write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do\n  case "$1" in\n    -n) shift ;;\n    -u) shift 2 ;;\n'
      '    *) break ;;\n  esac\ndone\nexec "$@"\n', encoding="utf-8", newline="\n")
    (self.shims / "python3").write_text(f'#!/bin/sh\nexec "{posixPath(sys.executable)}" "$@"\n', encoding="utf-8", newline="\n")
    (self.shims / "sha256sum").write_text('#!/bin/sh\n/usr/bin/sha256sum "$@" | sed "s/^\\([0-9a-f]*\\) \\*/\\1  /"\n',
      encoding="utf-8", newline="\n")
    self.shell = gitShell()
    self.commands = []

  def path(self, hostPath):
    return self.root / hostPath.lstrip("/")

  def run(self, command, standardInput=b""):
    self.commands.append(command)
    mapped = command.replace("/opt/eqemu", posixPath(self.root) + "/opt/eqemu")
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join([str(self.shims), str(self.shell.parent), environment["PATH"]])
    environment.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "false"})
    result = subprocess.run([str(self.shell), "-c", mapped], input=standardInput, capture_output=True, env=environment,
      timeout=120)
    return result.returncode, result.stdout, result.stderr


def git(folder, *arguments):
  return subprocess.run(["git", "-C", str(folder), *arguments], check=True, capture_output=True, text=True).stdout


def gitHead(folder, revision="HEAD"):
  return git(folder, "rev-parse", revision).strip()


def remoteHead(remote, branch="claude"):
  """The commit a bare remote's branch points to, or None before the first push."""
  result = subprocess.run(["git", "-C", str(remote), "rev-parse", "--verify", "--quiet", branch], capture_output=True, text=True)
  return result.stdout.strip() or None


def committedFiles(folder, revision="HEAD"):
  return set(git(folder, "ls-tree", "-r", "--full-tree", "--name-only", revision).split())


def changedInCommit(folder, revision="HEAD"):
  return set(git(folder, "show", "--name-only", "--format=", revision).split())


def makeRepository(root, branch="claude"):
  """A git repository at root on branch, with one empty commit and a bare remote origin beside it that pushes really reach."""
  root.mkdir(parents=True)
  remote = root.parent / (root.name + "Remote.git")
  subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(remote)], check=True, capture_output=True)
  git(root, "init", "-q", "-b", branch)
  git(root, "config", "user.name", "Ledger Test")
  git(root, "config", "user.email", "ledger@test.invalid")
  git(root, "config", "core.autocrlf", "false")
  git(root, "remote", "add", "origin", str(remote))
  git(root, "commit", "-q", "--allow-empty", "-m", "Empty repository")
  return remote


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


cleared = {"ownerMessage": "2026-10-11: 'yes, carry it to Peridot'"}


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
    repository="/opt/eqemu/akk-stack/server/quests", source="C:\\source", commits=["aaaa111", "bbbb222"])
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
    "ownerClearance": cleared,
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
  remote: Path

  def commitAll(self, message="test setup"):
    git(self.folder, "add", "-A")
    git(self.folder, "commit", "-q", "-m", message)

  def remoteHead(self):
    return remoteHead(self.remote)


def makeHarness(folder, fake=None):
  """A ledger over folder whose server is fake and whose pushes really reach folder's bare remote; events records each
  push and each SQL script, with the local and remote heads at that moment."""
  fake = fake if fake is not None else FakeMariaDB()
  events = []
  connections = []
  remote = Path(git(folder, "remote", "get-url", "origin").strip())

  def openServer():
    connections.append(fake)
    return ledger.Server(fake)

  def push(pushedFolder):
    ledger.pushBranch(pushedFolder)
    events.append(("push", gitHead(pushedFolder)))

  def observe(command, script):
    if " exec mysql " in command:
      events.append(("write" if "gtid_domain_id" in script else "read", gitHead(folder), remoteHead(remote)))

  fake.observers.append(observe)
  return Harness(folder, fake, events, ledger.Ledger(folder, openServer, push), connections, remote)


ruleColumns = [("ruleset_id", "tinyint"), ("rule_name", "varchar", "latin1"), ("rule_value", "varchar", "latin1"),
  ("notes", "text", "latin1")]
expansionUpdate = "UPDATE rule_values SET rule_value = '19' WHERE ruleset_id = 1 AND rule_name = 'Expansion:CurrentExpansion'"
testInsert = "INSERT INTO rule_values (ruleset_id, rule_name, rule_value, notes) VALUES (1, 'Test:New', 'y', NULL)"
testDelete = "DELETE FROM rule_values WHERE ruleset_id = 1 AND rule_name = 'Test:Gone'"
expansionCheck = ("SELECT (SELECT rule_value FROM rule_values WHERE ruleset_id = 1 AND rule_name = "
  "'Expansion:CurrentExpansion') = '19' AS applied")


def ruleRow(rulesetID, name, value, notes):
  return {"ruleset_id": rulesetID, "rule_name": name, "rule_value": value, "notes": notes}


def expansionValue(fake, rulesetID=1):
  return next(row["rule_value"] for row in fake.tables["rule_values"].rows if row["ruleset_id"] == rulesetID
    and row["rule_name"] == b"Expansion:CurrentExpansion")


def setRule(fake, rulesetID, name, value):
  for row in fake.tables["rule_values"].rows:
    if row["ruleset_id"] == rulesetID and row["rule_name"] == name:
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
  fake.predicates["ruleset_id = 1"] = lambda row: row["ruleset_id"] == 1
  fake.predicates["ruleset_id = 2"] = lambda row: row["ruleset_id"] == 2
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
itemsCreate = ("CREATE TABLE `items` (\n  `id` int(11) NOT NULL,\n  `name` varchar(64) NOT NULL{extra},\n  PRIMARY KEY (`id`)\n) "
  "ENGINE=InnoDB AUTO_INCREMENT={next} DEFAULT CHARSET=latin1")


def itemsFake():
  """items with two rows, and a two-step schema change that adds columns a and b; failSteps holds statements that fail
  once."""
  fake = FakeMariaDB()
  fake.tables["items"] = FakeTable([("id", "int"), ("name", "varchar", "latin1")], ("id",), [{"id": 1, "name": b"Sword"},
    {"id": 2, "name": b"Shield"}])
  fake.failSteps = set()

  def render(fake):
    extra = "".join(f",\n  `{column.name}` int(11) DEFAULT NULL" for column in fake.tables["items"].columns[2:])
    fake.createStatements["items"] = itemsCreate.format(extra=extra, next=len(fake.calls) + 3)

  def addColumn(name, statement):
    def run(fake):
      if statement in fake.failSteps:
        fake.failSteps.discard(statement)
        raise FakeSQLError(1205, "HY000", f"Lock wait timeout exceeded running {statement}")
      fake.tables["items"].columns.append(FakeColumn(name, "int"))
      for row in fake.tables["items"].rows:
        row[name] = None
      render(fake)
    return run

  def dropColumn(name):
    def run(fake):
      fake.tables["items"].columns = [column for column in fake.tables["items"].columns if column.name != name]
      for row in fake.tables["items"].rows:
        del row[name]
      render(fake)
    return run

  for name, statement in zip("ab", schemaSteps):
    fake.mutations[statement] = addColumn(name, statement)
  for name, statement in zip("ba", schemaReverts):
    fake.mutations[statement] = dropColumn(name)
  fake.checks[schemaCheck] = lambda fake: sum(column.name in ("a", "b") for column in fake.tables["items"].columns)
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
  return sorted(repr(sorted(row.items())) for row in fake.tables[tableName].rows)
