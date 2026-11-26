from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://raguser:ragpassword@localhost:5432/maintenancedb"
    ollama_base_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text"
    llm_model: str = "llama3"
    hmac_secret_key: str = "baykar-saha-acil-durum-gizli-anahtar-2026"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()