from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./customer.db"
    log_level: str = "INFO"

    # Central auth service and this service's client credentials
    auth_url: str = "http://localhost:8001"
    client_id: str = "customer-service"
    client_secret: str = ""

    # Audit service base URL; empty disables shipping (events stay in the outbox)
    audit_url: str = ""


settings = Settings()
