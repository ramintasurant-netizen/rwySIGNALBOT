"""Settings aplikasi dari environment/.env (pydantic-settings).

Default selalu aman: development, dry_run, live send nonaktif, LLM nonaktif,
provider berbayar nonaktif, WhatsApp hanya ekspor manual.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from core import redaction
from core.timeutil import parse_hhmm

AppEnv = Literal["development", "production"]
AppMode = Literal["dry_run", "live"]
LLMProvider = Literal["none", "openai", "anthropic", "openai_compatible"]

# Provider yang kontrak API-nya belum diverifikasi: adapter nonaktif; mengaktifkan = error konfigurasi.
UNVERIFIED_PROVIDERS: dict[str, str] = {
    "goapi": "GoAPI",
    "sectors": "Sectors",
    "broker_x": "Broker (read-only)",
}
KNOWN_PROVIDERS: frozenset[str] = frozenset({"yahoo", *UNVERIFIED_PROVIDERS})

_CHAT_TARGET_RE = re.compile(r"^(-?\d+)(?::(\d+))?$")


class ChatTarget(BaseModel):
    """Tujuan pengiriman Telegram: grup/supergroup/channel, opsional topik forum."""

    model_config = ConfigDict(frozen=True)

    chat_id: int
    thread_id: int | None = None

    @classmethod
    def parse(cls, raw: str) -> ChatTarget:
        match = _CHAT_TARGET_RE.match(raw.strip())
        if not match:
            raise ValueError(
                f"format tujuan tidak valid {raw!r}; gunakan <chat_id> atau <chat_id>:<thread_id>"
            )
        chat_id = int(match.group(1))
        if chat_id >= 0:
            # Format saja tidak cukup (verifikasi API tetap wajib), tetapi ID positif
            # jelas menunjuk pengguna/chat pribadi dan ditolak sedini mungkin.
            raise ValueError(
                f"chat_id {chat_id} positif menunjuk chat pribadi/pengguna; "
                "sinyal hanya dikirim ke grup/supergroup/channel (ID negatif)"
            )
        thread_id = int(match.group(2)) if match.group(2) else None
        return cls(chat_id=chat_id, thread_id=thread_id)

    def __str__(self) -> str:
        return (
            f"{self.chat_id}:{self.thread_id}" if self.thread_id is not None else str(self.chat_id)
        )


def parse_chat_targets(raw: str | None) -> tuple[ChatTarget, ...]:
    if not raw or not raw.strip():
        return ()
    targets = tuple(ChatTarget.parse(part) for part in raw.split(",") if part.strip())
    ids = [t.chat_id for t in targets]
    if len(ids) != len(set(ids)):
        raise ValueError("chat_id duplikat pada daftar tujuan")
    return targets


def _parse_int_list(raw: str | None, *, positive: bool) -> tuple[int, ...]:
    if not raw or not raw.strip():
        return ()
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not re.fullmatch(r"-?\d+", part):
            raise ValueError(f"nilai bukan bilangan bulat: {part!r}")
        value = int(part)
        if positive and value <= 0:
            raise ValueError(f"user_id harus positif: {value}")
        values.append(value)
    if len(values) != len(set(values)):
        raise ValueError("nilai duplikat")
    return tuple(values)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Aplikasi ---
    app_env: AppEnv = "development"
    app_mode: AppMode = "dry_run"
    app_timezone: str = "Asia/Jakarta"
    var_dir: Path = Path("var")
    config_dir: Path = Path("config")
    log_level: str = "INFO"
    log_diagnose: bool = False

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///var/dev.db"
    allow_sqlite_in_production: bool = False

    # --- Telegram ---
    telegram_bot_token: SecretStr | None = None
    telegram_enable_live_send: bool = False
    telegram_signal_chat_ids: str = ""
    telegram_admin_chat_id: str = ""
    telegram_admin_user_ids: str = ""
    telegram_command_cooldown_seconds: int = Field(default=60, ge=0, le=3600)

    # --- Jadwal (WIB) ---
    schedule_morning: str = "08:30"
    schedule_afternoon: str = "15:00"

    # --- LLM (opsional) ---
    llm_provider: LLMProvider = "none"
    llm_api_key: SecretStr | None = None
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    # --- Provider data ---
    provider_priority: str = "yahoo"
    production_single_provider_approved: bool = False
    goapi_enabled: bool = False
    goapi_api_key: SecretStr | None = None
    sectors_enabled: bool = False
    sectors_api_key: SecretStr | None = None
    broker_x_enabled: bool = False
    broker_x_api_key: SecretStr | None = None

    data_request_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    data_max_concurrency: int = Field(default=4, ge=1, le=32)
    data_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    data_max_retries: int = Field(default=2, ge=0, le=5)
    data_retry_backoff_seconds: float = Field(default=1.0, gt=0, le=60)
    data_breaker_failure_threshold: int = Field(default=3, ge=1, le=50)
    data_breaker_cooldown_seconds: int = Field(default=120, ge=1, le=3600)
    data_rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    cross_validation_max_diff_pct: Decimal = Field(default=Decimal("0.5"), gt=0, le=10)
    intraday_max_age_minutes: int = Field(default=20, ge=1, le=240)
    daily_min_history_bars: int = Field(default=250, ge=30, le=2000)

    # --- WhatsApp (hanya ekspor teks manual) ---
    whatsapp_export_enabled: bool = False

    # --- Sizing contoh (CONTOH; dikonfirmasi pada Tahap 3) ---
    sizing_capital_example: Decimal = Field(default=Decimal("100000000"), gt=0)
    sizing_risk_pct: Decimal = Field(default=Decimal("1.0"), gt=0, le=5)

    _signal_targets: tuple[ChatTarget, ...] = PrivateAttr(default=())
    _admin_target: ChatTarget | None = PrivateAttr(default=None)
    _admin_user_ids: tuple[int, ...] = PrivateAttr(default=())
    _provider_order: tuple[str, ...] = PrivateAttr(default=())
    _warnings: tuple[str, ...] = PrivateAttr(default=())

    # ----- validator per-field -----
    @field_validator("app_timezone")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"zona waktu tidak dikenal: {value!r}") from exc
        return value

    @field_validator("schedule_morning", "schedule_afternoon")
    @classmethod
    def _valid_hhmm(cls, value: str) -> str:
        parse_hhmm(value)
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"log_level tidak dikenal: {value!r}")
        return level

    # ----- validator kombinasi -----
    @model_validator(mode="after")
    def _validate_combinations(self) -> Settings:
        warnings: list[str] = []

        self._signal_targets = parse_chat_targets(self.telegram_signal_chat_ids)
        self._admin_target = (
            ChatTarget.parse(self.telegram_admin_chat_id)
            if self.telegram_admin_chat_id.strip()
            else None
        )
        self._admin_user_ids = _parse_int_list(self.telegram_admin_user_ids, positive=True)

        if self._admin_target is not None and self._admin_target.chat_id in {
            t.chat_id for t in self._signal_targets
        }:
            warnings.append(
                "TELEGRAM_ADMIN_CHAT_ID sama dengan salah satu TELEGRAM_SIGNAL_CHAT_IDS; "
                "alert operasional akan terlihat oleh anggota grup sinyal"
            )
        if self._admin_target is None:
            warnings.append(
                "TELEGRAM_ADMIN_CHAT_ID kosong: alert admin hanya dicatat ke log (tidak ada fallback ke chat pribadi)"
            )

        token = self.telegram_bot_token.get_secret_value() if self.telegram_bot_token else ""
        if token and not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", token):
            raise ValueError("TELEGRAM_BOT_TOKEN tidak sesuai format token BotFather")

        if self.app_mode == "live":
            problems: list[str] = []
            if not token:
                problems.append("TELEGRAM_BOT_TOKEN kosong")
            if not self._signal_targets:
                problems.append("TELEGRAM_SIGNAL_CHAT_IDS kosong")
            if not self.telegram_enable_live_send:
                problems.append("TELEGRAM_ENABLE_LIVE_SEND=false")
            if problems:
                raise ValueError("APP_MODE=live membutuhkan: " + "; ".join(problems))
        elif self.telegram_enable_live_send:
            warnings.append("TELEGRAM_ENABLE_LIVE_SEND=true diabaikan karena APP_MODE=dry_run")

        if self.app_env == "production" and self.database_url.startswith("sqlite"):
            if not self.allow_sqlite_in_production:
                raise ValueError(
                    "APP_ENV=production membutuhkan PostgreSQL (DATABASE_URL=postgresql+asyncpg://...); "
                    "set ALLOW_SQLITE_IN_PRODUCTION=true hanya jika Anda sadar risikonya"
                )
            warnings.append("SQLite dipakai di production atas persetujuan eksplisit")

        for key, display in UNVERIFIED_PROVIDERS.items():
            if getattr(self, f"{key}_enabled"):
                raise ValueError(
                    f"{key.upper()}_ENABLED=true ditolak: kontrak API {display} belum diverifikasi, "
                    "adapter masih template nonaktif. Lengkapi docs/verification_required.md dan "
                    "implementasikan adapter sebelum mengaktifkan."
                )

        order = tuple(p.strip().lower() for p in self.provider_priority.split(",") if p.strip())
        if not order:
            raise ValueError("PROVIDER_PRIORITY kosong")
        if len(order) != len(set(order)):
            raise ValueError("PROVIDER_PRIORITY memuat duplikat")
        for name in order:
            if name not in KNOWN_PROVIDERS:
                raise ValueError(f"provider tidak dikenal pada PROVIDER_PRIORITY: {name!r}")
            if name in UNVERIFIED_PROVIDERS and not getattr(self, f"{name}_enabled"):
                raise ValueError(
                    f"provider {name!r} ada di PROVIDER_PRIORITY tetapi nonaktif (template belum diverifikasi)"
                )
        self._provider_order = order
        if len(order) == 1:
            warnings.append(
                "Hanya satu provider aktif: kualitas data maksimal 'degraded' (tanpa cross-validation); "
                "produksi memerlukan PRODUCTION_SINGLE_PROVIDER_APPROVED=true"
            )

        if self.llm_provider != "none":
            if self.llm_api_key is None and self.llm_provider != "openai_compatible":
                raise ValueError(f"LLM_PROVIDER={self.llm_provider} membutuhkan LLM_API_KEY")
            if self.llm_provider == "openai_compatible" and not self.llm_base_url:
                raise ValueError("LLM_PROVIDER=openai_compatible membutuhkan LLM_BASE_URL")
            if not self.llm_model:
                raise ValueError("LLM_PROVIDER aktif membutuhkan LLM_MODEL")

        self._warnings = tuple(warnings)
        return self

    # ----- akses turunan -----
    @property
    def signal_targets(self) -> tuple[ChatTarget, ...]:
        return self._signal_targets

    @property
    def admin_target(self) -> ChatTarget | None:
        return self._admin_target

    @property
    def admin_user_ids(self) -> tuple[int, ...]:
        return self._admin_user_ids

    @property
    def provider_order(self) -> tuple[str, ...]:
        return self._provider_order

    @property
    def config_warnings(self) -> tuple[str, ...]:
        return self._warnings

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_dry_run(self) -> bool:
        return self.app_mode == "dry_run"

    @property
    def live_send_allowed(self) -> bool:
        return (
            self.app_mode == "live"
            and self.telegram_enable_live_send
            and self.telegram_bot_token is not None
            and bool(self._signal_targets)
        )

    @property
    def requires_cross_validation(self) -> bool:
        return self.is_production and not self.production_single_provider_approved

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        ids = {t.chat_id for t in self._signal_targets}
        if self._admin_target is not None:
            ids.add(self._admin_target.chat_id)
        return frozenset(ids)

    def register_secrets(self) -> None:
        """Daftarkan semua rahasia agar diredaksi dari log/exception."""
        for secret in (
            self.telegram_bot_token,
            self.llm_api_key,
            self.goapi_api_key,
            self.sectors_api_key,
            self.broker_x_api_key,
        ):
            if secret is not None:
                redaction.registry.register(secret.get_secret_value())

    def sanitized_summary(self) -> dict[str, object]:
        """Ringkasan konfigurasi tanpa rahasia untuk tampilan/log."""
        return {
            "app_env": self.app_env,
            "app_mode": self.app_mode,
            "live_send_allowed": self.live_send_allowed,
            "database": self.database_url.split("://", 1)[0],
            "telegram_token_configured": self.telegram_bot_token is not None,
            "signal_targets": [str(t) for t in self._signal_targets],
            "admin_target": str(self._admin_target) if self._admin_target else None,
            "admin_user_count": len(self._admin_user_ids),
            "schedule_wib": {
                "morning": self.schedule_morning,
                "afternoon": self.schedule_afternoon,
            },
            "llm_provider": self.llm_provider,
            "provider_order": list(self._provider_order),
            "requires_cross_validation": self.requires_cross_validation,
            "whatsapp_export_enabled": self.whatsapp_export_enabled,
            "warnings": list(self._warnings),
        }


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    settings.register_secrets()
    return settings
