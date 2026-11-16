from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./audit.db"
    log_level: str = "INFO"

    # Central auth service and this service's client credentials
    auth_url: str = "http://localhost:8001"
    client_id: str = "audit-service"
    client_secret: str = ""



settings = Settings()
