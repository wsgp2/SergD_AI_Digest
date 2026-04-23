"""Конфиг из .env."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
PHONE = os.environ["PHONE"]
SESSION_NAME = os.environ.get("SESSION_NAME", "session")
SESSION_PATH = str(BASE_DIR / SESSION_NAME)

DIGEST_RECIPIENT_ID = int(os.environ["DIGEST_RECIPIENT_ID"])
DIGEST_MODEL = os.environ.get("DIGEST_MODEL", "opus")
CHATLIST_INVITE = os.environ.get("CHATLIST_INVITE", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TIMEZONE = os.environ.get("TIMEZONE", "UTC")
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "8"))

DB_PATH = str(BASE_DIR / "digest.db")
SCHEMA_PATH = BASE_DIR / "db_schema.sql"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
