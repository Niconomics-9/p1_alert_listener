"""
main.py – P1 Alert Listener entry point.

Run with:
    python main.py

Or after packaging:
    P1AlertListener.exe
"""
import sys
import os

# Ensure the project root is on the path (needed for PyInstaller one-file builds)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env if present (python-dotenv is optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main() -> None:
    from app import run
    run()


if __name__ == "__main__":
    main()
