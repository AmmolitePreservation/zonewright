import re

import pytest

import ledger
from ledgerDoubles import makeHarness, rowChangeFields, writeChange


def test_semicolonsInsideQuotesNamesAndCommentsDoNotSplitStatements():
  text = ("UPDATE t SET a = 'x;y', b = \"p;q\" WHERE `odd;name` = 1; -- a comment; with a semicolon\n"
    "INSERT INTO t (a) VALUES ('it''s; fine'), ('back\\'slash;');\n"
    "/* block; comment */ DELETE FROM t WHERE a = '#not a comment';\n"
    "# a whole-line comment;\n")
  assert ledger.splitStatements(text) == [
    "UPDATE t SET a = 'x;y', b = \"p;q\" WHERE `odd;name` = 1",
    "INSERT INTO t (a) VALUES ('it''s; fine'), ('back\\'slash;')",
    "DELETE FROM t WHERE a = '#not a comment'",
  ]


def test_commentsAreStrippedSoAStatementCanBeEmbeddedOrReterminated():
  assert ledger.splitStatements("SELECT 1 AS applied -- why it is 1\n") == ["SELECT 1 AS applied"]
  assert ledger.splitStatements("SELECT /* inline */ 2 AS applied;") == ["SELECT   2 AS applied"]
  assert ledger.splitStatements("-- only a comment\n;;\n") == []


def test_aDoubleDashWithoutASpaceIsNotAComment():
  assert ledger.splitStatements("UPDATE t SET a = a--1;") == ["UPDATE t SET a = a--1"]


def test_firstKeywordSkipsLeadingComments():
  assert ledger.firstKeyword("/* note */ -- more\n  update t set a = 1") == "UPDATE"
  assert ledger.firstKeyword("'quoted'") == ""


@pytest.mark.parametrize("text, problem", [
  ("DELIMITER //\nSELECT 1//", "DELIMITER is not supported"),
  ("CREATE /*!50001 ALGORITHM=UNDEFINED */ VIEW v AS SELECT 1;", "MariaDB runs a versioned comment as code"),
  ("SELECT 1 /*M!100500 , 2 */ AS applied;", "MariaDB runs a versioned comment as code"),
  ("SELECT 'never closed", "an SQL quoted string or name is never closed"),
  ("SELECT 1 /* never closed", "an SQL comment is never closed"),
])
def test_scriptsTheLedgerCannotSplitSafelyAreRefused(text, problem):
  with pytest.raises(ledger.LedgerError, match=problem):
    ledger.splitStatements(text)


def test_aRowChangeHoldsOnlyRowStatementsAndASchemaChangeOneStatementPerStep(repository):
  harness = makeHarness(repository)
  rows = ledger.loadChange(writeChange(repository, "0001-rows", rowChangeFields(), {
    "apply.sql": "UPDATE rule_values SET rule_value = '1';\nALTER TABLE rule_values ADD COLUMN x INT;\n"}))
  with pytest.raises(ledger.LedgerError, match="apply.sql holds only INSERT, UPDATE, DELETE and REPLACE; it also has: ALTER"):
    harness.ledger.applyStatements(rows)
  schema = ledger.loadChange(writeChange(repository, "0002-schema", rowChangeFields(scope=[{"table": "items"}], schemaSteps=2),
    {"apply.sql": "ALTER TABLE items ADD COLUMN a INT;\n", "revert.sql": "ALTER TABLE items DROP COLUMN a;\n"}))
  with pytest.raises(ledger.LedgerError, match="schemaSteps 2 but apply.sql holds 1 statements"):
    harness.ledger.applyStatements(schema)
  with pytest.raises(ledger.LedgerError, match="revert.sql must hold 2 statements"):
    harness.ledger.revertStatements(schema)


@pytest.mark.parametrize("statement, problem", [
  ("CREATE VIEW v AS SELECT 1", "MariaDB writes CREATE VIEW to the binary log rebuilt with its definer"),
  ("CREATE OR REPLACE ALGORITHM=MERGE VIEW v AS SELECT 1", "MariaDB writes CREATE VIEW"),
  ("ALTER VIEW v AS SELECT 2", "MariaDB writes ALTER VIEW"),
  ("CREATE DEFINER=`root`@`localhost` TRIGGER t BEFORE INSERT ON items FOR EACH ROW SET NEW.a = 1", "MariaDB writes CREATE "
    "TRIGGER"),
  ("CREATE PROCEDURE p() SELECT 1", "MariaDB writes CREATE PROCEDURE"),
  ("CREATE FUNCTION f() RETURNS INT DETERMINISTIC RETURN 1", "MariaDB writes CREATE FUNCTION"),
  ("CREATE EVENT e ON SCHEDULE EVERY 1 DAY DO DELETE FROM items", "MariaDB writes CREATE EVENT"),
  ("CREATE TABLE copy AS SELECT * FROM items", "MariaDB writes CREATE TABLE ... SELECT to the binary log as a CREATE TABLE"),
  ("CREATE TABLE copy (id INT PRIMARY KEY) SELECT id FROM items", "MariaDB writes CREATE TABLE ... SELECT"),
  ("DROP TABLES items", "MariaDB keeps the tag of a DROP TABLE only when it is written DROP TABLE [IF EXISTS] name"),
  ("DROP TEMPORARY TABLE items", "MariaDB keeps the tag of a DROP TABLE only when"),
])
def test_aSchemaStepMariaDBWritesToTheBinaryLogWithoutItsTagIsRefused(repository, statement, problem):
  harness = makeHarness(repository)
  change = ledger.loadChange(writeChange(repository, "0001-schema", rowChangeFields(scope=[{"table": "items"}], schemaSteps=1),
    {"apply.sql": f"{statement};\n", "revert.sql": f"{statement};\n"}))
  for name, read in (("apply.sql", harness.ledger.applyStatements), ("revert.sql", harness.ledger.revertStatements)):
    with pytest.raises(ledger.LedgerError, match=re.escape(f"0001-schema/{name}: {problem}")) as raised:
      read(change)
    assert str(raised.value).endswith(f", so drift could not tell the ledger made it: {statement}")


def test_schemaStepsMariaDBWritesToTheBinaryLogWithTheirTagAreAccepted(repository):
  harness = makeHarness(repository)
  statements = ["ALTER TABLE items ADD COLUMN `view` INT", "ALTER TABLE items ADD COLUMN trigger_count INT",
    "CREATE TABLE extra (id INT) COMMENT 'select'", "CREATE UNIQUE INDEX byName ON items (name)", "DROP TABLE IF EXISTS extra",
    "CREATE TABLE copy LIKE items", "DROP VIEW v", "RENAME TABLE copy TO copied"]
  change = ledger.loadChange(writeChange(repository, "0001-schema", rowChangeFields(scope=[{"table": "items"}],
    schemaSteps=len(statements)), {"apply.sql": "".join(f"{statement};\n" for statement in statements)}))
  assert harness.ledger.applyStatements(change) == statements


@pytest.mark.parametrize("check", ["SELECT 1 AS applied; SELECT 2 AS applied;", "UPDATE t SET a = 1;", ""])
def test_checkSQLMustBeOneSelect(repository, check):
  harness = makeHarness(repository)
  change = ledger.loadChange(writeChange(repository, "0001-rows", rowChangeFields(), {"check.sql": check}))
  with pytest.raises(ledger.LedgerError, match="check.sql must be one SELECT"):
    harness.ledger.checkStatement(change)


def test_batchOutputIsUnescaped():
  assert ledger.unescapeBatchField(r"CREATE TABLE `t` (\n\t`a` int\n) COMMENT 'x\\y\0'") == \
    "CREATE TABLE `t` (\n\t`a` int\n) COMMENT 'x\\y\0'"
