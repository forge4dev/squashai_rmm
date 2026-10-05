import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_FILE = (
    Path(os.environ.get("PROGRAMDATA", ".")) / "SquashRMM" / "ControlPlane" / "control-plane.env"
)


def _settings_files() -> tuple[str, ...]:
    """Use the machine config when this process is allowed to read it."""
    files: list[str] = []
    try:
        with DEFAULT_CONFIG_FILE.open("rb"):
            pass
    except OSError:
        # Developer shells and test runners normally cannot read the service's
        # ACL-protected configuration file. Environment variables still apply.
        pass
    else:
        files.append(str(DEFAULT_CONFIG_FILE))
    files.append(".env")
    return tuple(files)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SQUASH_",
        env_file=_settings_files(),
        extra="ignore",
    )

    environment: str = "development"
    database_url: str = "sqlite+pysqlite:///./squash-dev.db"
    api_key_sha256: str = Field(
        default="",
        description="Lowercase SHA-256 digest of the caller API key.",
    )
    max_script_bytes: int = 256 * 1024
    default_execution_timeout_seconds: int = 60
    max_execution_timeout_seconds: int = 300
    default_delivery_timeout_seconds: int = 30
    max_delivery_timeout_seconds: int = 300
    output_limit_bytes_per_stream: int = 1024 * 1024
    heartbeat_interval_seconds: int = 10
    heartbeat_grace_seconds: int = 35
    execution_timeout_grace_seconds: int = 15
    websocket_url: str = "/v1/agent/connect"
    public_base_url: str = "http://127.0.0.1:8000"
    listen_host: str = "127.0.0.1"
    listen_port: int = 8000
    tls_certificate_file: str = ""
    tls_private_key_file: str = ""
    data_encryption_key: str = ""
    api_rate_limit_per_minute: int = 240

    def validate_security(self) -> None:
        if self.environment.lower() != "development" and not self.api_key_sha256:
            raise RuntimeError("SQUASH_API_KEY_SHA256 is required outside development")
        if self.environment.lower() != "development":
            supported_database = self.database_url.startswith((
                "sqlite+pysqlite:///",
                "postgresql+psycopg://",
            ))
            if not supported_database or self.database_url.endswith(":memory:"):
                raise RuntimeError("Production requires a persistent SQLite or PostgreSQL database")
            if not self.public_base_url.startswith("https://"):
                raise RuntimeError("SQUASH_PUBLIC_BASE_URL must use HTTPS outside development")
            if not self.tls_certificate_file or not self.tls_private_key_file:
                raise RuntimeError("TLS certificate and private key files are required")
            if not self.data_encryption_key:
                raise RuntimeError("SQUASH_DATA_ENCRYPTION_KEY is required outside development")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_security()
    return settings
