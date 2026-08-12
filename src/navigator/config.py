"""Application settings.

Settings are read from environment variables and an optional ``.env`` file.
Secrets are typed ``SecretStr`` and must never appear in logs or exception
messages.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # Database
    oracle_tns_admin: Path
    oracle_dsn: str
    oracle_user: str
    oracle_password: SecretStr
    oracle_wallet_password: SecretStr

    # OCI (declared now, used in later phases)
    oci_tenancy: str | None = None
    oci_user: str | None = None
    oci_fingerprint: str | None = None
    oci_region: str | None = None
    oci_compartment_id: str | None = None
    oci_key_file: Path | None = None

    # Data sources
    careeronestop_user_id: str | None = None
    careeronestop_token: SecretStr | None = None
    scorecard_api_key: SecretStr | None = None
    bls_api_key: SecretStr | None = None

    # Application
    demo_mode: bool = True
    log_level: str = "INFO"
    pool_min: int = 1
    pool_max: int = 4
    pool_increment: int = 1

    @field_validator("oracle_tns_admin")
    @classmethod
    def _wallet_dir_is_usable(cls, value: Path) -> Path:
        if not value.is_dir():
            raise ValueError(f"wallet directory does not exist: {value}")
        for required in ("tnsnames.ora", "ewallet.pem"):
            if not (value / required).is_file():
                raise ValueError(f"wallet directory {value} is missing required file: {required}")
        return value


@lru_cache
def get_settings() -> Settings:
    """Construct settings once per process."""
    # Fields are populated from the environment; mypy cannot see that.
    return Settings()  # type: ignore[call-arg]
