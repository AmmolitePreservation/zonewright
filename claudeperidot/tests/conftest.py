import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from ledgerDoubles import git  # noqa: E402


@pytest.fixture
def repository(tmp_path):
  """An empty claudeperidot folder (plan.md and changes still to write) in a git repository of its own on branch claude,
  with one empty commit."""
  root = tmp_path / "repository"
  root.mkdir()
  git(root, "init", "-q", "-b", "claude")
  git(root, "config", "user.name", "Ledger Test")
  git(root, "config", "user.email", "ledger@test.invalid")
  git(root, "config", "core.autocrlf", "false")
  git(root, "commit", "-q", "--allow-empty", "-m", "Empty repository")
  folder = root / "claudeperidot"
  (folder / "changes").mkdir(parents=True)
  return folder
