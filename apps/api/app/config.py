from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SA_", env_file=".env", extra="ignore")

    app_name: str = "shopping-agent"
    database_url: str = "postgresql://shopping:shopping@localhost:5432/shopping_agent"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    cors_origins: str = "*"
    mock_browser: bool = True
    log_level: str = "INFO"


settings = Settings()
