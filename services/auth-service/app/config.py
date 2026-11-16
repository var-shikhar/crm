from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./auth.db"
    log_level: str = "INFO"

    # Tokens
    issuer: str = "http://localhost:8080/auth"
    audience: str = "crm"
    jwt_private_key_path: str = "./keys/jwt_private.pem"
    access_token_ttl: int = 900  # 15 min
    refresh_token_ttl: int = 7 * 24 * 3600
    service_token_ttl: int = 600

    # Used to encrypt TOTP secrets at rest. Must be set to a random value in production.
    secret_key: str = "dev-only-change-me"

    # Login protection
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    password_min_length: int = 10

    # MFA
    mfa_issuer: str = "CRM"
    mfa_challenge_ttl: int = 300
    mfa_max_attempts: int = 5

    # Email (OTP delivery). backend: smtp | console | memory
    email_backend: str = "console"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False
    email_from: str = "CRM Security <no-reply@crm.example.com>"

    # First administrator, created on first start if no such user exists.
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "System Administrator"

    # Confidential service clients (secrets come from the environment)
    customer_service_client_secret: str = ""
    audit_service_client_secret: str = ""

    # Audit service base URL; empty disables shipping (events stay in the outbox)
    audit_url: str = ""


settings = Settings()
