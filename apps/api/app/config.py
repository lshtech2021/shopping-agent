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
    mock_browser: bool = True

    # Live-browse pacing, enforced server-side per site (design §6).
    browse_min_interval_s: float = 2.0
    browse_jitter_s: float = 0.6
    job_timeout_s: float = 60.0
    fx_rates: dict[str, float] = {
        "CNY": 1.0,
        "USD": 7.2,
        "EUR": 7.8,
        "GBP": 9.1,
    }


settings = Settings()
