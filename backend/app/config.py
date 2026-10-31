from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    db_path: Path = BACKEND_DIR / "data" / "app.db"
    # 새 세션이 다루는 카테고리 (categories/<id>.yaml, 카탈로그가 DB에 빌드되어 있어야 함)
    default_category: str = "headphones"
    # 빌드된 프론트엔드(npm run build 결과). index.html이 있으면 FastAPI가 같은 포트에서 함께 서비스한다.
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5-mini"
    # 추론 모델의 reasoning effort. 빈 값이면 보내지 않는다(비추론 모델용).
    openai_reasoning_effort: str | None = "low"
    # 질문 작성기(친구 말투 질문)의 모델과 reasoning effort. 모델이 비어 있으면 openai_model을 쓴다.
    # effort가 빈 값이면 보내지 않는다(비추론 모델용).
    openai_question_model: str | None = None
    openai_question_reasoning_effort: str | None = "low"
    openai_timeout: float = 30.0
    # 텍스트 관련도(동률 정렬)용 임베딩. 상품 임베딩(scripts/build_embeddings.py)과 같은 설정이어야 한다.
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 512


settings = Settings()
