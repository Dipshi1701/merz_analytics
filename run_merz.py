"""Run the Merz Flask analytics dashboard."""

import sys
from pathlib import Path

APP_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(APP_ROOT))

from reporting_dashboard.app import create_app  # noqa: E402

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8501, debug=True)
