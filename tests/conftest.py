"""Shared isolated test environment.

Tests must never read from or write to the developer's real RDC home.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_RDC_HOME = Path(tempfile.mkdtemp(prefix="rdc-tests-"))
os.environ["RDC_HOME"] = str(TEST_RDC_HOME)

from remote_dev_ctrl.server.db import init_databases  # noqa: E402

init_databases()


@pytest.fixture(scope="session", autouse=True)
def isolated_rdc_home():
    yield TEST_RDC_HOME
    from remote_dev_ctrl.server.db import close_databases

    close_databases()
    shutil.rmtree(TEST_RDC_HOME, ignore_errors=True)
