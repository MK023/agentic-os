# main.py reads these at import time; pytest loads conftest.py before test
# modules, so this must set them before `from main import app` runs anywhere.
import os
import sys
from pathlib import Path

os.environ.setdefault("PROMETHEUS_URL", "http://prometheus:9090")
os.environ.setdefault("STATUS_API_TOKEN", "test-token")

# CI runs pytest from the repo root (`pytest services/public-status-api/...`),
# where this directory is not on sys.path and `from main import app` would fail.
sys.path.insert(0, str(Path(__file__).parent))
