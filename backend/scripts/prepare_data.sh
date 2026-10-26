#!/usr/bin/env bash
# 원본 다운로드 → 카테고리 필터링 → 카탈로그 빌드를 한 번에 실행한다.
#
#   cd backend && scripts/prepare_data.sh [카테고리 id]     # 기본 headphones
#
# - 원본: Amazon Reviews 2023 메타 파일 (어떤 파일인지는 categories/<id>.yaml의 source.raw_file)
# - 이미 받은 파일은 크기를 HuggingFace와 비교해 같으면 건너뛰고, 다르면 이어받는다.
# - 결과: data/interim/<id>.jsonl, SQLite(data/app.db), data/catalog/<id>.json
#
# @see README.md#데이터-준비
set -euo pipefail

CATEGORY="${1:-headphones}"
cd "$(dirname "$0")/.."

HF_REPO="McAuley-Lab/Amazon-Reviews-2023"
RAW_FILE=$(uv run python -c "from app.catalog.spec import load_category_spec as f; print(f('$CATEGORY').source.raw_file)")
URL="https://huggingface.co/datasets/$HF_REPO/resolve/main/raw/meta_categories/$RAW_FILE"
DEST="data/raw/$RAW_FILE"

echo "== [1/3] 원본 다운로드: $RAW_FILE"
EXPECTED=$(curl -sf "https://huggingface.co/api/datasets/$HF_REPO/tree/main/raw/meta_categories" \
  | python3 -c "import json,sys; print(next(f['size'] for f in json.load(sys.stdin) if f['path'].endswith('/$RAW_FILE')))")
mkdir -p data/raw
local_size() { if [ -f "$DEST" ]; then wc -c < "$DEST" | tr -d ' '; else echo 0; fi; }

if [ "$(local_size)" = "$EXPECTED" ]; then
  echo "   이미 받음 ($EXPECTED bytes)"
else
  printf '   받는 중 (%s / %s bytes, 끊기면 이어받음)\n' "$(local_size)" "$EXPECTED"
  for attempt in 1 2 3 4 5; do
    curl -L -C - --fail --retry 5 --retry-delay 3 -o "$DEST" "$URL" && break
    echo "   시도 $attempt 실패, 이어받기 재시도"
  done
  if [ "$(local_size)" != "$EXPECTED" ]; then
    echo "   크기 불일치: $(local_size) != $EXPECTED" >&2
    exit 1
  fi
fi

echo "== [2/3] 카테고리 필터링"
uv run python scripts/filter_category.py "$CATEGORY"

echo "== [3/3] 카탈로그 빌드 (후보 필터 → 속성 추출 → 샘플링 → SQLite + JSON)"
uv run python scripts/build_catalog.py "$CATEGORY"
