
import os
import sys

# cPanel/shared hosts often default to ASCII; force UTF-8 for config and API text
os.environ.setdefault("LANG", "en_US.UTF-8")
os.environ.setdefault("LC_ALL", "en_US.UTF-8")
os.environ.setdefault("PYTHONUTF8", "1")

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

# Load .env early so SECRET_KEY and login credentials are available before app init
from dotenv import load_dotenv
load_dotenv(os.path.join(APP_ROOT, ".env"), override=True)

from reporting_dashboard.app import create_app

application = create_app()
