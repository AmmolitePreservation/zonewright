import hashlib

import pytest

import ledger
from ledgerDoubles import FakeColumn, FakeMariaDB, FakeTable, makeHarness, rowChangeFields, writeChange


def test_aTableDigestIsSHA256OfTheSortedRowsJoinedAsTheServerJoinsThem():
  rows = [["V32", "N"], ["V31", "V"]]
  assert ledger.tableDigest(rows) == hashlib.sha256(b"V31,V|V32,N").hexdigest()
  assert ledger.tableDigest(list(reversed(rows))) == ledger.tableDigest(rows)
  assert ledger.tableDigest([]) is None


def test_nullEmptyTextAndTheWordNULLHaveDifferentDigests():
  digests = {ledger.tableDigest([["V31", value]]) for value in ("N", "V", "V" + b"NULL".hex().upper())}
  assert len(digests) == 3


def test_theScopeChecksumFollowsEveryDigestAndColumnName():
  table = {"table": "t", "columns": [{"name": "a"}, {"name": "b"}], "digest": "1" * 64}
  base = ledger.scopeChecksum([table])
  assert ledger.scopeChecksum([dict(table, digest="2" * 64)]) != base
  assert ledger.scopeChecksum([dict(table, columns=[{"name": "a"}, {"name": "c"}])]) != base
  assert ledger.scopeChecksum([dict(table, digest=None)]) != base
  assert base == hashlib.sha256(("t\ta,b\t" + "1" * 64).encode()).hexdigest()


def digestFake():
  fake = FakeMariaDB()
  fake.tables["mixed"] = FakeTable([FakeColumn("id", "int"), FakeColumn("name", "varchar", "latin1"),
    FakeColumn("ratio", "float"), FakeColumn("kind", "enum", "utf8mb4", ("one", "two")), FakeColumn("blob", "blob")], ("id",), [
    {"id": 1, "name": b"caf\xe9", "ratio": 0.5, "kind": b"two", "blob": b"\x00\xff"},
    {"id": 2, "name": None, "ratio": None, "kind": b"one", "blob": b""},
    {"id": 3, "name": b"other", "ratio": 1.0, "kind": b"one", "blob": None},
  ])
  fake.predicates["id < 3"] = lambda row: row["id"] < 3
  fake.predicates["id > 9"] = lambda row: row["id"] > 9
  return fake


def scopeOf(repository, fake, where):
  harness = makeHarness(repository, fake)
  change = ledger.loadChange(writeChange(repository, "0001-digest", rowChangeFields(scope=[{"table": "mixed", "where": where}])))
  return harness, ledger.describeScope(harness.ledger.fetchScope(change))


def test_theServersDigestOfTheScopeIsTheDigestOfTheRowsItSaved(repository):
  fake = digestFake()
  harness, tables = scopeOf(repository, fake, "id < 3")
  assert tables[0]["rowCount"] == 2
  assert harness.ledger.scopeDigests(tables) == [tables[0]["digest"]]
  fake.tables["mixed"].rows[1]["blob"] = None
  assert harness.ledger.scopeDigests(tables) != [tables[0]["digest"]]


def test_anEmptyScopesDigestIsNullOnTheServerAndInTheLedger(repository):
  harness, tables = scopeOf(repository, digestFake(), "id > 9")
  assert tables[0]["digest"] is None
  assert harness.ledger.scopeDigests(tables) == [None]


def test_aRowOutsideTheScopeLeavesTheScopesDigestAlone(repository):
  fake = digestFake()
  harness, tables = scopeOf(repository, fake, "id < 3")
  fake.tables["mixed"].rows[2]["name"] = b"changed outside"
  assert harness.ledger.scopeDigests(tables) == [tables[0]["digest"]]


def test_tableChecksumsAreReadFromCHECKSUMTABLE(repository):
  fake = FakeMariaDB()
  fake.tables["a"] = FakeTable([("id", "int")], ("id",), [{"id": 1}])
  fake.tables["b"] = FakeTable([("id", "int")], ("id",), [])
  harness = makeHarness(repository, fake)
  assert harness.ledger.tableChecksums(["a", "b"]) == {"a": fake.tableChecksum("a"), "b": fake.tableChecksum("b")}
  with pytest.raises(ledger.LedgerError, match=r"CHECKSUM TABLE gave \['peq.missing', 'NULL'\]"):
    harness.ledger.tableChecksums(["missing"])
