import json

import pytest
import yaml

import ledger
from ledgerDoubles import FakeTable, makeHarness, planText, rowChangeFields, rowFiles, ruleFake, writeChange

# mysqlbinlog --base64-output=decode-rows --verbose output in MariaDB 10.5's form: a ledger write (GTID domain 7), a GM
# session's writes to a player table and a content table, a DDL statement, and a write to a state table.
binaryLogSample = """/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=1*/;
/*!40019 SET @@session.max_insert_delayed_threads=0*/;
/*!50003 SET @OLD_COMPLETION_TYPE=@@COMPLETION_TYPE,COMPLETION_TYPE=0*/;
DELIMITER /*!*/;
# at 4321
#261011 10:01:00 server id 1  end_log_pos 4363 CRC32 0x2a5b1c3d 	GTID 7-1-15 trans
/*!100001 SET @@session.gtid_domain_id=7*//*!*/;
/*!100001 SET @@session.server_id=1*//*!*/;
/*!100001 SET @@session.gtid_seq_no=15*//*!*/;
START TRANSACTION
/*!*/;
# at 4363
#261011 10:01:00 server id 1  end_log_pos 4480 CRC32 0x1b2c3d4e 	Annotate_rows:
#Q> UPDATE rule_values SET rule_value = '19' WHERE ruleset_id = 1 AND rule_name = 'Expansion:CurrentExpansion'
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
# at 4660
#261011 10:01:00 server id 1  end_log_pos 4691 CRC32 0x3c4d5e6f 	Xid = 120
COMMIT/*!*/;
# at 4691
#261011 10:05:12 server id 1  end_log_pos 4733 CRC32 0x4d5e6f70 	GTID 0-1-16 trans
/*!100001 SET @@session.gtid_domain_id=0*//*!*/;
/*!100001 SET @@session.gtid_seq_no=16*//*!*/;
START TRANSACTION
/*!*/;
# at 4733
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
# at 4950
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
# at 5140
#261011 10:05:12 server id 1  end_log_pos 5171 CRC32 0xb4c5d6e7 	Xid = 131
COMMIT/*!*/;
# at 5171
#261011 10:06:00 server id 1  end_log_pos 5213 CRC32 0xc5d6e7f8 	GTID 0-1-17 ddl
/*!100001 SET @@session.gtid_seq_no=17*//*!*/;
# at 5213
#261011 10:06:00 server id 1  end_log_pos 5330 CRC32 0xd6e7f809 	Query	thread_id=12	exec_time=0	error_code=0	xid=0
use `peq`/*!*/;
SET TIMESTAMP=1791626760/*!*/;
SET @@session.pseudo_thread_id=12/*!*/;
ALTER TABLE items
  ADD COLUMN claude_note INT
/*!*/;
# at 5330
#261011 10:07:00 server id 1  end_log_pos 5372 CRC32 0xe7f8091a 	GTID 0-1-18 trans
/*!100001 SET @@session.gtid_seq_no=18*//*!*/;
START TRANSACTION
/*!*/;
# at 5372
#261011 10:07:00 server id 1  end_log_pos 5420 CRC32 0xf8091a2b 	Table_map: `peq`.`respawn_times` mapped to number 52
# at 5420
#261011 10:07:00 server id 1  end_log_pos 5480 CRC32 0x091a2b3c 	Delete_rows: table id 52 flags: STMT_END_F
### DELETE FROM `peq`.`respawn_times`
### WHERE
###   @1=345
# at 5480
#261011 10:07:00 server id 1  end_log_pos 5511 CRC32 0x1a2b3c4d 	Xid = 140
COMMIT/*!*/;
DELIMITER ;
# End of log file
ROLLBACK /* added by mysqlbinlog */;
/*!50003 SET COMPLETION_TYPE=@OLD_COMPLETION_TYPE*/;
/*!50530 SET @@SESSION.PSEUDO_SLAVE_MODE=0*/;
"""


def test_theBinaryLogIsReadIntoTransactionsWithTheirWritesAndStatements():
  transactions = ledger.parseBinaryLog(binaryLogSample)
  assert [(transaction.gtid, transaction.domain, transaction.time, transaction.position) for transaction in transactions] == [
    ("7-1-15", 7, "261011 10:01:00", "4321"), ("0-1-16", 0, "261011 10:05:12", "4691"),
    ("0-1-17", 0, "261011 10:06:00", "5171"), ("0-1-18", 0, "261011 10:07:00", "5330")]
  assert [transaction.writes for transaction in transactions] == [
    {("peq", "rule_values", "UPDATE"): 1},
    {("peq", "character_data", "UPDATE"): 1, ("peq", "spawn2", "INSERT"): 2},
    {},
    {("peq", "respawn_times", "DELETE"): 1}]
  assert [transaction.statements for transaction in transactions] == [[], [], ["ALTER TABLE items\n  ADD COLUMN claude_note INT"],
    []]


def test_onlyWritesNoChangeAccountsForToWatchedTablesAreUnaccounted():
  unaccounted = ledger.unaccountedTransactions(ledger.parseBinaryLog(binaryLogSample))
  assert [ledger.describeTransaction(transaction) for transaction in unaccounted] == [
    "261011 10:05:12 GTID 0-1-16 at 4691: INSERT spawn2 x2",
    "261011 10:06:00 GTID 0-1-17 at 5171: ALTER TABLE items ADD COLUMN claude_note INT"]


def driftHarness(repository, baselineChecksums, binaryLog):
  fake = ruleFake()
  fake.tables["items"] = FakeTable([("id", "int", "")], ("id",), [{"id": b"1"}])
  fake.tables["character_data"] = FakeTable([("id", "int", "")], ("id",), [{"id": b"1"}])
  fake.binaryLog = binaryLog
  harness = makeHarness(repository, fake)
  writeChange(repository, "0001-expansionTest", rowChangeFields(), rowFiles())
  (repository / "plan.md").write_text(planText(["- [ ] 1. Set the expansion [0001]"]), encoding="utf-8")
  harness.commitAll()
  harness.ledger.stage("0001")
  harness.ledger.apply("0001")
  baseline = {"dump": {"path": "/opt/eqemu/baseline/peq-1.sql.gz", "sha256": "0" * 64, "time": "2026-10-12T00:00:00Z"},
    "dbVersion": {"version": 9325, "botsVersion": 0, "customVersion": 0}, "binaryLog": {"file": "binlog.000002",
    "position": 4321}, "deployed": {"akkStack": "13f442d"}, "tableChecksums": baselineChecksums(fake)}
  (repository / "baseline.yaml").write_text(yaml.safe_dump(baseline), encoding="utf-8")
  return harness


def test_driftIsCleanWhenTheTablesMatchTheBaselinePlusAppliedChangesAndOnlyTheLedgerWrote(repository):
  ledgerOnly = binaryLogSample.split("# at 4691")[0] + "COMMIT/*!*/;\n"
  harness = driftHarness(repository, lambda fake: {"items": fake.tableChecksum("items"), "rule_values": 1, "character_data": 5},
    ledgerOnly)
  assert harness.ledger.drift() == ["drift: clean"]
  binaryLogCommand = next(command for command, _ in harness.fake.calls if "mysqlbinlog" in command)
  assert binaryLogCommand.endswith("--start-position=4321 --to-last-log binlog.000002'")


def test_driftListsTableDifferencesAndUnaccountedWrites(repository):
  harness = driftHarness(repository, lambda fake: {"items": 99, "rule_values": 1, "gone": 5}, binaryLogSample)
  harness.fake.tables["created"] = FakeTable([("id", "int", "")], ("id",), [])
  assert harness.ledger.drift() == [
    "created: new since the baseline, and no change accounts for it",
    "gone: in the baseline but gone now",
    f"items: checksum {harness.fake.tableChecksum('items')}, expected 99 from the baseline",
    "unaccounted write: 261011 10:05:12 GTID 0-1-16 at 4691: INSERT spawn2 x2",
    "unaccounted write: 261011 10:06:00 GTID 0-1-17 at 5171: ALTER TABLE items ADD COLUMN claude_note INT"]


def test_aTableAnAppliedChangeTouchedIsExpectedAtItsAppliedChecksum(repository):
  harness = driftHarness(repository, lambda fake: {"items": fake.tableChecksum("items"), "rule_values": 1}, "")
  applied = json.loads((repository / "changes/0001-expansionTest/applied.json").read_text())
  harness.fake.tables["rule_values"].rows[0]["notes"] = b"edited by the PEQ editor"
  assert harness.ledger.drift() == [f"rule_values: checksum {harness.fake.tableChecksum('rule_values')}, expected "
    f"{applied['tableChecksums']['rule_values']} from 0001-expansionTest"]


def test_driftNamesAnAppliedChangeWithoutItsAppliedRecord(repository):
  harness = driftHarness(repository, lambda fake: {"items": fake.tableChecksum("items"), "rule_values": 1}, "")
  (repository / "changes/0001-expansionTest/applied.json").unlink()
  assert harness.ledger.drift()[0] == ("0001-expansionTest is applied but has no applied.json: its apply stopped before the "
    "commit, or it ran outside the ledger")


def test_driftNeedsABaseline(repository):
  harness = makeHarness(repository)
  with pytest.raises(ledger.LedgerError, match="there is no baseline.yaml yet: seed claudeperidot first"):
    harness.ledger.drift()


def test_aBinaryLogPositionThatIsNotAFileAndOffsetIsRefused():
  with pytest.raises(ledger.LedgerError, match="is not a file name and a position"):
    ledger.binaryLogCommand("binlog.000002; rm -rf /", 4)
  with pytest.raises(ledger.LedgerError, match="is not a file name and a position"):
    ledger.binaryLogCommand("binlog.000002", "4")
