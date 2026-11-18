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

if os.environ.get("ESHU_UI_SYNTHETIC_HA") == "1":
    # The browser check cannot reach a real Home Assistant. Swap only the
    # read paths the X11 views use; no production code branches on this.
    sys.path.insert(0, os.path.join(ROOT, "tests", "ui"))
    import synthetic_ha  # noqa: E402
    from core import allowances as _allowances  # noqa: E402
    from core import ha_devices as _ha_devices  # noqa: E402
    _ha_devices.devices = synthetic_ha.devices
    _ha_devices.labels = synthetic_ha.labels
    _allowances._ha_registry = synthetic_ha.registry
    _allowances._risky_classes = synthetic_ha.risky_classes

uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["ESHU_UI_PORT"]),
            log_level="error")
