"""상품 설명 임베딩을 만든다 (동률 정렬의 텍스트 관련도, 실제 API 호출).

    uv run python scripts/load_catalog.py headphones     # 먼저 카탈로그를 SQLite에 넣는다
    uv run python scripts/build_embeddings.py headphones

글(상품명 + 특징 + 세부 정보)이 바뀌었거나 모델·차원이 다른 상품만 다시 만든다.
4,628개 처음 만들 때 약 2백만 토큰(text-embedding-3-small, 약 $0.04).
임베딩이 없으면 동률 정렬은 BM25만 쓴다.

@see docs/architecture.md#1-한눈에-보기
@see docs/architecture.md#5-데이터-모델-sqlite
@see docs/ig-engine.md#4-추천과-한계-표시
"""

import argparse
import sys
from contextlib import closing
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.llm import get_llm_client  # noqa: E402
from app.services.relevance import read_product_texts, text_hash  # noqa: E402

BATCH = 128


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("category")
    args = parser.parse_args()
    model, dims = settings.openai_embedding_model, settings.openai_embedding_dimensions

    init_db(settings.db_path)
    with closing(connect(settings.db_path)) as conn:
        texts = read_product_texts(conn, args.category)
        if not texts:
            sys.exit("카탈로그 없음. scripts/load_catalog.py를 먼저 실행한다")
        have = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT product_id, model, dimensions, text_hash FROM product_embeddings")}
        todo = [pid for pid, text in texts.items() if have.get(pid) != (model, dims, text_hash(text))]
        print(f"{len(texts)}개 중 {len(todo)}개 임베딩 ({model}, {dims}차원)")
        client = get_llm_client()
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            vectors = client.embed([texts[p] for p in batch], model=model, dimensions=dims)
            conn.executemany(
                "INSERT OR REPLACE INTO product_embeddings (product_id, model, dimensions, text_hash, vector) "
                "VALUES (?, ?, ?, ?, ?)",
                [(p, model, dims, text_hash(texts[p]), np.asarray(v, dtype=np.float32).tobytes())
                 for p, v in zip(batch, vectors)])
            conn.commit()
            print(f"  {min(start + BATCH, len(todo))}/{len(todo)}")


if __name__ == "__main__":
    main()
