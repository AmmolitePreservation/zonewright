import hashlib

import pytest

import ledger
from ledgerDoubles import FakeMariaDB, FakeTable, makeHarness


def test_aTableDigestIsSHA256OfTheSortedRowsJoinedAsTheServerJoinsThem():
  rows = [["V32", "N"], ["V31", "V"]]
  assert ledger.tableDigest(rows) == hashlib.sha256(b"V31,V|V32,N").hexdigest()
  assert ledger.tableDigest(list(reversed(rows))) == ledger.tableDigest(rows)
  assert ledger.tableDigest([]) is None


def test_nullEmptyTextAndTheWordNULLHaveDifferentDigests():
  digests = {ledger.tableDigest([["V31", value]]) for value in ("N", "V", "V" + b"NULL".hex().upper())}
  assert len(digests) == 3


def test_theScopeChecksumFollowsEveryValueAndColumnName():
  table = {"table": "t", "columns": [{"name": "a"}, {"name": "b"}], "rows": [["V31", "N"], ["V32", "V33"]]}
  base = ledger.scopeChecksum([table])
  assert ledger.scopeChecksum([dict(table, rows=list(reversed(table["rows"])))]) == base
  assert ledger.scopeChecksum([dict(table, rows=[["V31", "N"], ["V32", "V34"]])]) != base
  assert ledger.scopeChecksum([dict(table, columns=[{"name": "a"}, {"name": "c"}])]) != base
  assert ledger.scopeChecksum([dict(table, rows=[])]) != base
  expected = hashlib.sha256(("t\ta,b\t" + hashlib.sha256(b"V31,N|V32,V33").hexdigest()).encode()).hexdigest()
  assert base == expected


def test_digestConditionComparesTheServersDigestWithTheSavedOne():
  condition = ledger.digestCondition("t", "id = 1", [ledger.Column("id", "int", ""), ledger.Column("b", "blob", "")], None)
  assert condition == ("(SELECT SHA2(GROUP_CONCAT(ledgerRow ORDER BY BINARY ledgerRow SEPARATOR '|'), 256) FROM (SELECT "
    "CONCAT_WS(',', IF(`id` IS NULL, 'N', CONCAT('V', HEX(CAST(`id` AS CHAR)))), IF(`b` IS NULL, 'N', CONCAT('V', HEX(`b`)))) "
    "AS ledgerRow FROM `t` WHERE (id = 1)) AS ledgerRows) <=> NULL")


def test_tableChecksumsAreReadFromCHECKSUMTABLE(repository):
  fake = FakeMariaDB()
  fake.tables["a"] = FakeTable([("id", "int", "")], ("id",), [{"id": b"1"}])
  fake.tables["b"] = FakeTable([("id", "int", "")], ("id",), [])
  harness = makeHarness(repository, fake)
  assert harness.ledger.tableChecksums(["a", "b"]) == {"a": fake.tableChecksum("a"), "b": fake.tableChecksum("b")}
  assert harness.fake.sqlScripts[-1].endswith("CHECKSUM TABLE `a`, `b` EXTENDED;\n")
  with pytest.raises(ledger.LedgerError, match=r"CHECKSUM TABLE gave \['peq.missing', 'NULL'\]"):
    harness.ledger.tableChecksums(["missing"])
