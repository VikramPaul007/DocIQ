"""App configuration. Fails fast at startup if a secret is missing or a placeholder."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _reject_placeholder(value: str, field_name: str) -> str:
    if not value or value.upper().startswith("CHANGEME"):
        raise ValueError(f"{field_name} is missing or still a placeholder value")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "dociq"
    postgres_owner_user: str
    postgres_owner_password: str
    postgres_app_user: str
    postgres_app_password: str
    postgres_worker_user: str
    postgres_worker_password: str

    session_secret: str
    document_encryption_key: str

    minio_endpoint: str
    minio_root_user: str
    minio_root_password: str
    minio_bucket: str = "dociq-documents"
    minio_secure: bool = False

    llm_base_url: str
    llm_api_key: str
    llm_model: str = "gpt-4o-mini"

    @field_validator(
        "postgres_owner_password",
        "postgres_app_password",
        "postgres_worker_password",
        "session_secret",
        "document_encryption_key",
        "minio_root_password",
        "llm_api_key",
    )
    @classmethod
    def _no_placeholders(cls, v: str, info) -> str:  # type: ignore[no-untyped-def]
        return _reject_placeholder(v, info.field_name)

    @field_validator("session_secret", "document_encryption_key")
    @classmethod
    def _must_be_32_bytes_hex(cls, v: str, info) -> str:  # type: ignore[no-untyped-def]
        try:
            raw = bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError(f"{info.field_name} must be a hex string") from exc
        if len(raw) != 32:
            raise ValueError(f"{info.field_name} must decode to exactly 32 bytes")
        return v

    @property
    def owner_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_owner_user}:{self.postgres_owner_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def owner_conninfo(self) -> str:
        """Plain (non-SQLAlchemy) libpq connection string, for raw psycopg use
        (role bootstrap needs psycopg.sql for safe identifier/literal quoting)."""
        return (
            f"postgresql://{self.postgres_owner_user}:{self.postgres_owner_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def app_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_app_user}:{self.postgres_app_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def worker_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_worker_user}:{self.postgres_worker_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()  # type: ignore[call-arg]  # populated from env/.env at import time
