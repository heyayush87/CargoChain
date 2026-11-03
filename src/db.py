import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

SCHEMA = "analytics"
VIEW_NAME = f"{SCHEMA}.vw_active_fleet"
AUDIT_TABLE = f"{SCHEMA}.agent_audit_log"


def _owner_url():
    raw = os.getenv("DATABASE_URL")
    if not raw:
        raise ValueError("CRITICAL: DATABASE_URL is missing from the environment / .env.")
    url = make_url(raw)
    # Neon hands out plain postgresql:// URLs; force the psycopg (v3) driver
    return url.set(drivername="postgresql+psycopg")


@lru_cache(maxsize=1)
def get_owner_engine() -> Engine:
    """Owner connection: used only for setup scripts and the admin audit viewer."""
    return create_engine(_owner_url(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_agent_engine() -> Engine:
    """Least-privilege connection for the agent: SELECT on the view, INSERT on the audit log."""
    user = os.getenv("AGENT_DB_USER", "agent_ro")
    password = os.getenv("AGENT_DB_PASSWORD")
    if not password:
        raise ValueError("CRITICAL: AGENT_DB_PASSWORD is missing from the environment / .env.")
    url = _owner_url().set(username=user, password=password)
    return create_engine(url, pool_pre_ping=True)
