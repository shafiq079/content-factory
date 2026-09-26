"""Backend package bootstrap.

Loads backend/.env once before provider modules read environment variables.
Process-level environment variables still take precedence.
"""
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_ROOT / ".env", override=False)
