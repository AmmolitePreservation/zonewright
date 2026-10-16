import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from ledgerDoubles import FakeHost, makeRepository  # noqa: E402


def pytest_configure(config):
  config.addinivalue_line("markers", "mariadb: runs against claudeperidot's real MariaDB, only when facet's inventory holds "
    "claudeperidot")


@pytest.fixture
def repository(tmp_path):
  """An empty claudeperidot folder (plan.md and changes still to write) in a git repository of its own on branch claude,
  with one empty commit and a bare remote it pushes to."""
  makeRepository(tmp_path / "repository")
  folder = tmp_path / "repository" / "claudeperidot"
  (folder / "changes").mkdir(parents=True)
  return folder


@pytest.fixture
def host(tmp_path):
  """claudeperidot's shell over tmp_path/host, standing in for the host's /opt/eqemu."""
  return FakeHost(tmp_path / "host")
