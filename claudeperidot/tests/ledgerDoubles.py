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
import math
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
serverVersion = "10.5.4-MariaDB-1:10.5.4+maria~focal-log"
binaryLogFile = "mysql-bin.000002"


def single(value):
  return struct.unpack("<f", struct.pack("<f", value))[0]


def temporalText(column, text):
  """A date or time as MariaDB stores it in column: a DATETIME, TIMESTAMP or TIME keeps exactly its fractional digits, the
  rest of a fraction cut off and a short one padded with zeros."""
  if column.dataType not in ("datetime", "timestamp", "time"):
    return text
  whole, _, fraction = text.partition(".")
  return whole + ("." + (fraction + "0" * 6)[:column.fractionalDigits] if column.fractionalDigits else "")


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
  fractionalDigits: int = 0

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


def formatReal(value, digits):
  """A real number as MariaDB 10.5's my_gcvt prints it from dtoa's digits: positional unless the decimal point falls more
  than 14 places before the first digit or more than 15 after it on a whole number, then N.NNNeX."""
  sign = "-" if math.copysign(1.0, value) < 0 else ""
  if value == 0:
    return sign + "0"
  shape = digits.as_tuple()
  text = "".join(map(str, shape.digits))
  point = len(text) + shape.exponent
  if point >= -14 and (point <= 15 or len(text) > point):
    if point <= 0:
      return sign + "0." + "0" * -point + text
    if point < len(text):
      return sign + text[:point] + "." + text[point:]
    return sign + text + "0" * (point - len(text))
  return sign + text[0] + ("." + text[1:] if len(text) > 1 else "") + f"e{point - 1}"


def formatDouble(value):
  """A DOUBLE as MariaDB prints it: the fewest digits that read back as the same double."""
  return formatReal(value, Decimal(repr(abs(value))).normalize())


def formatSingle(value):
  """A FLOAT as MariaDB prints it: at most 6 significant digits."""
  return formatReal(value, Decimal(format(abs(value), ".5e")).normalize())


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
  """HEX of a string is its bytes; of a number, the 64-bit two's complement of it rounded half away from zero, a real
  outside the 64-bit range all ones, as MariaDB's HEX gives them."""
  if isinstance(value, (Text, Member, Temporal)):
    return stringBytes(value).hex().upper()
  value = number(value)
  if isinstance(value, float):
    whole = -1 if value <= -2.0 ** 63 or value >= 2.0 ** 64 else int(value + 0.5 if value > 0 else value - 0.5)
  else:
    whole = roundedInteger(value)
  return format(whole & 0xFFFFFFFFFFFFFFFF, "X")


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
      if self.isWord("IS"):
        self.take()
        negated = self.isWord("NOT")
        if negated:
          self.take()
        self.take("NULL")
        tested = ("isNull", tested, negated)
      self.take(",")
      whenTrue = self.expression()
      self.take(",")
      whenFalse = self.expression()
      self.take(")")
      return ("if", tested, whenTrue, whenFalse)
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
      exitCode, output, errors = self.runClient(script, Session(client.group(2)), "--force" in options,
        "--comments" in options)
      for hook in list(self.afterWrite):
        if "gtid_domain_id" in script:
          hook(self)
      return exitCode, output.encode("utf-8", "surrogateescape"), "".join(errors).encode("utf-8")
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
    """The mysql client's exit code for script, what it prints and its errors: each query run as the client sends it, and
    the first error ending the run with exit code 1 unless force, under which the run goes on and exits 0."""
    output = []
    errors = []
    exitCode = 0
    try:
      for line, statement in clientStatements(script, keepComments):
        try:
          output.extend(self.runStatement(statement, session))
        except FakeSQLError as error:
          errors.append(f"ERROR {error.code} ({error.state}) at line {line}: {error.message}\n")
          if not force:
            exitCode = 1
            break
    finally:
      if session.snapshot is not None:
        self.tables = session.snapshot
    return exitCode, "".join(line + "\n" for line in output), errors

  def runStatement(self, received, session):
    """Runs a query as the server received it; its comments matter only to the binary log."""
    statement = removeComments(received)
    if not statement:
      return []
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
    self.write(received.strip(), statement, session)
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
      text = value.decode(charsetCodecs[column.charset])
      if text not in column.members and text:
        raise AssertionError(f"{column.name} holds {text!r}, which is not one of its ENUM's members")
      return Member(value, column.members.index(text) + 1 if text in column.members else 0, column.charset)
    if column.dataType == "set":
      names = value.decode(charsetCodecs[column.charset]).split(",") if value else []
      return Member(value, sum(1 << column.members.index(name) for name in names), column.charset)
    if column.dataType == "bit":
      return Bit(value, column.width)
    if column.dataType in temporalPatterns:
      if temporalText(column, value) != value:
        raise AssertionError(f"{column.name} holds {value!r}, which MariaDB never stores in a {column.dataType} with "
          f"{column.fractionalDigits} fractional digits")
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
    if kind == "isNull":
      return int((self.evaluate(node[1], session, environment, group) is None) != node[2])
    if kind == "if":
      chosen = node[2] if truth(self.evaluate(node[1], session, environment, group)) is True else node[3]
      return self.evaluate(chosen, session, environment, group)
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
      raise FakeSQLError(1146, "42S02", f"Table '{session.database}.{tableName}' doesn't exist")
    table = self.tables[tableName]
    derived = []
    for row in table.rows:
      if self.predicate(where, row) is True:
        derived.append((table, row, {alias: self.evaluate(inner, session, (table, row, {}))}))
    return self.evaluate(selected, session, None, derived)

  def select(self, statement, session):
    special = self.selectSpecial(statement, session)
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
      raise FakeSQLError(1146, "42S02", f"Table '{session.database}.{name}' doesn't exist")
    table = self.tables[name]
    return ["\t".join(batchField(self.evaluate(node, session, (table, row, {}))) for node in nodes)
      for row in table.rows if self.predicate(where, row) is True]

  def selectSpecial(self, statement, session):
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
      return [f"{session.database}.{name}\t{self.tableChecksum(name) if name in self.tables else 'NULL'}"
        for name in re.findall(r"`(\w+)`", checksum.group(1))]
    create = re.match(r"^SHOW CREATE TABLE `(\w+)`$", statement)
    if create:
      text = self.createStatements[create.group(1)]
      return [f"{create.group(1)}\t" + text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")]
    if statement == "SELECT version, bots_version, custom_version FROM db_version":
      return ["\t".join(self.dbVersion)]
    if statement == "SHOW MASTER STATUS":
      return [f"{binaryLogFile}\t{self.nextPosition}\t\t"] if self.binaryLogOn else []
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

  def write(self, statement, bare, session):
    """Runs a write: bare is the statement without its comments, and statement as received is what the binary log keeps."""
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
      self.writeLog(session.domain, [("query", loggedQuery(statement))])
      return
    rows = rowEvents(before, self.tables)
    if not rows:
      return
    if session.snapshot is None:
      self.writeLog(session.domain, [("annotate", statement)] + rows)
    else:
      session.pending.extend([("annotate", statement)] + rows)

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
      for tokens in parseValueRows(statement, insert.start(3)):
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
      self.writeLog(domain, [("query", loggedQuery(statement))])
      return
    rows = rowEvents(before, self.tables)
    if rows:
      self.writeLog(domain, [("annotate", statement)] + rows)

  def renderBinaryLog(self, startPosition):
    """mysqlbinlog's output from startPosition to the end, as MariaDB 10.5.4's prints it: the rotation to the file and its
    format description, then each transaction, printing a session variable only when it changes."""
    lines = ["/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=1*/;", "/*!40019 SET @@session.max_insert_delayed_threads=0*/;",
      "/*!50003 SET @OLD_COMPLETION_TYPE=@@COMPLETION_TYPE,COMPLETION_TYPE=0*/;", "DELIMITER /*!*/;",
      f"# at {startPosition}", f"#691231 18:00:00 server id 1  end_log_pos 0 \tRotate to {binaryLogFile}  pos: {startPosition}",
      f"# at {startPosition}", f"#261011 08:00:00 server id 1  end_log_pos 0 CRC32 0x0000abcd \tStart: binlog v 4, server v "
      f"{serverVersion} created 261011 08:00:00"]
    printed = {}
    for transaction in self.log:
      if transaction.start < startPosition:
        continue
      lines += renderTransaction(transaction, printed)
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
  """A value as the mysql client prints it in batch mode: text in the connection's utf8mb4 and binary as its bytes (carried
  in surrogate escapes), with NUL, tab, newline and backslash escaped."""
  if value is None:
    return "NULL"
  data = stringBytes(value)
  if isinstance(value, (Text, Member)) and value.charset != "binary":
    data = convert(data, value.charset, "utf8mb4")
  data = data.replace(b"\\", b"\\\\").replace(b"\0", b"\\0").replace(b"\t", b"\\t").replace(b"\n", b"\\n")
  return data.decode("utf-8", "surrogateescape")


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


clientCommandTakesArgument = {"?": True, "clear": False, "connect": True, "delimiter": True, "edit": False, "ego": False,
  "exit": False, "go": False, "help": True, "nopager": False, "notee": False, "pager": True, "print": False, "prompt": True,
  "quit": False, "rehash": False, "source": True, "status": False, "system": True, "tee": True, "use": True, "charset": True,
  "warnings": False, "nowarning": False}


def clientCommand(text, delimiter):
  """The mysql client command text is, as its name and argument, or None when text is SQL (the client's find_command)."""
  text = text.lstrip()
  if delimiter in text and text[:9].lower() != "delimiter":
    return None
  word, argument = re.match(r"([^ \t]*)[ \t]*(.*)$", text, re.DOTALL).groups()
  takesArgument = clientCommandTakesArgument.get(word.lower())
  if takesArgument is None or argument and not takesArgument:
    return None
  return word.lower(), argument


def clientStatements(script, keepComments):
  """(line, query) for each query MariaDB 10.5's mysql client sends for script in batch mode, as its read_and_execute and
  add_line read it: a query ends at the delimiter outside quotes and comments, and a DELIMITER line changes it. With
  --comments (keepComments) the client keeps comments and the whitespace after a delimiter, sends a comment that starts a
  line's query as a query of its own, and lets a line of spaces start a query; without, it drops them. An error names the
  line the client's buffer last started filling on, so a query that follows another on the same line, or a block comment
  after a delimiter, names that earlier line. A last query without a delimiter is sent at the end."""
  delimiter = ";"
  queries = []
  buffer = ""
  inString = None
  inComment = False
  startLine = 0

  def send(text):
    text = text.rstrip()
    if not text:
      return
    if clientCommand(text, delimiter):
      raise AssertionError(f"the stand-in does not emulate the client command {text!r}")
    queries.append((startLine, text))

  lines = script.split("\n")
  if lines[-1] == "":
    lines.pop()
  for number, line in enumerate(lines, start=1):
    if not buffer:
      startLine = number
    if not buffer and not inString and not inComment:
      command = clientCommand(line, delimiter)
      if command:
        name, argument = command
        if name != "delimiter" or not argument or argument[0] in "'\"`":
          raise AssertionError(f"the stand-in does not emulate the client command {line!r}")
        delimiter = argument.split()[0]
        continue
    if not line and not buffer:
      continue
    out = ""
    index = 0
    needSpace = False
    inVersioned = False
    while index < len(line):
      character = line[index]
      if not keepComments and character.isspace() and not out and not buffer:
        index += 1
      elif not inComment and character == "\\" and inString != "`":
        if index + 1 == len(line):
          break
        if not inString and line[index + 1] != "N":
          raise AssertionError("the stand-in does not emulate the client command \\" + line[index + 1])
        out += line[index:index + 2]
        index += 2
      elif not inComment and not inString and line.startswith(delimiter, index):
        index += len(delimiter)
        while keepComments and index < len(line) and line[index].isspace():
          out += line[index]
          index += 1
        rest = line[index:]
        if keepComments and (rest.startswith("#") or rest.startswith("--") and rest[2:3].isspace()):
          out += rest
          index = len(line)
        send(buffer + out)
        buffer = out = ""
      elif not inComment and not inString and (character == "#" or line.startswith("--", index) and (
          index + 2 == len(line) or line[index + 2].isspace() or not buffer and not out)):
        if not keepComments:
          buffer += out
        elif buffer or out:
          buffer += out + line[index:]
        else:
          send(line[index:])
        out = ""
        break
      elif not inString and line.startswith("/*", index) and not line.startswith(("/*!", "/*M!"), index):
        buffer += out + ("/*" if keepComments else "")
        out = ""
        index += 2
        inComment = True
      elif inComment and not inVersioned and line.startswith("*/", index):
        buffer += out + ("*/" if keepComments else "")
        out = ""
        index += 2
        inComment = False
        needSpace = True
      else:
        if not inString and line.startswith("/*!", index):
          inVersioned = True
        elif not inString and inVersioned and line.startswith("*/", index):
          inVersioned = False
        if inString and character == inString:
          inString = None
        elif not inComment and not inString and character in "'\"`":
          inString = character
        if not inComment or keepComments:
          if needSpace and not character.isspace():
            out += " "
          needSpace = False
          out += character
        index += 1
    if out or buffer:
      if not out.lower().startswith("delimiter") or inString or inComment:
        out += "\n"
      if not inComment or keepComments:
        buffer += out
  send(buffer)
  return queries


def removeComments(statement):
  """The statement as the server reads it: each plain comment (block, -- or #) is whitespace; a versioned one is kept."""
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
      index = statement.index("*/", index + 2) + 2
      while index < len(statement) and statement[index].isspace():
        index += 1
      if result and not result[-1].isspace():
        result.append(" ")
      continue
    elif character == "#" or statement.startswith("--", index) and statement[index + 2:index + 3] in ("", " ", "\t", "\n",
        "\r"):
      end = statement.find("\n", index)
      index = len(statement) if end == -1 else end
      result.append(" ")
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


def keptComment(statement, characters):
  """The comment MariaDB keeps when it rebuilds a DROP TABLE for the binary log (its comment_length): a block comment that
  starts right after the first characters non-space characters of the statement as received, or empty."""
  index = 0
  while index < len(statement):
    if not statement[index].isspace():
      if characters == 0:
        break
      characters -= 1
    index += 1
  end = statement.find("*/", index + 2)
  if not statement.startswith("/*", index) or statement.startswith("/*!", index) or end == -1:
    return ""
  return statement[index:end + 2]


def loggedQuery(statement):
  """A DDL statement as MariaDB 10.5.4 writes it to the binary log: a DROP TABLE rebuilt by the server from its table names,
  keeping only a comment right after DROP TABLE [IF EXISTS]; any other statement as received."""
  drop = re.match(r"^DROP\s+TABLE\s+(IF\s+EXISTS\s+)?(.*)$", removeComments(statement), re.IGNORECASE | re.DOTALL)
  if not drop:
    return statement
  comment = keptComment(statement, 17 if drop.group(1) else 9)
  names = ",".join(f"`{name.strip().strip('`')}`" for name in drop.group(2).split(","))
  return (f"DROP TABLE {'IF EXISTS ' if drop.group(1) else ''}{comment + ' ' if comment else ''}{names} "
    "/* generated by server */")


firstQueryPreamble = ["use `peq`/*!*/;", "SET TIMESTAMP=1791626760/*!*/;", "SET @@session.pseudo_thread_id=12/*!*/;",
  "SET @@session.foreign_key_checks=1, @@session.sql_auto_is_null=0, @@session.unique_checks=1, @@session.autocommit=1, "
  "@@session.check_constraint_checks=1, @@session.sql_if_exists=0/*!*/;", "SET @@session.sql_mode=1411907584/*!*/;",
  "SET @@session.auto_increment_increment=1, @@session.auto_increment_offset=1/*!*/;", "/*!\\C utf8mb4 *//*!*/;",
  "SET @@session.character_set_client=45,@@session.collation_connection=45,@@session.collation_server=45/*!*/;",
  "SET @@session.lc_time_names=0/*!*/;", "SET @@session.collation_database=DEFAULT/*!*/;"]


def renderTransaction(transaction, printed):
  """A transaction as mysqlbinlog 10.5.4 prints it, given the session state it printed before (updated): its GTID event
  with the variables that changed, then BEGIN, each statement's Annotate_rows printed with its first table map (cut short by
  one character for each newline the statement holds, as 10.5.4's mysqlbinlog cuts it), its rows, and Xid; or a DDL
  statement's Query event."""
  stamp = "261011 10:00:00"
  position = transaction.start
  isQuery = transaction.events[0][0] == "query"
  lines = [f"# at {position}", f"#{stamp} server id 1  end_log_pos {position + 42} CRC32 0x0000abcd \tGTID "
    f"{transaction.domain}-1-{transaction.sequence} {'ddl' if isQuery else 'trans'}"]
  if not printed:
    lines.append("/*!100101 SET @@session.skip_parallel_replication=0*//*!*/;")
  if printed.get("domain") != transaction.domain:
    lines.append(f"/*!100001 SET @@session.gtid_domain_id={transaction.domain}*//*!*/;")
  if not printed:
    lines.append("/*!100001 SET @@session.server_id=1*//*!*/;")
  printed["domain"] = transaction.domain
  lines.append(f"/*!100001 SET @@session.gtid_seq_no={transaction.sequence}*//*!*/;")
  if not isQuery:
    lines += ["BEGIN", "/*!*/;"]
  position += 42
  tableNumber = 30
  annotate = None
  for event in transaction.events:
    if event[0] == "query":
      lines += [f"# at {position}", f"#{stamp} server id 1  end_log_pos {position + 90} CRC32 0x0000abcd \tQuery\t"
        "thread_id=12\texec_time=0\terror_code=0"]
      lines += ["SET TIMESTAMP=1791626760/*!*/;"] if printed.get("query") else firstQueryPreamble
      printed["query"] = True
      lines += event[1].split("\n") + ["/*!*/;"]
      position += 90
    elif event[0] == "annotate":
      lines.append(f"# at {position}")
      annotate = (position + 60, event[1][:len(event[1]) - event[1].count("\n")])
      position += 60
    else:
      _, name, operation, count = event
      tableNumber += 1
      lines.append(f"# at {position}")
      if annotate:
        lines.append(f"#{stamp} server id 1  end_log_pos {annotate[0]} CRC32 0x0000abcd \tAnnotate_rows:")
        lines += [f"#Q> {line}" for line in annotate[1].split("\n")]
        annotate = None
      eventName = {"INSERT": "Write_rows", "UPDATE": "Update_rows", "DELETE": "Delete_rows"}[operation]
      statementName = {"INSERT": "INSERT INTO", "UPDATE": "UPDATE", "DELETE": "DELETE FROM"}[operation]
      lines += [f"#{stamp} server id 1  end_log_pos {position + 40} CRC32 0x0000abcd \tTable_map: `peq`.`{name}` mapped to "
        f"number {tableNumber}", f"# at {position + 40}", f"#{stamp} server id 1  end_log_pos {position + 80} CRC32 "
        f"0x0000abcd \t{eventName}: table id {tableNumber} flags: STMT_END_F"]
      for _ in range(count):
        lines.append(f"### {statementName} `peq`.`{name}`")
        lines += ["### WHERE", "###   @1=1"] if operation != "INSERT" else []
        lines += ["### SET", "###   @1=1"] if operation != "DELETE" else []
      lines.append(f"# Number of rows: {count}")
      position += 80
  if not isQuery:
    lines += [f"# at {position}", f"#{stamp} server id 1  end_log_pos {position + 31} CRC32 0x0000abcd \tXid = 120",
      "COMMIT/*!*/;"]
  return lines


literalPattern = re.compile(r"\s*(?:(NULL)|('(?:[^'\\]|\\.|'')*')|_(\w+) X'([0-9A-Fa-f]*)'|X'([0-9A-Fa-f]*)'|"
  r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?))\s*")


def syntaxError(statement, position):
  """MariaDB's error for a statement it cannot read at position: the rest of it from there, cut to 80 characters, and the
  line of the statement that is on."""
  return FakeSQLError(1064, "42000", "You have an error in your SQL syntax; check the manual that corresponds to your MariaDB "
    f"server version for the right syntax to use near '{statement[position:position + 80]}' at line "
    f"{statement.count(chr(10), 0, position) + 1}")


def parseValueRows(text, position):
  """The rows of the VALUES list that starts at position in the INSERT text as (kind, payload, charset) tokens."""
  rows = []
  while True:
    opening = re.compile(r"\s*\(").match(text, position)
    if not opening:
      raise syntaxError(text, position)
    position = opening.end()
    values = []
    while True:
      match = literalPattern.match(text, position)
      if not match:
        raise syntaxError(text, position)
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
        raise syntaxError(text, position)
      position += 1
      break
    rows.append(values)
    following = re.compile(r"\s*").match(text, position).end()
    if following == len(text):
      return rows
    if not text.startswith(",", following):
      raise syntaxError(text, following)
    position = following + 1


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
    value = Decimal(payload).quantize(Decimal(1).scaleb(-column.scale), ROUND_HALF_UP)
    return value.copy_abs() if value.is_zero() else value
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
    return temporalText(column, text)
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
