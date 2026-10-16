"""Pre-flight check for a laptop: Python, packages, data folder, .env and (optionally)
a live Gemini call.

Usage:  python scripts/check_setup.py            (no network call)
        python scripts/check_setup.py --test-ai  (one tiny Gemini request)
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PACKAGES = [("streamlit", "streamlit"), ("sqlalchemy", "SQLAlchemy"), ("pydantic", "pydantic"),
            ("pandas", "pandas"), ("plotly", "plotly"), ("dotenv", "python-dotenv"),
            ("google.genai", "google-genai")]


def line(ok: bool, text: str, optional: bool = False) -> bool:
    mark = "OK" if ok else ("--" if optional else "!!")
    print(f"  [{mark}] {text}")
    return ok


def main() -> int:
    print("STOCKSENSE setup check")
    good = line(sys.version_info >= (3, 11),
                f"Python {sys.version.split()[0]} (3.11 or newer required)")
    for module, name in PACKAGES:
        try:
            mod = importlib.import_module(module)
            good &= line(True, f"{name} {getattr(mod, '__version__', '')}".strip())
        except ImportError:
            good &= line(False, f"{name} missing -> run: pip install -r requirements.txt")
    if not good:
        return 1

    from stocksense.config import load_config
    from stocksense.database import Database
    cfg = load_config()
    try:
        db = Database(cfg.database_url)
        db.init_schema()
        line(True, f"Database ready: {cfg.db_path}")
        line(True, "Administrator exists" if db.has_users()
             else "No administrator yet (the app will ask you to create one)")
    except Exception as exc:  # noqa: BLE001 - reported to the user
        line(False, f"Database problem: {getattr(exc, 'user_message', exc)}")
        return 1

    env = ROOT / ".env"
    line(env.exists(), ".env file found" if env.exists()
         else ".env not found (optional: AI review stays off; copy .env.example to .env)",
         optional=True)
    line(cfg.gemini_configured, f"Gemini key {'set' if cfg.gemini_configured else 'not set (optional)'}; "
                                f"model {cfg.gemini_model}", optional=True)
    if "--test-ai" in sys.argv and cfg.gemini_configured:
        from stocksense.services.ai_service import AIReviewService, test_connection
        result = test_connection(AIReviewService(cfg))
        line(result["ok"], result["message"], optional=True)
        if not result["ok"]:
            print("  Core features still work; only AI review is affected.")
    print("Setup check finished. Start the app with: streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
