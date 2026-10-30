"""Amazon-C4 평가 데이터를 data/raw/amazon-c4/에 받는다 (git 제외).

Amazon-C4: Amazon Reviews 2023의 5점 리뷰를 ChatGPT가 1인칭 요구 문장으로 바꿔 쓴 질의와,
그 리뷰의 상품(정답, parent_asin)의 쌍. 질의 21,223개. BLaIR 논문(arXiv 2403.03952)과 함께 공개.
https://huggingface.co/datasets/McAuley-Lab/Amazon-C4

    uv run python scripts/download_amazon_c4.py                   # 질의(test.csv) + README
    uv run python scripts/download_amazon_c4.py --with-item-pool  # BLaIR 평가용 상품 풀(643MB)까지

- 데이터셋 버전(커밋)을 고정해 받는다. 다른 버전은 --revision.
- 받은 파일은 크기와 해시(LFS 파일은 sha256, 그 외는 git blob sha1)로 검증한다.
- 이미 받은 파일은 해시가 같으면 건너뛰고, 받다가 끊긴 파일은 이어받는다.
- 받은 버전·파일·해시를 MANIFEST.json에 남긴다.

@see README.md#평가-데이터-amazon-c4-선택
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ID = "McAuley-Lab/Amazon-C4"
# 2024-04-09 커밋. 데이터셋이 바뀌어도 같은 데이터를 받도록 고정한다.
REVISION = "39322697749a88d179f88d322a2fe4765b655c98"
DEST = Path(__file__).resolve().parent.parent / "data" / "raw" / "amazon-c4"

QUERY_FILES = ["README.md", "test.csv"]
ITEM_POOL_FILE = "sampled_item_metadata_1M.jsonl"
CHUNK = 1 << 20


def api_tree(revision: str) -> dict[str, dict]:
    url = f"https://huggingface.co/api/datasets/{REPO_ID}/tree/{revision}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return {f["path"]: f for f in json.load(resp) if f["type"] == "file"}


def file_digest(path: Path, entry: dict) -> str:
    """HuggingFace 목록의 해시와 같은 방식으로 계산한다. LFS: sha256, 일반: git blob sha1."""
    if entry.get("lfs"):
        h = hashlib.sha256()
    else:
        h = hashlib.sha1()
        h.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def expected_digest(entry: dict) -> str:
    return entry["lfs"]["oid"] if entry.get("lfs") else entry["oid"]


def download(name: str, entry: dict, revision: str) -> None:
    path = DEST / name
    size = entry["size"]
    have = path.stat().st_size if path.exists() else 0
    if have == size and file_digest(path, entry) == expected_digest(entry):
        print(f"  {name}: 이미 받음 ({size:,} bytes)")
        return
    if have >= size:  # 크기가 같은데 해시가 다르거나 더 크면 처음부터
        path.unlink()
        have = 0

    url = f"https://huggingface.co/datasets/{REPO_ID}/resolve/{revision}/{name}"
    request = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(request, timeout=60) as resp:
        if have and resp.status != 206:  # 서버가 이어받기를 지원하지 않으면 처음부터
            have = 0
        mode = "ab" if have else "wb"
        print(f"  {name}: 받는 중 ({have:,} / {size:,} bytes)")
        with path.open(mode) as out:
            done, next_report = have, have + 100 * CHUNK
            while chunk := resp.read(CHUNK):
                out.write(chunk)
                done += len(chunk)
                if done >= next_report:
                    print(f"    {100 * done / size:5.1f}%")
                    next_report += 100 * CHUNK

    if path.stat().st_size != size or file_digest(path, entry) != expected_digest(entry):
        path.unlink()
        sys.exit(f"{name}: 크기 또는 해시가 맞지 않아 지웠다. 다시 실행할 것")
    print(f"  {name}: 완료, 해시 일치")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-item-pool", action="store_true",
                        help=f"BLaIR 평가용 상품 풀({ITEM_POOL_FILE}, 643MB)도 받는다")
    parser.add_argument("--revision", default=REVISION, help="데이터셋 커밋 (기본: 고정된 버전)")
    args = parser.parse_args()

    tree = api_tree(args.revision)
    names = QUERY_FILES + ([ITEM_POOL_FILE] if args.with_item_pool else [])
    missing = [n for n in names if n not in tree]
    if missing:
        sys.exit(f"revision {args.revision}에 없는 파일: {missing}")

    DEST.mkdir(parents=True, exist_ok=True)
    print(f"{REPO_ID}@{args.revision[:12]} -> {DEST}")
    for name in names:
        download(name, tree[name], args.revision)

    manifest = {
        "repo": REPO_ID,
        "revision": args.revision,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {n: {"size": tree[n]["size"], "hash": expected_digest(tree[n]),
                      "hash_type": "sha256" if tree[n].get("lfs") else "git-blob-sha1"}
                  for n in names},
    }
    (DEST / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(f"manifest -> {DEST / 'MANIFEST.json'}")


if __name__ == "__main__":
    main()
