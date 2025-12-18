from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # For dev: local SQLite file
    DATABASE_URL: str = "sqlite:///./flipforge.db"

    class Config:
        env_file = ".env"


settings = Settings()
