import datetime
import json
import logging

logRetentionDays = 90
runPythonLoggerName = "runPython"


def logFiles(logDirectory, stem):
  """A log's files: each day's <stem>.<day>.log, and the <stem>.log and <stem>.log.<day> it was written to before it was kept by day."""
  return sorted(logDirectory.glob(f"{stem}.*"))


def pruneLogs(logDirectory, stem, cutoff):
  for path in logFiles(logDirectory, stem):
    # Another server process may have pruned the file just now, or still hold it open.
    try:
      if path.stat().st_mtime < cutoff:
        path.unlink()
    except (FileNotFoundError, PermissionError):
      pass


class DailyFileHandler(logging.FileHandler):
  """Appends each record to <stem>.<day>.log, the file of the day the clock gives when it is emitted, and never renames one: several
  server processes append to the same files, and Windows refuses to rename a file another process holds open. Opening a day's file
  prunes the log's files older than logRetentionDays."""

  def __init__(self, logDirectory, stem, formatter, clock=datetime.datetime.now):
    self.logDirectory, self.stem, self.clock = logDirectory, stem, clock
    self.day = clock().date()
    super().__init__(self.dayPath(), encoding="utf-8", delay=True)
    self.setFormatter(formatter)

  def dayPath(self):
    return self.logDirectory / f"{self.stem}.{self.day.isoformat()}.log"

  def emit(self, record):
    now = self.clock()
    if now.date() != self.day:
      self.day = now.date()
      self.baseFilename = str(self.dayPath())
      if self.stream is not None:
        self.stream.close()
        self.stream = None
    if self.stream is None:
      pruneLogs(self.logDirectory, self.stem, now.timestamp() - logRetentionDays * 86400)
    super().emit(record)


def configureLogging(toolingRoot):
  logDirectory = toolingRoot / "logs"
  logDirectory.mkdir(parents=True, exist_ok=True)
  rootLogger = logging.getLogger()
  rootLogger.handlers = [DailyFileHandler(logDirectory, "zonewright", logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))]
  rootLogger.setLevel(logging.INFO)
  runPythonLogger = logging.getLogger(runPythonLoggerName)
  runPythonLogger.handlers = [DailyFileHandler(logDirectory, "runPython", logging.Formatter("%(message)s"))]
  runPythonLogger.propagate = False


def recordRunPython(code):
  """One JSON line per runPython call, so repeated raw scripting can be spotted and turned into tools."""
  logging.getLogger(runPythonLoggerName).info(json.dumps({"code": code}))


def countRunPython(toolingRoot):
  logPaths = logFiles(toolingRoot / "logs", "runPython")
  return {"calls": sum(len(logPath.read_text(encoding="utf-8").splitlines()) for logPath in logPaths), "logFiles": [str(logPath) for logPath in logPaths]}
