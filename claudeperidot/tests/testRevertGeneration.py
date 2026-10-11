"""revert.sql is written from the rows saved at stage. Each test saves rows through the ledger's own queries, changes them as
an apply would (updates, inserts, deletes), then runs the generated revert.sql through FakeMariaDB, which parses every
literal and stores what MariaDB would store for it, and compares the table with the rows before."""
import copy

import pytest

import ledger
from ledgerDoubles import FakeMariaDB, FakeTable, makeHarness, rowChangeFields, rowsOf, writeChange

payloadColumns = [("id", "int", ""), ("payload", "blob", ""), ("label", "varchar", "utf8mb4"), ("note", "text", "latin1"),
  ("ratio", "float", ""), ("amount", "decimal", ""), ("created", "datetime", ""), ("flags", "set", "latin1"),
  ("big", "bigint", "")]


def payloadRow(identifier, payload, label, note, ratio, amount, created, flags, big):
  return {"id": identifier, "payload": payload, "label": label, "note": note, "ratio": ratio, "amount": amount,
    "created": created, "flags": flags, "big": big}


def payloadFake():
  fake = FakeMariaDB()
  fake.tables["payloads"] = FakeTable(payloadColumns, ("id",), [
    payloadRow(b"1", b"\x00'\\\xff;--", "naive \u2603 caf\u00e9".encode("utf-8"), b"caf\xe9", b"0.1", b"-12.50",
      b"2026-10-11 09:30:00", b"a,b", b"-9223372036854775808"),
    payloadRow(b"2", b"", "".encode("utf-8"), None, b"-1.5e-7", b"0.00", b"0000-00-00 00:00:00", b"", b"0"),
    payloadRow(b"3", None, b"NULL", b"O'Brien; DROP TABLE x; --", None, None, None, None, None),
    payloadRow(b"4", b"plain", b"tab\there", b"line\nbreak", b"3e38", b"1", b"2000-01-01 00:00:00", b"c", b"18446744073709551615"),
  ])
  fake.predicates["id < 10"] = lambda row: int(row["id"]) < 10
  return fake


def savedScope(repository, fake, where="id < 10"):
  harness = makeHarness(repository, fake)
  change = ledger.loadChange(writeChange(repository, "0001-payloads", rowChangeFields(scope=[{"table": "payloads",
    "where": where}])))
  return harness.ledger.fetchScope(change)


def runRevert(fake, tables):
  script = "START TRANSACTION;\n" + "".join(f"{statement};\n" for statement in ledger.splitStatements(
    ledger.revertScript(tables))) + "COMMIT;\n"
  fake.runScript(script)


def test_theLedgerWritesTheSimplestExactLiteralForEachValue(repository):
  tables = savedScope(repository, payloadFake())
  revert = ledger.revertScript(tables)
  assert "DELETE FROM `payloads` WHERE (id < 10);" in revert
  assert ("(1, X'00275CFF3B2D2D', _utf8mb4 X'6E6169766520E2988320636166C3A9', _latin1 X'636166E9', 0.1, -12.50, "
    "'2026-10-11 09:30:00', 'a,b', -9223372036854775808)") in revert
  assert "(2, X'', '', NULL, -1.5e-7, 0.00, '0000-00-00 00:00:00', '', 0)" in revert
  assert "(3, NULL, 'NULL', 'O''Brien; DROP TABLE x; --', NULL, NULL, NULL, NULL, NULL)" in revert
  assert "(4, X'706C61696E', _utf8mb4 X'7461620968657265', _latin1 X'6C696E650A627265616B', 3e38, 1, '2000-01-01 00:00:00', 'c', 18446744073709551615)" in revert


@pytest.mark.parametrize("change", ["update", "insert", "delete", "everything"])
def test_revertPutsBackExactlyTheSavedRowsAfterAnApply(repository, change):
  fake = payloadFake()
  before = rowsOf(fake, "payloads")
  tables = savedScope(repository, fake)
  rows = fake.tables["payloads"].rows
  if change in ("update", "everything"):
    rows[0]["payload"] = b"changed"
    rows[1]["note"] = b"was NULL"
    rows[2]["label"] = None
  if change in ("insert", "everything"):
    fake.tables["payloads"].insert(payloadRow(b"9", b"new", b"new", None, None, None, None, None, None))
  if change in ("delete", "everything"):
    del rows[3]
  assert rowsOf(fake, "payloads") != before
  runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == before


def test_revertOfAScopeThatWasEmptyDeletesWhatApplyInserted(repository):
  fake = payloadFake()
  fake.predicates["id > 100"] = lambda row: int(row["id"]) > 100
  before = rowsOf(fake, "payloads")
  tables = savedScope(repository, fake, "id > 100")
  assert tables[0]["rows"] == []
  assert ledger.splitStatements(ledger.revertScript(tables)) == ["DELETE FROM `payloads` WHERE (id > 100)"]
  fake.tables["payloads"].insert(payloadRow(b"101", b"x", b"y", None, None, None, None, None, None))
  runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == before


def test_revertLeavesRowsOutsideTheScopeAlone(repository):
  fake = payloadFake()
  fake.predicates["id = 1"] = lambda row: row["id"] == b"1"
  tables = savedScope(repository, fake, "id = 1")
  fake.tables["payloads"].rows[0]["note"] = b"changed in scope"
  fake.tables["payloads"].rows[1]["note"] = b"changed outside the scope"
  expected = copy.deepcopy(fake.tables["payloads"].rows)
  expected[0]["note"] = b"caf\xe9"
  runRevert(fake, tables)
  assert sorted(map(repr, fake.tables["payloads"].rows)) == sorted(map(repr, expected))


def test_aRevertThatCollidesWithARowApplyMovedOutOfTheScopeRollsBackWhole(repository):
  fake = payloadFake()
  fake.predicates["id = 1"] = lambda row: row["id"] == b"1"
  fake.tables["payloads"].primaryKey = ("label",)
  tables = savedScope(repository, fake, "id = 1")
  fake.tables["payloads"].rows[0]["id"] = b"50"
  moved = rowsOf(fake, "payloads")
  with pytest.raises(Exception, match="Duplicate entry"):
    runRevert(fake, tables)
  assert rowsOf(fake, "payloads") == moved


def test_eachTableInTheScopeGetsItsDeleteAndInsertInScopeOrder(repository):
  fake = payloadFake()
  fake.tables["notes"] = FakeTable([("id", "int", ""), ("text", "varchar", "latin1")], ("id",), [{"id": b"1", "text": b"a;b"}])
  fake.predicates["id = 1"] = lambda row: row["id"] == b"1"
  harness = makeHarness(repository, fake)
  change = ledger.loadChange(writeChange(repository, "0001-two", rowChangeFields(scope=[{"table": "notes", "where": "id = 1"},
    {"table": "payloads", "where": "id = 1"}])))
  statements = ledger.splitStatements(ledger.revertScript(harness.ledger.fetchScope(change)))
  assert len(statements) == 4
  assert statements[0] == "DELETE FROM `notes` WHERE (id = 1)"
  assert statements[1] == "INSERT INTO `notes` (`id`, `text`) VALUES\n  (1, 'a;b')"
  assert statements[2] == "DELETE FROM `payloads` WHERE (id = 1)"
  assert statements[3].startswith("INSERT INTO `payloads` (`id`, `payload`, `label`, `note`, `ratio`, `amount`, `created`, "
    "`flags`, `big`) VALUES\n  (1, X'00275CFF3B2D2D', ")


def test_aColumnTypeTheLedgerCannotSaveExactlyRefusesStage(repository):
  fake = payloadFake()
  fake.tables["payloads"].columns.append(("shape", "geometry", ""))
  for row in fake.tables["payloads"].rows:
    row["shape"] = None
  with pytest.raises(ledger.LedgerError, match="column shape has type geometry, which the ledger cannot save exactly"):
    savedScope(repository, fake)


@pytest.mark.parametrize("column, value, problem", [
  (ledger.Column("id", "int", ""), b"12a", "holds '12a', not a number"),
  (ledger.Column("created", "datetime", ""), b"2026-01-01'", "not a date or time"),
  (ledger.Column("ratio", "double", ""), b"\xe9", "holds non-ASCII text"),
  (ledger.Column("name", "varchar", ""), b"x", "text column name has no character set"),
])
def test_aValueThatIsNotWhatItsColumnHoldsIsRefused(column, value, problem):
  with pytest.raises(ledger.LedgerError, match=problem):
    ledger.literal(column, "V" + value.hex().upper())
