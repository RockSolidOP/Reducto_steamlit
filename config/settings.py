# config/settings.py
from __future__ import annotations
from typing import Optional, Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
import tomllib
from dotenv import load_dotenv

# Project root: .../Reducto_steamlit
BASE_DIR = Path(__file__).resolve().parents[1]

# Force-load .env from project root, then fall back to CWD
load_dotenv(BASE_DIR / ".env")
load_dotenv()  # no-op if already loaded

class Settings(BaseSettings):
    # app
    app_storage_dir: str = Field(default="./storage")
    default_provider: str = Field(default="reducto")

    # provider creds
    reducto_api_key: Optional[str] = None  # reads REDUCTO_API_KEY

    # pydantic v2 settings config
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
        env_file=[str(BASE_DIR / ".env"), ".env"],  # try both absolute and CWD .env
        env_file_encoding="utf-8",
    )

def load_settings() -> Settings:
    cfg_path = BASE_DIR / "config" / "app.toml"
    data: Dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
    # TOML defaults + env override
    return Settings(**data.get("app", {}), **data.get("keys", {}))
