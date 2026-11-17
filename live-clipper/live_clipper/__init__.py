"""Live Twitch Clipper package."""
import sys
from pathlib import Path

# Auto-register project virtual environment site-packages if running with external Python interpreter
_venv_site = Path(__file__).resolve().parent.parent / ".venv" / "lib" / "python3.11" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

__version__ = "1.0.0"
