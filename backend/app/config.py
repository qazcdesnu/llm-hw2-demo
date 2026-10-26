from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    db_path: Path = BACKEND_DIR / "data" / "app.db"
    # 새 세션이 다루는 카테고리 (categories/<id>.yaml, 카탈로그가 DB에 빌드되어 있어야 함)
    default_category: str = "headphones"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5-mini"
    # 추론 모델의 reasoning effort. 빈 값이면 보내지 않는다(비추론 모델용).
    openai_reasoning_effort: str | None = "low"
    openai_timeout: float = 30.0


settings = Settings()
