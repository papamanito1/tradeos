from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    # App
    app_name: str = "TradeOS"
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"

    # Security
    secret_key: str = "INSECURE_CHANGE_ME"
    access_token_expire_minutes: int = 480
    algorithm: str = "HS256"

    # Admin seed credentials
    admin_username: str = "Kashan"
    admin_password: str = "Manan"

    # Database
    database_url: str = "sqlite+aiosqlite:///./tradeos.db"

    # Redis
    redis_url: str = "redis://:redis_secret@localhost:6379/0"

    # CORS (comma-separated list of allowed origins, or * for all)
    cors_origins: str = ""

    # Trading
    trading_mode: Literal["paper", "live"] = "paper"
    allow_live_trading: int = 0  # 0 = disabled, 1 = enabled

    # Exchange
    exchange_id: str = "binance"
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    exchange_testnet: bool = True
    use_mock_exchange: int = 1

    class Config:
        env_file = (".env", "../.env")
        extra = "ignore"


settings = Settings()
