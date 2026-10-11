"""revert.sql is written from the rows saved at stage. Each test saves rows through the ledger's own queries, which the
MariaDB stand-in evaluates per column with MariaDB's semantics, changes them as an apply would, then runs the generated
revert.sql through the stand-in, which parses every literal and stores what MariaDB would store for it, and compares the
table with the rows before."""
import copy
import struct
from decimal import Decimal

import pytest

import ledger
from ledgerDoubles import FakeColumn, FakeMariaDB, FakeTable, makeHarness, rowChangeFields, rowsOf, writeChange

payloadColumns = [FakeColumn("id", "int"), FakeColumn("payload", "blob"), FakeColumn("label", "varchar", "utf8mb4"),
  FakeColumn("note", "text", "latin1"), FakeColumn("ratio", "float"), FakeColumn("precise", "double"),
  FakeColumn("amount", "decimal", scale=2), FakeColumn("created", "datetime", fractionalDigits=6),
  FakeColumn("flags", "set", "latin1", ("a", "b", "c")), FakeColumn("mood", "enum", "latin1", ("calm", "angry", "sad")),
  FakeColumn("mask", "bit", width=12), FakeColumn("big", "bigint"), FakeColumn("path", "varchar", "latin1")]


def single(value):
  return struct.unpack("<f", struct.pack("<f", value))[0]


def payloadRow(identifier, payload, label, note, ratio, precise, amount, created, flags, mood, mask, big, path):
  return {"id": identifier, "payload": payload, "label": label, "note": note, "ratio": ratio, "precise": precise,
    "amount": amount, "created": created, "flags": flags, "mood": mood, "mask": mask, "big": big, "path": path}


def payloadFake():
  fake = FakeMariaDB()
  fake.tables["payloads"] = FakeTable(payloadColumns, ("id",), [
    payloadRow(1, b"\x00'\\\xff;--", "naive \u2603 caf\u00e9".encode("utf-8"), b"caf\xe9", single(0.123456789),
      0.30000000000000004, Decimal("-12.50"), "2026-10-11 09:30:17.000000", b"a,b", b"angry", 0b101000000101,
      -9223372036854775808, b"C:\\Games\\EverQuest"),
    payloadRow(2, b"", "".encode("utf-8"), None, single(-1.5e-7), 1e-300, Decimal("0.00"), "2026-10-11 09:30:00.250000", b"",
      b"calm", 0, 0, b"plain"),
    payloadRow(3, None, b"NULL", b"O'Brien; DROP TABLE x; --", None, None, None, None, None, None, None, None, None),
    payloadRow(4, b"plain", b"tab\there", b"line\nbreak", single(3e38), -0.0, Decimal("1.00"), "2000-01-01 00:00:59.000000", b"c",
      b"sad", 4095, 18446744073709551615, b"back\\slash"),
    payloadRow(5, b"x", b"y", b"z", -0.0, 123456789.12345679, Decimal("99.99"), "1999-12-31 23:59:59.999999", b"a,c", b"calm",
      1, 7, b"\\"),
  ])
  fake.predicates["id < 10"] = lambda row: row["id"] < 10
  return fake


def savedScope(repository, fake, where="id < 10"):
  harness = makeHarness(repository, fake)
  change = ledger.loadChange(writeChange(repository, "0001-payloads", rowChangeFields(scope=[{"table": "payloads",
    "where": where}])))
  return harness.ledger.fetchScope(change)


def runRevert(fake, tables):
  ledger.Server(fake).sql("START TRANSACTION;\n" + "".join(f"{statement};\n" for statement in ledger.splitStatements(
    ledger.revertScript(tables))) + "COMMIT;\n", writes=True)


def test_theLedgerWritesTheSimplestExactLiteralForEachValue(repository):
  revert = ledger.revertScript(savedScope(repository, payloadFake()))
  assert "DELETE FROM `payloads` WHERE (id < 10);" in revert
  assert ("(1, X'00275CFF3B2D2D', _utf8mb4 X'6E6169766520E2988320636166C3A9', _latin1 X'636166E9', 0.12345679104328156e0, "
    "0.30000000000000004e0, -12.50, '2026-10-11 09:30:17.000000', 'a,b', 'angry', 2565, -9223372036854775808, "
    "_latin1 X'433A5C47616D65735C457665725175657374')") in revert
  assert "(3, NULL, 'NULL', 'O''Brien; DROP TABLE x; --', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)" in revert


@pytest.mark.parametrize("change", ["update", "insert", "delete", "everything"])
def test_revertPutsBackExactlyTheSavedRowsAfterAnApply(repository, change):
  fake = payloadFake()
  before = rowsOf(fake, "payloads")
  tables = savedScope(repository, fake)
  rows = fake.tables["payloads"].rows
  if change in ("update", "everything"):
    rows[0]["payload"] = b"changed"
    rows[0]["ratio"] = 0.5
    rows[1]["note"] = b"was NULL"
    rows[2]["label"] = None
    rows[3]["mood"] = b"calm"
    rows[3]["mask"] = 3
    rows[4]["ratio"] = 0.0
    rows[4]["precise"] = 0.0
  if change in ("insert", "everything"):
    fake.tables["payloads"].insert(payloadRow(9, b"new", b"new", None, None, None, None, None, None, None, None, None, None))
  if change in ("delete", "everything"):
    del rows[3]
  assert rowsOf(fake, "payloads") != before
  runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == before


@pytest.mark.parametrize("column, values", [
  ("ratio", [single(0.123456789), single(16777217.0), single(-1.5e-7), single(3e38), -0.0]),
  ("precise", [0.30000000000000004, 1e-300, 123456789.12345679, -0.0, 2.0 ** 60 + 1]),
  ("mood", [b"calm", b"angry", b"sad"]),
  ("flags", [b"a,b", b"", b"c"]),
  ("mask", [0, 5, 4095]),
  ("created", ["2026-10-11 09:30:17.000000", "2026-10-11 09:30:00.250000", "1999-12-31 23:59:59.999999"]),
  ("path", [b"C:\\Games", b"\\", b"\\n is not a newline"]),
])
def test_everyValueOfAKindComesBackExactly(repository, column, values):
  fake = payloadFake()
  for row, value in zip(fake.tables["payloads"].rows, values):
    row[column] = value
  before = rowsOf(fake, "payloads")
  tables = savedScope(repository, fake)
  for row in fake.tables["payloads"].rows:
    row[column] = None
  runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == before


def test_generatedColumnsAreNeitherSavedNorWrittenBack(repository):
  fake = payloadFake()
  fake.tables["payloads"].columns.append(FakeColumn("doubled", "bigint", generated="VIRTUAL GENERATED"))
  for row in fake.tables["payloads"].rows:
    row["doubled"] = None
  before = rowsOf(fake, "payloads")
  tables = savedScope(repository, fake)
  assert "doubled" not in [column["name"] for column in tables[0]["columns"]]
  del fake.tables["payloads"].rows[0]
  runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == before


def test_revertOfAScopeThatWasEmptyDeletesWhatApplyInserted(repository):
  fake = payloadFake()
  fake.predicates["id > 100"] = lambda row: row["id"] > 100
  before = rowsOf(fake, "payloads")
  tables = savedScope(repository, fake, "id > 100")
  assert tables[0]["rows"] == []
  assert ledger.splitStatements(ledger.revertScript(tables)) == ["DELETE FROM `payloads` WHERE (id > 100)"]
  fake.tables["payloads"].insert(payloadRow(101, b"x", b"y", None, None, None, None, None, None, None, None, None, None))
  runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == before


def test_revertLeavesRowsOutsideTheScopeAlone(repository):
  fake = payloadFake()
  fake.predicates["id = 1"] = lambda row: row["id"] == 1
  tables = savedScope(repository, fake, "id = 1")
  fake.tables["payloads"].rows[0]["note"] = b"changed in scope"
  fake.tables["payloads"].rows[1]["note"] = b"changed outside the scope"
  expected = copy.deepcopy(fake.tables["payloads"].rows)
  expected[0]["note"] = b"caf\xe9"
  runRevert(fake, tables)
  assert sorted(map(repr, fake.tables["payloads"].rows)) == sorted(map(repr, expected))


def test_aRevertThatCollidesWithARowApplyMovedOutOfTheScopeRollsBackWhole(repository):
  fake = payloadFake()
  fake.predicates["id = 1"] = lambda row: row["id"] == 1
  fake.tables["payloads"].primaryKey = ("label",)
  tables = savedScope(repository, fake, "id = 1")
  fake.tables["payloads"].rows[0]["id"] = 50
  moved = rowsOf(fake, "payloads")
  with pytest.raises(ledger.LedgerError, match="Duplicate entry"):
    runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == moved


def test_eachTableInTheScopeGetsItsDeleteAndInsertInScopeOrder(repository):
  fake = payloadFake()
  fake.tables["notes"] = FakeTable([("id", "int"), ("text", "varchar", "latin1")], ("id",), [{"id": 1, "text": b"a;b"}])
  fake.predicates["id = 1"] = lambda row: row["id"] == 1
  harness = makeHarness(repository, fake)
  change = ledger.loadChange(writeChange(repository, "0001-two", rowChangeFields(scope=[{"table": "notes", "where": "id = 1"},
    {"table": "payloads", "where": "id = 1"}])))
  statements = ledger.splitStatements(ledger.revertScript(harness.ledger.fetchScope(change)))
  assert [statement.split(" (")[0] for statement in statements] == ["DELETE FROM `notes` WHERE", "INSERT INTO `notes`",
    "DELETE FROM `payloads` WHERE", "INSERT INTO `payloads`"]


def test_anEnumHoldingItsInvalidValueIsSavedAsSuchAndRefusedBeforeRevertSQLIsWritten(repository):
  fake = payloadFake()
  fake.tables["payloads"].rows[0]["mood"] = b""
  tables = savedScope(repository, fake)
  assert [row[9] for row in tables[0]["rows"]] == ["E", "V63616C6D", "N", "V736164", "V63616C6D"]
  with pytest.raises(ledger.LedgerError, match=r"column mood holds its ENUM's invalid value \(index 0"):
    ledger.revertScript(tables)


def test_anEnumsEmptyMemberIsAValueLikeAnyOther(repository):
  fake = payloadFake()
  fake.tables["payloads"].column("mood").members = ("", "calm", "angry", "sad")
  fake.tables["payloads"].rows[0]["mood"] = b""
  before = rowsOf(fake, "payloads")
  tables = savedScope(repository, fake)
  assert tables[0]["rows"][0][9] == "V"
  fake.tables["payloads"].rows[0]["mood"] = b"sad"
  runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == before


def test_aColumnTypeTheLedgerCannotSaveExactlyRefusesStage(repository):
  fake = payloadFake()
  fake.tables["payloads"].columns.append(FakeColumn("shape", "geometry"))
  for row in fake.tables["payloads"].rows:
    row["shape"] = None
  with pytest.raises(ledger.LedgerError, match="column shape has type geometry, which the ledger cannot save exactly"):
    savedScope(repository, fake)


@pytest.mark.parametrize("column, value, problem, echo", [
  (ledger.Column("id", "int", ""), b"12a", "holds a value that is not a number", "12a"),
  (ledger.Column("created", "datetime", ""), b"2026-01-01'", "holds a value that is not a date or time", "2026-01-01"),
  (ledger.Column("ratio", "double", ""), b"\xe9", "holds non-ASCII text", "\u00e9"),
])
def test_aValueThatIsNotWhatItsColumnHoldsIsRefusedWithoutEchoingIt(column, value, problem, echo):
  with pytest.raises(ledger.LedgerError, match=problem) as raised:
    ledger.literal(column, "V" + value.hex().upper())
  assert echo not in str(raised.value)


def test_aTextColumnWithoutACharacterSetIsRefused():
  with pytest.raises(ledger.LedgerError, match="text column name has no character set"):
    ledger.literal(ledger.Column("name", "varchar", ""), "V78")
