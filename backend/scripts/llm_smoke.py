"""실제 OpenAI API로 텍스트 응답과 구조화 응답을 한 번씩 호출해 본다.

    uv run python scripts/llm_smoke.py
"""

import logging
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import get_llm_client  # noqa: E402


class Preference(BaseModel):
    connection: str | None
    noise_cancelling: bool | None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    client = get_llm_client()

    reply = client.complete(
        [{"role": "user", "content": "한 문장으로 자기소개 해줘."}],
        instructions="너는 헤드폰 쇼핑 도우미다. 한국어로 짧게 답한다.",
    )
    print("complete:", reply)

    pref = client.parse(
        [{"role": "user", "content": "무선이고 노이즈 캔슬링 되는 걸로 찾아줘"}],
        Preference,
        instructions="사용자 발화에서 헤드폰 선호 조건을 추출한다. 언급이 없으면 null.",
    )
    print("parse:", pref)


if __name__ == "__main__":
    main()
