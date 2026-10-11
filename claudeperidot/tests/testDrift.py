import json

import pytest
import yaml

import ledger
from ledgerDoubles import (FakeTable, committedFiles, expansionCheck, expansionUpdate, git, itemsFake, makeHarness, planText,
  rowChangeFields, rowFiles, ruleFake, schemaChangeFields, setRule, writeChange)

# mysqlbinlog --base64-output=decode-rows --verbose output as claudeperidot's MariaDB 10.5.4 prints it: a ledger write (GTID
# domain 7) tagged with its change, its annotation cut short as 10.5.4 cuts one holding a newline, a GM session's writes to a
# player table and a content table, a DDL statement, a zone's respawn_times purge and world's eqtime save made without
# annotations, a view the server rebuilt, and a statement inside versioned comments, which the server logs as sent.
binaryLogSample = """/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=1*/;
/*!40019 SET @@session.max_insert_delayed_threads=0*/;
/*!50003 SET @OLD_COMPLETION_TYPE=@@COMPLETION_TYPE,COMPLETION_TYPE=0*/;
DELIMITER /*!*/;
# at 4321
#691231 18:00:00 server id 1  end_log_pos 0 	Rotate to mysql-bin.000002  pos: 4321
# at 4321
#261011 08:00:00 server id 1  end_log_pos 0 CRC32 0x7ce0b7af 	Start: binlog v 4, server v 10.5.4-MariaDB-1:10.5.4+maria~focal-log created 261011 08:00:00
# at 4321
#261011 10:01:00 server id 1  end_log_pos 4363 CRC32 0x2a5b1c3d 	GTID 7-1-15 trans
/*!100101 SET @@session.skip_parallel_replication=0*//*!*/;
/*!100001 SET @@session.gtid_domain_id=7*//*!*/;
/*!100001 SET @@session.server_id=1*//*!*/;
/*!100001 SET @@session.gtid_seq_no=15*//*!*/;
BEGIN
/*!*/;
# at 4363
# at 4480
#261011 10:01:00 server id 1  end_log_pos 4480 CRC32 0x1b2c3d4e 	Annotate_rows:
#Q> /* ledger 0001 apply */ UPDATE rule_values SET rule_value = '19'
#Q> WHERE ruleset_id = 1 AND rule_name = 'Expansion:CurrentExpansion
#261011 10:01:00 server id 1  end_log_pos 4545 CRC32 0x5f6e7d8c 	Table_map: `peq`.`rule_values` mapped to number 31
# at 4545
#261011 10:01:00 server id 1  end_log_pos 4660 CRC32 0x0a1b2c3d 	Update_rows: table id 31 flags: STMT_END_F
### UPDATE `peq`.`rule_values`
### WHERE
###   @1=1
###   @2='Expansion:CurrentExpansion'
###   @3='4'
### SET
###   @1=1
###   @2='Expansion:CurrentExpansion'
###   @3='19'
# Number of rows: 1
# at 4660
#261011 10:01:00 server id 1  end_log_pos 4691 CRC32 0x3c4d5e6f 	Xid = 120
COMMIT/*!*/;
# at 4691
#261011 10:05:12 server id 1  end_log_pos 4733 CRC32 0x4d5e6f70 	GTID 0-1-16 trans
/*!100001 SET @@session.gtid_domain_id=0*//*!*/;
/*!100001 SET @@session.gtid_seq_no=16*//*!*/;
BEGIN
/*!*/;
# at 4733
# at 4800
#261011 10:05:12 server id 1  end_log_pos 4800 CRC32 0x5e6f7081 	Annotate_rows:
#Q> UPDATE character_data SET zone_id = 202 WHERE id = 1
#261011 10:05:12 server id 1  end_log_pos 4860 CRC32 0x6f708192 	Table_map: `peq`.`character_data` mapped to number 40
# at 4860
#261011 10:05:12 server id 1  end_log_pos 4950 CRC32 0x708192a3 	Update_rows: table id 40 flags: STMT_END_F
### UPDATE `peq`.`character_data`
### WHERE
###   @1=1
### SET
###   @1=1
# Number of rows: 1
# at 4950
# at 5010
#261011 10:05:12 server id 1  end_log_pos 5010 CRC32 0x8192a3b4 	Annotate_rows:
#Q> INSERT INTO spawn2 (id, zone) VALUES (900001, 'freporte'), (900002, 'freporte')
#261011 10:05:12 server id 1  end_log_pos 5060 CRC32 0x92a3b4c5 	Table_map: `peq`.`spawn2` mapped to number 41
# at 5060
#261011 10:05:12 server id 1  end_log_pos 5140 CRC32 0xa3b4c5d6 	Write_rows: table id 41 flags: STMT_END_F
### INSERT INTO `peq`.`spawn2`
### SET
###   @1=900001
###   @2='freporte'
### INSERT INTO `peq`.`spawn2`
### SET
###   @1=900002
###   @2='freporte'
# Number of rows: 2
# at 5140
#261011 10:05:12 server id 1  end_log_pos 5171 CRC32 0xb4c5d6e7 	Xid = 131
COMMIT/*!*/;
# at 5171
#261011 10:06:00 server id 1  end_log_pos 5213 CRC32 0xc5d6e7f8 	GTID 0-1-17 ddl
/*!100001 SET @@session.gtid_seq_no=17*//*!*/;
# at 5213
#261011 10:06:00 server id 1  end_log_pos 5330 CRC32 0xd6e7f809 	Query	thread_id=12	exec_time=0	error_code=0
use `peq`/*!*/;
SET TIMESTAMP=1791626760/*!*/;
SET @@session.pseudo_thread_id=12/*!*/;
SET @@session.foreign_key_checks=1, @@session.sql_auto_is_null=0, @@session.unique_checks=1, @@session.autocommit=1, @@session.check_constraint_checks=1, @@session.sql_if_exists=0/*!*/;
SET @@session.sql_mode=1411383296/*!*/;
SET @@session.auto_increment_increment=1, @@session.auto_increment_offset=1/*!*/;
/*!\\C utf8mb4 *//*!*/;
SET @@session.character_set_client=45,@@session.collation_connection=45,@@session.collation_server=45/*!*/;
SET @@session.lc_time_names=0/*!*/;
SET @@session.collation_database=DEFAULT/*!*/;
ALTER TABLE items
  ADD COLUMN claude_note INT
/*!*/;
# at 5330
#261011 10:07:00 server id 1  end_log_pos 5372 CRC32 0xe7f8091a 	GTID 0-1-18 trans
/*!100001 SET @@session.gtid_seq_no=18*//*!*/;
BEGIN
/*!*/;
# at 5372
#261011 10:07:00 server id 1  end_log_pos 5420 CRC32 0xf8091a2b 	Table_map: `peq`.`respawn_times` mapped to number 52
# at 5420
#261011 10:07:00 server id 1  end_log_pos 5480 CRC32 0x091a2b3c 	Delete_rows: table id 52 flags: STMT_END_F
### DELETE FROM `peq`.`respawn_times`
### WHERE
###   @1=345
# Number of rows: 1
# at 5480
#261011 10:07:00 server id 1  end_log_pos 5511 CRC32 0x1a2b3c4d 	Xid = 140
COMMIT/*!*/;
# at 5511
#261011 10:10:00 server id 1  end_log_pos 5553 CRC32 0x00000001 	GTID 0-1-19 trans
/*!100001 SET @@session.gtid_seq_no=19*//*!*/;
BEGIN
/*!*/;
# at 5553
#261011 10:10:00 server id 1  end_log_pos 5597 CRC32 0x00000002 	Table_map: `peq`.`eqtime` mapped to number 70
# at 5597
#261011 10:10:00 server id 1  end_log_pos 5657 CRC32 0x00000003 	Update_rows: table id 70 flags: STMT_END_F
### UPDATE `peq`.`eqtime`
### WHERE
###   @1=10
### SET
###   @1=11
# Number of rows: 1
# at 5657
#261011 10:10:00 server id 1  end_log_pos 5688 CRC32 0x00000004 	Xid = 200
COMMIT/*!*/;
# at 5688
#261011 10:11:00 server id 1  end_log_pos 5730 CRC32 0x00000005 	GTID 0-1-20 ddl
/*!100001 SET @@session.gtid_seq_no=20*//*!*/;
# at 5730
#261011 10:11:00 server id 1  end_log_pos 5900 CRC32 0x00000006 	Query	thread_id=12	exec_time=0	error_code=0
SET TIMESTAMP=1791626760/*!*/;
CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`%` SQL SECURITY DEFINER VIEW `v_spawns` AS select 'secret' AS `x`
/*!*/;
# at 5900
#261011 10:12:00 server id 1  end_log_pos 5942 CRC32 0x00000007 	GTID 0-1-21 ddl
/*!100001 SET @@session.gtid_seq_no=21*//*!*/;
# at 5942
#261011 10:12:00 server id 1  end_log_pos 6060 CRC32 0x00000008 	Query	thread_id=12	exec_time=0	error_code=0
SET TIMESTAMP=1791626820/*!*/;
/*M!100101 ALTER TABLE spawn2 COMMENT = 'secret' */
/*!*/;
DELIMITER ;
# End of log file
ROLLBACK /* added by mysqlbinlog */;
/*!50003 SET COMPLETION_TYPE=@OLD_COMPLETION_TYPE*/;
/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=0*/;
"""


def test_theBinaryLogIsReadIntoTransactionsWithTheirWritesTagsAndStatements():
  transactions = ledger.parseBinaryLog(binaryLogSample)
  assert [(transaction.gtid, transaction.domain, transaction.time, transaction.position) for transaction in transactions] == [
    ("7-1-15", 7, "261011 10:01:00", "4321"), ("0-1-16", 0, "261011 10:05:12", "4691"),
    ("0-1-17", 0, "261011 10:06:00", "5171"), ("0-1-18", 0, "261011 10:07:00", "5330"),
    ("0-1-19", 0, "261011 10:10:00", "5511"), ("0-1-20", 0, "261011 10:11:00", "5688"),
    ("0-1-21", 0, "261011 10:12:00", "5900")]
  assert [transaction.writes for transaction in transactions] == [
    {("peq", "rule_values", "UPDATE", "0001"): 1},
    {("peq", "character_data", "UPDATE", None): 1, ("peq", "spawn2", "INSERT", None): 2},
    {},
    {("peq", "respawn_times", "DELETE", None): 1},
    {("peq", "eqtime", "UPDATE", None): 1},
    {},
    {}]
  assert [transaction.statements for transaction in transactions] == [[], [], [(None, "ALTER TABLE items\n  ADD COLUMN "
    "claude_note INT", "peq")], [], [], [(None, "CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`%` SQL SECURITY DEFINER VIEW "
    "`v_spawns` AS select 'secret' AS `x`", "peq")], [(None, "ALTER TABLE spawn2 COMMENT = 'secret'", "peq")]]


def test_theParserTakesItsInputALineAtATime():
  lines = []
  for transaction in ledger.parseBinaryLog(binaryLogSample):
    lines.append(transaction.gtid)
  fed = []
  parser = ledger.BinaryLogParser(lambda transaction: fed.append(transaction.gtid))
  for line in binaryLogSample.splitlines():
    parser.feed(line)
    if line.startswith("#261011 10:07:00") and "GTID" in line:
      assert fed == ["7-1-15", "0-1-16", "0-1-17"]
  parser.finish()
  assert fed == lines


class ChunkedConnection:
  """An SSH connection whose command output arrives in pieces that split lines anywhere."""

  def __init__(self, output, chunk):
    self.output = output
    self.chunk = chunk

  def streamCommand(self, command, writeOutput, standardInput=b"", timeoutSeconds=60):
    for start in range(0, len(self.output), self.chunk):
      writeOutput(self.output[start:start + self.chunk])
    return 0, b""


@pytest.mark.parametrize("chunk", [1, 5, 4096])
def test_aStreamedCommandHandsOverWholeLinesWhereverItsOutputSplits(chunk):
  lines = []
  ledger.Server(ChunkedConnection("first\nsecond caf\u00e9\r\n\nlast".encode("utf-8"), chunk)).stream("cat", lines.append)
  assert lines == ["first", "second caf\u00e9", "", "last"]


def account(transactions, changes=()):
  drift = ledger.DriftAccount(list(changes))
  for transaction in transactions:
    drift.add(transaction)
  return drift


def test_onlyWritesNoChangeAccountsForToWatchedTablesAreUnaccountedAndRuntimeWritesAreApart(repository):
  writeChange(repository, "0001-expansionTest", rowChangeFields(), rowFiles())
  drift = account(ledger.parseBinaryLog(binaryLogSample), ledger.loadChanges(repository))
  assert drift.problems == [
    "unaccounted write: 261011 10:05:12 GTID 0-1-16 at 4691: INSERT spawn2 x2",
    "unaccounted write: 261011 10:06:00 GTID 0-1-17 at 5171: ALTER TABLE items ADD COLUMN claude_note INT",
    "unaccounted write: 261011 10:11:00 GTID 0-1-20 at 5688: CREATE ALGORITHM=UNDEFINED DEFINER=`root`@`%` SQL SECURITY "
    "DEFINER VIEW `v_spawns` AS select '?' AS `x`",
    "unaccounted write: 261011 10:12:00 GTID 0-1-21 at 5900: ALTER TABLE spawn2 COMMENT = '?'"]
  assert drift.runtimeLines() == ["runtime (the servers' own writes, not drift): UPDATE eqtime x1",
    "runtime (the servers' own writes, not drift): DELETE respawn_times x1"]


def test_aLedgerWriteOutsideItsChangesScopeOrWithoutATagIsReported(repository):
  writeChange(repository, "0001-expansionTest", rowChangeFields(), rowFiles())
  writeChange(repository, "0002-itemsColumns", schemaChangeFields(), {})
  changes = ledger.loadChanges(repository)
  transaction = ledger.BinaryLogTransaction("7-1-30", 7, "261011 11:00:00", "9000", {
    ("peq", "rule_values", "UPDATE", "0001"): 1, ("peq", "variables", "UPDATE", "0001"): 1, ("peq", "rule_values", "INSERT",
    None): 1, ("peq", "rule_values", "DELETE", "0009"): 1}, [("0002", "ALTER TABLE items ADD COLUMN a INT", "peq"),
    ("0001", "ALTER TABLE rule_values ADD COLUMN x INT", "peq"), (None, "DROP TABLE spawn2", "peq"),
    ("0002", "ALTER TABLE scratch ADD COLUMN a INT", "ledgerTest")])
  assert account([transaction], changes).problems == ["ledger write outside its change's scope: 261011 11:00:00 GTID 7-1-30 "
    "at 9000: DELETE peq.rule_values x1 by 0009, which is not a db change in the ledger; INSERT peq.rule_values x1 with no "
    "ledger tag; UPDATE peq.variables x1 by 0001, whose scope is rule_values; ALTER TABLE rule_values ADD COLUMN x INT by "
    "0001, which is not a schema change in the ledger; DROP TABLE spawn2 with no ledger tag; ALTER TABLE scratch ADD COLUMN a "
    "INT in ledgerTest by 0002, which is not a schema change in the ledger"]


def test_aLedgerSchemaStatementFromASessionWithNoDatabaseIsOutsideItsScope(repository):
  writeChange(repository, "0002-itemsColumns", schemaChangeFields(), {})
  transaction = ledger.BinaryLogTransaction("7-1-31", 7, "261011 11:05:00", "9100", {}, [
    ("0002", "ALTER TABLE items ADD COLUMN a INT", None)])
  assert account([transaction], ledger.loadChanges(repository)).problems == ["ledger write outside its change's scope: "
    "261011 11:05:00 GTID 7-1-31 at 9100: ALTER TABLE items ADD COLUMN a INT in no database by 0002, which is not a schema "
    "change in the ledger"]


def test_writesInAnotherDatabaseAreNotThePeqDatabasesDrift():
  transaction = ledger.BinaryLogTransaction("0-1-40", 0, "261011 12:00:00", "9900", {("ledgerTest", "payloads", "INSERT",
    None): 2, ("mysql", "user", "UPDATE", None): 1}, [(None, "CREATE TABLE payloads (id INT)", "ledgerTest"),
    (None, "DROP DATABASE ledgerTest", "mysql")])
  assert account([transaction]).problems == []


def test_aStatementNamingPeqIsPeqsDriftWhateverDatabaseItRanIn():
  transaction = ledger.BinaryLogTransaction("0-1-41", 0, "261011 12:05:00", "9950", {}, [
    (None, "ALTER TABLE peq.items DROP INDEX byName", "mysql"),
    (None, "ALTER TABLE `peq` . `spawn2` ADD INDEX byZone (zone)", "ledgerTest"),
    (None, "ALTER TABLE ledgerTest.peqCopy ADD COLUMN a INT", "mysql"),
    (None, "CREATE TABLE notpeq.items (id INT)", "mysql")])
  assert account([transaction]).problems == ["unaccounted write: 261011 12:05:00 GTID 0-1-41 at 9950: ALTER TABLE peq.items "
    "DROP INDEX byName; ALTER TABLE `peq` . `spawn2` ADD INDEX byZone (zone)"]


# A server start as claudeperidot's mysqlbinlog printed it: the provisioner's DROP USER from a session with no database (no
# use line, at the start of the stream), Spire's migration and its connection sync in peq, world emptying the trader table,
# then account statements from a session with no database, which mysqlbinlog prints without a use line after peq's.
serverStartSample = """/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=1*/;
DELIMITER /*!*/;
# at 121867
#261011 15:38:08 server id 1  end_log_pos 416 CRC32 0x53709eab 	GTID 0-1-7237 ddl
/*!100001 SET @@session.gtid_domain_id=0*//*!*/;
/*!100001 SET @@session.gtid_seq_no=7237*//*!*/;
# at 121909
#261011 15:38:08 server id 1  end_log_pos 509 CRC32 0x58a1deb6 	Query	thread_id=5	exec_time=0	error_code=0
SET TIMESTAMP=1791751088/*!*/;
SET @@session.pseudo_thread_id=5/*!*/;
SET @@session.sql_mode=1411383296/*!*/;
/*!\\C latin1 *//*!*/;
SET @@session.collation_database=DEFAULT/*!*/;
DROP USER IF EXISTS 'root'@'%'
/*!*/;
# at 122002
#261011 15:38:19 server id 1  end_log_pos 551 CRC32 0x31cb86c0 	GTID 0-1-7238 ddl
/*!100001 SET @@session.gtid_seq_no=7238*//*!*/;
# at 122044
#261011 15:38:19 server id 1  end_log_pos 682 CRC32 0xd9da8301 	Query	thread_id=8	exec_time=0	error_code=0
use `peq`/*!*/;
SET TIMESTAMP=1791751099/*!*/;
ALTER TABLE `spire_settings` MODIFY COLUMN `setting` varchar(190)
/*!*/;
# at 122175
#261011 15:38:19 server id 1  end_log_pos 724 CRC32 0xb07f6508 	GTID 0-1-7239 trans
/*!100001 SET @@session.gtid_seq_no=7239*//*!*/;
BEGIN
/*!*/;
# at 122217
# at 122996
#261011 15:38:19 server id 1  end_log_pos 1503 CRC32 0x3702581b 	Annotate_rows:
#Q> UPDATE `spire_server_database_connections` SET `name`='ClaudePeridot',`db_password`='secret' WHERE `id` = 1
#261011 15:38:19 server id 1  end_log_pos 1641 CRC32 0x6b79e10a 	Table_map: `peq`.`spire_server_database_connections` mapped to number 23
# at 123134
#261011 15:38:19 server id 1  end_log_pos 2279 CRC32 0x6c9f6a47 	Update_rows: table id 23 flags: STMT_END_F
### UPDATE `peq`.`spire_server_database_connections`
### WHERE
###   @1=1
### SET
###   @1=1
# Number of rows: 1
# at 123772
#261011 15:38:19 server id 1  end_log_pos 2310 CRC32 0xcd46b869 	Xid = 488
COMMIT/*!*/;
# at 125430
#261011 15:38:34 server id 1  end_log_pos 3979 CRC32 0xb1be5888 	GTID 0-1-7241 ddl
/*!100001 SET @@session.gtid_seq_no=7241*//*!*/;
# at 125472
#261011 15:38:34 server id 1  end_log_pos 4066 CRC32 0xe25d4d07 	Query	thread_id=12	exec_time=1	error_code=0
SET TIMESTAMP=1791751114/*!*/;
TRUNCATE TABLE trader
/*!*/;
# at 125559
#261011 15:40:00 server id 1  end_log_pos 4108 CRC32 0x60649a2c 	GTID 0-1-7242 ddl
/*!100001 SET @@session.gtid_seq_no=7242*//*!*/;
# at 125601
#261011 15:40:00 server id 1  end_log_pos 4194 CRC32 0xef0b95bb 	Query	thread_id=14	exec_time=0	error_code=0
SET TIMESTAMP=1791751200/*!*/;
SET PASSWORD FOR 'eqemu'@'%' = PASSWORD('hunter2')
/*!*/;
# at 125687
#261011 15:40:01 server id 1  end_log_pos 4236 CRC32 0x1aac2bc7 	GTID 0-1-7243 ddl
/*!100001 SET @@session.gtid_seq_no=7243*//*!*/;
# at 125729
#261011 15:40:01 server id 1  end_log_pos 4332 CRC32 0x117dcdd9 	Query	thread_id=14	exec_time=0	error_code=0
SET TIMESTAMP=1791751201/*!*/;
GRANT ALL PRIVILEGES ON `peq`.* TO 'eqemu'@'%'
/*!*/;
# at 125825
#261011 15:40:02 server id 1  end_log_pos 4374 CRC32 0x98e7669c 	GTID 0-1-7244 ddl
/*!100001 SET @@session.gtid_seq_no=7244*//*!*/;
# at 125867
#261011 15:40:02 server id 1  end_log_pos 4472 CRC32 0x6d4c9388 	Query	thread_id=14	exec_time=0	error_code=0
SET TIMESTAMP=1791751202/*!*/;
FLUSH PRIVILEGES
/*!*/;
DELIMITER ;
# End of log file
ROLLBACK /* added by mysqlbinlog */;
"""


def test_aStatementBeforeAnyUseLineRanInNoDatabaseAndSetPasswordIsKept():
  transactions = ledger.parseBinaryLog(serverStartSample)
  assert [transaction.statements for transaction in transactions] == [
    [(None, "DROP USER IF EXISTS 'root'@'%'", None)],
    [(None, "ALTER TABLE `spire_settings` MODIFY COLUMN `setting` varchar(190)", "peq")],
    [],
    [(None, "TRUNCATE TABLE trader", "peq")],
    [(None, "SET PASSWORD FOR 'eqemu'@'%' = PASSWORD('hunter2')", "peq")],
    [(None, "GRANT ALL PRIVILEGES ON `peq`.* TO 'eqemu'@'%'", "peq")],
    [(None, "FLUSH PRIVILEGES", "peq")]]


def test_aServerStartsOwnWritesAreRuntimeAndAccountStatementsAreServerAccountChanges():
  drift = account(ledger.parseBinaryLog(serverStartSample))
  assert drift.problems == []
  assert drift.accountLines() == [
    "server account change (not a peq write): 261011 15:38:08 GTID 0-1-7237 at 121867: DROP USER IF EXISTS '?'@'?'",
    "server account change (not a peq write): 261011 15:40:00 GTID 0-1-7242 at 125559: SET PASSWORD FOR '?'@'?' = "
    "PASSWORD('?')",
    "server account change (not a peq write): 261011 15:40:01 GTID 0-1-7243 at 125687: GRANT ALL PRIVILEGES ON `peq`.* TO "
    "'?'@'?'",
    "server account change (not a peq write): 261011 15:40:02 GTID 0-1-7244 at 125825: FLUSH PRIVILEGES"]
  assert drift.runtimeLines() == [
    "runtime (the servers' own writes, not drift): UPDATE spire_server_database_connections x1",
    "runtime (the servers' own writes, not drift): ALTER TABLE spire_settings x1",
    "runtime (the servers' own writes, not drift): TRUNCATE trader x1"]


@pytest.mark.parametrize("statement", ["CREATE USER 'claude'@'%' IDENTIFIED BY 'x'", "create or replace user claude",
  "ALTER USER 'eqemu'@'%' IDENTIFIED BY 'x'", "RENAME USER 'a'@'%' TO 'b'@'%'", "DROP USER 'root'@'%'",
  "REVOKE ALL PRIVILEGES ON *.* FROM 'eqemu'@'%'", "CREATE ROLE editor", "DROP ROLE IF EXISTS editor",
  "SET DEFAULT ROLE editor FOR 'eqemu'@'%'", "/* provisioner */ GRANT SELECT ON peq.spawn2 TO 'reader'@'%'",
  "FLUSH TABLES"])
def test_eachAccountStatementIsAServerAccountChangeInAnyDatabase(statement):
  for database in (None, "peq", "mysql"):
    drift = account([ledger.BinaryLogTransaction("0-1-50", 0, "261011 13:00:00", "9990", {}, [(None, statement, database)])])
    assert drift.problems == []
    assert drift.accountLines() == ["server account change (not a peq write): 261011 13:00:00 GTID 0-1-50 at 9990: "
      + ledger.redactValues(statement)]


def test_aSessionWithNoDatabaseWritesPeqOnlyByNamingIt():
  transaction = ledger.BinaryLogTransaction("0-1-51", 0, "261011 13:05:00", "9995", {}, [
    (None, "CREATE DATABASE scratch", None), (None, "CREATE TABLE scratch.notes (id INT)", None),
    (None, "ALTER TABLE peq.items ADD COLUMN note INT", None)])
  assert account([transaction]).problems == ["unaccounted write: 261011 13:05:00 GTID 0-1-51 at 9995: ALTER TABLE peq.items "
    "ADD COLUMN note INT"]


def test_aDeliberateChangeToARuntimeOrSpireTableIsStillDrift():
  transaction = ledger.BinaryLogTransaction("0-1-52", 0, "261011 13:10:00", "9999", {
    ("peq", "spire_users", "UPDATE", None): 1, ("peq", "spire_settings", "INSERT", None): 1}, [
    (None, "ALTER TABLE eqtime ADD COLUMN note INT", "peq"), (None, "TRUNCATE TABLE items", "peq"),
    (None, "TRUNCATE character_data", "peq"), (None, "CREATE INDEX byName ON spire_users (user_name)", "peq"),
    (None, "ALTER TABLE `peq`.`spire_settings` MODIFY COLUMN `setting` varchar(190)", "mysql"),
    (None, "TRUNCATE /* world */ TABLE `peq` . `buyer`", "mysql"), (None, "TRUNCATE TABLE trader", "ledgerTest")])
  drift = account([transaction])
  assert drift.problems == ["unaccounted write: 261011 13:10:00 GTID 0-1-52 at 9999: INSERT spire_settings x1; UPDATE "
    "spire_users x1; ALTER TABLE eqtime ADD COLUMN note INT; TRUNCATE TABLE items; TRUNCATE character_data; CREATE INDEX "
    "byName ON spire_users (user_name)"]
  assert drift.runtimeLines() == ["runtime (the servers' own writes, not drift): TRUNCATE buyer x1",
    "runtime (the servers' own writes, not drift): ALTER TABLE spire_settings x1"]


def writeBaseline(repository, fake, position):
  """A baseline at position holding the checksum of every table of fake drift watches."""
  tables = [table for table in fake.tables if ledger.isWatched(table)]
  baseline = {"dump": {"path": "/opt/eqemu/baseline/peq-1.sql.gz", "sha256": "0" * 64, "time": "2026-10-12T00:00:00Z"},
    "dbVersion": {"version": 9325, "botsVersion": 0, "customVersion": 0}, "binaryLog": {"file": "mysql-bin.000002",
    "position": position}, "deployed": {"akkStack": "13f442d"}, "tableChecksums": {table: fake.tableChecksum(table)
    for table in tables}}
  (repository / "baseline.yaml").write_text(yaml.safe_dump(baseline), encoding="utf-8")


def driftFake():
  fake = ruleFake()
  fake.tables["scratch"] = FakeTable([("id", "int")], ("id",), [{"id": 1}])
  fake.tables["items"] = FakeTable([("id", "int")], ("id",), [{"id": 1}])
  fake.tables["character_data"] = FakeTable([("id", "int"), ("zone_id", "int")], ("id",), [{"id": 1, "zone_id": 1}])
  fake.tables["eqtime"] = FakeTable([("id", "int"), ("minute", "int")], ("id",), [{"id": 1, "minute": 10}])
  fake.tables["gm_ips"] = FakeTable([("ip_address", "varchar", "latin1")], ("ip_address",), [])
  return fake


def driftHarness(repository, fake=None):
  fake = fake or driftFake()
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-expansionTest", rowChangeFields(), rowFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. Set the expansion [0001]"]), encoding="utf-8")
  harness.commitAll()
  writeBaseline(repository, fake, fake.nextPosition)
  harness.commitAll("baseline")
  return harness


def test_driftIsCleanWhenOnlyTheLedgerAndTheServersRuntimeWrote(repository):
  harness = driftHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  harness.fake.externalWrite("UPDATE eqtime SET minute = 11", lambda fake: fake.tables["eqtime"].rows[0].update(minute=11))
  harness.fake.externalWrite("UPDATE character_data SET zone_id = 202 WHERE id = 1",
    lambda fake: fake.tables["character_data"].rows[0].update(zone_id=202))
  assert harness.ledger.drift() == ["drift: clean", "runtime (the servers' own writes, not drift): UPDATE eqtime x1"]


def test_aServerStartLeavesDriftCleanWithItsAccountChangesAndRuntimeWritesApart(repository):
  fake = driftFake()
  fake.tables["spire_server_database_connections"] = FakeTable([("id", "int"), ("db_password", "varchar", "latin1")], ("id",),
    [{"id": 1, "db_password": b"sealed once"}])
  fake.tables["spire_settings"] = FakeTable([("id", "int"), ("setting", "varchar", "latin1")], ("id",),
    [{"id": 1, "setting": b"AUTH_ENABLED"}])
  fake.tables["trader"] = FakeTable([("id", "int")], ("id",), [{"id": 7}])
  harness = driftHarness(repository, fake)
  fake.externalWrite("DROP USER IF EXISTS 'root'@'%'", lambda fake: None, database=None)
  fake.externalWrite("ALTER TABLE `spire_settings` MODIFY COLUMN `setting` varchar(190)", lambda fake: None)
  for sealed in (b"sealed twice", b"sealed again"):
    fake.externalWrite("UPDATE `spire_server_database_connections` SET `db_password`='sealed' WHERE `id` = 1",
      lambda fake, sealed=sealed: fake.tables["spire_server_database_connections"].rows[0].update(db_password=sealed))
  fake.externalWrite("TRUNCATE TABLE trader", lambda fake: fake.tables["trader"].rows.clear())
  fake.externalWrite("UPDATE eqtime SET minute = 11", lambda fake: fake.tables["eqtime"].rows[0].update(minute=11))
  assert harness.ledger.drift() == ["drift: clean",
    "server account change (not a peq write): 261011 10:00:00 GTID 0-1-15 at 4321: DROP USER IF EXISTS '?'@'?'",
    "runtime (the servers' own writes, not drift): UPDATE eqtime x1",
    "runtime (the servers' own writes, not drift): UPDATE spire_server_database_connections x2",
    "runtime (the servers' own writes, not drift): ALTER TABLE spire_settings x1",
    "runtime (the servers' own writes, not drift): TRUNCATE trader x1"]


def test_driftListsTableDifferencesAndUnaccountedWrites(repository):
  harness = driftHarness(repository)
  harness.fake.tables["created"] = FakeTable([("id", "int")], ("id",), [])
  harness.fake.externalWrite("INSERT INTO items (id) VALUES (2)", lambda fake: fake.tables["items"].insert({"id": 2}))
  harness.fake.externalWrite("INSERT INTO gm_ips VALUES ('10.0.0.9')", lambda fake: fake.tables["gm_ips"].insert(
    {"ip_address": b"10.0.0.9"}))
  del harness.fake.tables["scratch"]
  baseline = yaml.safe_load((repository / "baseline.yaml").read_text())
  assert harness.ledger.drift() == [
    "created: new since the baseline, and no change accounts for it",
    f"gm_ips: checksum {harness.fake.tableChecksum('gm_ips')}, expected {baseline['tableChecksums']['gm_ips']} from the "
    "baseline",
    f"items: checksum {harness.fake.tableChecksum('items')}, expected {baseline['tableChecksums']['items']} from the baseline",
    "scratch: expected from the baseline but gone now",
    "unaccounted write: 261011 10:00:00 GTID 0-1-15 at 4321: INSERT items x1",
    "unaccounted write: 261011 10:00:00 GTID 0-1-16 at 4721: INSERT gm_ips x1"]


def test_aTableTheLedgerWroteIsExpectedAtItsChecksumAfterThatWrite(repository):
  harness = driftHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  expected = json.loads((repository / "tableChecksums.json").read_text())["rule_values"]["checksum"]
  harness.fake.tables["rule_values"].rows[3]["notes"] = b"edited by the PEQ editor outside the binary log"
  assert harness.ledger.drift() == [f"rule_values: checksum {harness.fake.tableChecksum('rule_values')}, expected {expected} "
    "from apply 0001-expansionTest"]


def test_aFixtureOnAPlayTableLeavesDriftClean(repository, host):
  fake = driftFake()
  fake.host = host
  fake.tables["account"] = FakeTable([("id", "int"), ("status", "int")], ("id",), [{"id": 1, "status": 0}])
  fake.predicates["id = 1"] = lambda row: row["id"] == 1
  gm = "UPDATE account SET status = 255 WHERE id = 1"
  fake.mutations[gm] = lambda fake: fake.tables["account"].rows[0].update(status=255)
  check = "SELECT status = 255 AS applied FROM account WHERE id = 1"
  fake.checks[check] = lambda fake: int(fake.tables["account"].rows[0]["status"] == 255)
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-gmAccount", rowChangeFields(scope=[{"table": "account", "where": "id = 1"}],
    disposition="fixture"), {"apply.sql": gm + ";\n", "check.sql": check + ";\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 13. GM fixture [0001]"]), encoding="utf-8")
  harness.commitAll()
  writeBaseline(repository, fake, fake.nextPosition)
  harness.commitAll("baseline")
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  assert harness.ledger.drift() == ["drift: clean"]


def test_theExpectedChecksumFollowsTheLedgersWritesInTheOrderTheyRan(repository):
  fake = driftFake()
  second = "UPDATE rule_values SET rule_value = '8' WHERE ruleset_id = 2"
  fake.mutations[second] = lambda fake: setRule(fake, 2, b"Expansion:CurrentExpansion", b"8")
  secondCheck = "SELECT 'ruleset2' AS applied"
  fake.checks[secondCheck] = lambda fake: int(any(row["rule_value"] == b"8" for row in fake.tables["rule_values"].rows
    if row["ruleset_id"] == 2))
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-expansionTest", rowChangeFields(), {"apply.sql": expansionUpdate + ";\n",
    "check.sql": expansionCheck + ";\n"})
  writeChange(repository, "0002-rulesetTwo", rowChangeFields(scope=[{"table": "rule_values", "where": "ruleset_id = 2"}]),
    {"apply.sql": second + ";\n", "check.sql": secondCheck + ";\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 1. One [0001]", "- [ ] 2. Two [0002]"]), encoding="utf-8")
  harness.commitAll()
  writeBaseline(repository, fake, fake.nextPosition)
  harness.commitAll("baseline")
  for number in ("0002", "0001"):
    harness.ledger.stage(number)
    harness.ledger.apply(number)
  assert harness.ledger.drift() == ["drift: clean"]
  harness.ledger.revert("0001")
  assert harness.ledger.drift() == ["drift: clean"]


def test_aWriteApplyMadeToATableOutsideItsScopeIsReported(repository):
  fake = driftFake()
  fake.tables["variables"] = FakeTable([("varname", "varchar", "latin1"), ("value", "text", "latin1")], ("varname",),
    [{"varname": b"motd", "value": b"hello"}])
  other = "UPDATE variables SET value = 'changed' WHERE varname = 'motd'"
  fake.mutations[other] = lambda fake: fake.tables["variables"].rows[0].update(value=b"changed")
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-expansionTest", rowChangeFields(), {"apply.sql": f"{expansionUpdate};\n{other};\n",
    "check.sql": expansionCheck + ";\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 1. Set the expansion [0001]"]), encoding="utf-8")
  harness.commitAll()
  writeBaseline(repository, fake, fake.nextPosition)
  harness.commitAll("baseline")
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  report = harness.ledger.drift()
  baseline = yaml.safe_load((repository / "baseline.yaml").read_text())
  assert report[0] == (f"variables: checksum {fake.tableChecksum('variables')}, expected "
    f"{baseline['tableChecksums']['variables']} from the baseline")
  assert report[1] == ("ledger write outside its change's scope: 261011 10:00:00 GTID 7-1-15 at 4321: UPDATE peq.variables x1 "
    "by 0001, whose scope is rule_values")


def test_aSchemaChangeThatDropsATableLeavesDriftClean(repository):
  fake = itemsFake()
  fake.tables["scratch"] = FakeTable([("id", "int")], ("id",), [{"id": 1}])
  fake.createStatements["scratch"] = "CREATE TABLE `scratch` (\n  `id` int(11) NOT NULL\n) ENGINE=InnoDB"
  drop = "DROP TABLE scratch"
  fake.mutations[drop] = lambda fake: fake.tables.pop("scratch")
  check = "SELECT COUNT(*) = 0 AS applied FROM information_schema.TABLES WHERE TABLE_NAME = 'scratch'"
  fake.checks[check] = lambda fake: int("scratch" not in fake.tables)
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-dropScratch", schemaChangeFields(scope=[{"table": "scratch"}], schemaSteps=1), {
    "apply.sql": drop + ";\n", "revert.sql": "CREATE TABLE scratch (id INT NOT NULL PRIMARY KEY);\n", "check.sql": check + ";\n"})
  (repository / "plan.md").write_text(planText(["- [ ] 1. Drop it [0001]"]), encoding="utf-8")
  harness.commitAll()
  writeBaseline(repository, fake, fake.nextPosition)
  harness.commitAll("baseline")
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  assert [event for transaction in fake.log for event in transaction.events] == [("query",
    "DROP TABLE /* ledger 0001 apply */ `scratch` /* generated by server */")]
  assert harness.ledger.drift() == ["drift: clean"]


def test_aDropTablesTagGoesWhereMariaDBKeepsItAndIsReadBackFromTheStatementTheServerRebuilt(repository):
  change = ledger.loadChange(writeChange(repository, "0001-dropScratch", schemaChangeFields(scope=[{"table": "scratch"}],
    schemaSteps=1)))
  assert ledger.taggedStatements(change, "revert", ["DROP TABLE scratch", "drop  table if exists a, b",
    "ALTER TABLE items DROP COLUMN a"]) == ("DROP TABLE /* ledger 0001 revert */ scratch;\n"
    "drop  table if exists /* ledger 0001 revert */ a, b;\n/* ledger 0001 revert */ ALTER TABLE items DROP COLUMN a;\n")
  assert ledger.splitTag("DROP TABLE IF EXISTS /* ledger 0001 revert */ `a`,`b` /* generated by server */") == ("0001",
    "DROP TABLE IF EXISTS `a`,`b` /* generated by server */")
  assert ledger.splitTag("/* ledger 0002 apply */ ALTER TABLE items DROP COLUMN a") == ("0002",
    "ALTER TABLE items DROP COLUMN a")
  assert ledger.splitTag("DROP TABLE `a` /* generated by server */") == (None, "DROP TABLE `a` /* generated by server */")


def test_driftNamesAChangeLeftMidwayOrRunOutsideTheLedger(repository):
  harness = driftHarness(repository)
  harness.fake.checks[expansionCheck] = lambda fake: 1
  assert harness.ledger.drift()[0] == ("0001-expansionTest is applied; not staged but has no applied.json: it ran outside the "
    "ledger")
  (repository / "changes/0001-expansionTest/intent.json").write_text('{"intent": "applying"}\n', encoding="utf-8")
  assert harness.ledger.drift()[0] == ("0001-expansionTest has an interrupted apply (intent.json): run apply 0001 again to "
    "finish it")


def test_driftNeedsABaselineAndARowFormatBinaryLog(repository):
  harness = driftHarness(repository)
  harness.fake.serverSettings = ["1", "MIXED", "1"]
  with pytest.raises(ledger.LedgerError, match=r"needs log_bin 1, binlog_format ROW and binlog_annotate_row_events 1; the "
      r"server has \[\['1', 'MIXED', '1'\]\]"):
    harness.ledger.drift()
  (repository / "baseline.yaml").unlink()
  with pytest.raises(ledger.LedgerError, match="there is no baseline.yaml yet: seed claudeperidot first"):
    harness.ledger.drift()


def test_advanceMovesTheBaselineToTheLogsEndAfterACleanDrift(repository):
  harness = driftHarness(repository)
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  harness.fake.externalWrite("UPDATE eqtime SET minute = 11", lambda fake: fake.tables["eqtime"].rows[0].update(minute=11))
  lines = harness.ledger.advance()
  assert lines == ["drift: clean", "runtime (the servers' own writes, not drift): UPDATE eqtime x1",
    f"baseline advanced to mysql-bin.000002:{harness.fake.nextPosition}"]
  baseline = yaml.safe_load((repository / "baseline.yaml").read_text())
  assert baseline["binaryLog"] == {"file": "mysql-bin.000002", "position": harness.fake.nextPosition}
  assert baseline["tableChecksums"] == {table: harness.fake.tableChecksum(table) for table in ("gm_ips", "items", "scratch",
    "rule_values")}
  assert "claudeperidot/tableChecksums.json" not in committedFiles(repository)
  assert git(repository, "status", "--porcelain") == ""
  assert harness.ledger.drift() == ["drift: clean"]


def test_advanceRefusesWhileDriftIsNotClean(repository):
  harness = driftHarness(repository)
  harness.fake.externalWrite("INSERT INTO items (id) VALUES (2)", lambda fake: fake.tables["items"].insert({"id": 2}))
  before = (repository / "baseline.yaml").read_text()
  with pytest.raises(ledger.LedgerError, match="advance moves the baseline only after a clean drift"):
    harness.ledger.advance()
  assert (repository / "baseline.yaml").read_text() == before


def test_aBinaryLogPositionThatIsNotAFileAndOffsetIsRefused():
  with pytest.raises(ledger.LedgerError, match="is not a file name and a position"):
    ledger.binaryLogCommand("mysql-bin.000002; rm -rf /", 4)
  with pytest.raises(ledger.LedgerError, match="is not a file name and a position"):
    ledger.binaryLogCommand("mysql-bin.000002", "4")
