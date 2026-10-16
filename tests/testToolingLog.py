import datetime
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import toolingLog


class Clock:
  def __init__(self, now):
    self.now = now

  def __call__(self):
    return self.now


def dailyHandler(folder, stem, clock):
  return toolingLog.DailyFileHandler(folder, stem, logging.Formatter("%(message)s"), clock)


def emit(handler, message):
  handler.handle(logging.makeLogRecord({"msg": message}))


def linesOf(path):
  return path.read_text(encoding="utf-8").splitlines()


def lastWrittenOn(path, day):
  written = datetime.datetime.combine(day, datetime.time(23)).timestamp()
  os.utime(path, (written, written))


def testTwoHandlersAppendToEachDaysFileAcrossADayChangeWithoutLoss(tmp_path):
  clock = Clock(datetime.datetime(2026, 10, 15, 23, 59, 59))
  first, second = dailyHandler(tmp_path, "zonewright", clock), dailyHandler(tmp_path, "zonewright", clock)
  for handler, message in ((first, "first 1"), (second, "second 1"), (first, "first 2")):
    emit(handler, message)
  clock.now = datetime.datetime(2026, 10, 16, 0, 0, 1)
  # The second moves to the new day's file while the first still holds the old day's open, where a rename would fail.
  for handler, message in ((second, "second 2"), (first, "first 3"), (second, "second 3")):
    emit(handler, message)
  first.close()
  second.close()
  assert sorted(path.name for path in tmp_path.iterdir()) == ["zonewright.2026-10-15.log", "zonewright.2026-10-16.log"]
  assert linesOf(tmp_path / "zonewright.2026-10-15.log") == ["first 1", "second 1", "first 2"]
  assert linesOf(tmp_path / "zonewright.2026-10-16.log") == ["second 2", "first 3", "second 3"]


def testOpeningADaysFilePrunesTheLogsFilesPastRetentionButOneHeldOpen(tmp_path):
  now = datetime.datetime(2026, 10, 15, 12, 0)
  aged = ["zonewright.2026-07-16.log", "zonewright.log.2026-07-15", "zonewright.log", "zonewright.2026-07-14.log", "runPython.2026-07-16.log"]
  for name in aged:
    (tmp_path / name).write_text("old\n", encoding="utf-8")
    lastWrittenOn(tmp_path / name, now.date() - datetime.timedelta(days=toolingLog.logRetentionDays + 1))
  (tmp_path / "zonewright.2026-07-18.log").write_text("kept\n", encoding="utf-8")
  lastWrittenOn(tmp_path / "zonewright.2026-07-18.log", now.date() - datetime.timedelta(days=toolingLog.logRetentionDays - 1))
  handler = dailyHandler(tmp_path, "zonewright", Clock(now))
  with open(tmp_path / "zonewright.2026-07-14.log", encoding="utf-8"):
    emit(handler, "today")
  handler.close()
  assert sorted(path.name for path in tmp_path.iterdir()) == [
    "runPython.2026-07-16.log", "zonewright.2026-07-14.log", "zonewright.2026-07-18.log", "zonewright.2026-10-15.log",
  ]
  assert linesOf(tmp_path / "zonewright.2026-10-15.log") == ["today"]


def testRunPythonCallsAreCountedAcrossTheDailyFilesAndTheRotatedOnesBeforeThem(tmp_path):
  logs = tmp_path / "logs"
  logs.mkdir()
  for name, calls in (("runPython.log.2026-10-11", 2), ("runPython.log", 3), ("zonewright.2026-10-15.log", 4)):
    (logs / name).write_text("".join(json.dumps({"code": f"result = {call}"}) + "\n" for call in range(calls)), encoding="utf-8")
  handler = dailyHandler(logs, "runPython", Clock(datetime.datetime(2026, 10, 15, 9, 0)))
  emit(handler, json.dumps({"code": "result = 1"}))
  handler.close()
  assert toolingLog.countRunPython(tmp_path) == {
    "calls": 6, "logFiles": [str(logs / name) for name in ("runPython.2026-10-15.log", "runPython.log", "runPython.log.2026-10-11")],
  }
