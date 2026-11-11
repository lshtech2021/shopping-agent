from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SA_", env_file=".env", extra="ignore")

    app_name: str = "shopping-agent"
    database_url: str = "postgresql://shopping:shopping@localhost:5432/shopping_agent"
    log_level: str = "INFO"

    # LLM (OpenAI-compatible). Required — empty key interrupts turns with llm_unavailable.
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    agent_max_steps: int = 14
    agent_turn_timeout_s: float = 600.0

    cors_origins: str = "http://localhost:8100,http://127.0.0.1:8100"
    extension_token: str = ""
    extension_id: str = ""
    # When true, reject requests without a matching SA_EXTENSION_TOKEN
    # (including session GETs). Local/dev defaults to open when token is empty.
    auth_required: bool = False
    # When true, RunRepository creates tables on connect. Prefer Alembic
    # (`alembic upgrade head`) and leave this false outside quick local hacks.
    auto_create_schema: bool = False
    # 0 = one shared psycopg connection; >0 opens psycopg_pool.ConnectionPool.
    pg_pool_size: int = 0
    mock_browser: bool = True

    # Live-browse pacing, enforced server-side per site (design §6).
    browse_min_interval_s: float = 2.0
    browse_jitter_s: float = 0.6
    job_timeout_s: float = 60.0

    # Shipping caps. Ratio is relative to item price (0.10 = 10%); the
    # absolute cap is in the session currency. A session may override either
    # value, including clearing it with an explicit null.
    max_shipping_ratio: float = 0.10
    max_shipping_cost: float | None = None

    # Optional audit/observability features.
    artifact_storage_enabled: bool = False
    artifact_threshold_chars: int = 20000
    metrics_enabled: bool = False
    otel_enabled: bool = False
    otel_service_name: str = "shopping-agent"
    log_redaction_enabled: bool = True
    log_retention_days: int = 0


settings = Settings()
