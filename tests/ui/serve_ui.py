"""Serve the dashboard on a throwaway database for tests/ui/ui_views.cjs."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASH = os.path.join(ROOT, "dashboard")
sys.path.insert(0, DASH)

import db.core as core  # noqa: E402

core.DB_PATH = os.environ["ESHU_UI_DB"]
os.chdir(DASH)
import main  # noqa: E402
import uvicorn  # noqa: E402

uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["ESHU_UI_PORT"]),
            log_level="error")
