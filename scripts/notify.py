#!/usr/bin/env python3
"""ntfy (https://ntfy.sh/) で新商品の要約・失敗を知らせる。林檎ポチと同じ
サービスを使うが、実行頻度が1日1回である点が違うため通知の設計を単純化した。

林檎ポチ(30分間隔)は「連続失敗が閾値時間を超えた瞬間だけ鳴らす」エッジ
トリガー方式を採っているが、これは短い間隔で何度も再試行が走ることを前提に
した抑制策。コンビニポチは1日1回しか実行されないため、1回の失敗が即
「その日の更新を逃した」ことを意味し、逆に毎回鳴らさないと気づけない。
よって単純に「失敗したら毎回鳴らす」「成功して新商品があれば要約を送る」
の2本にしている。

トピック名は GitHub Secrets (NTFY_TOPIC) で管理し、リポジトリには書かない
(公開リポジトリなので、トピック名が漏れると誰でも通知を送りつけられる)。

使い方:
  python3 scripts/notify.py --summary   新規取得件数の要約を送る(0件なら送らない)
  python3 scripts/notify.py --failure   更新が失敗したことを知らせる

必要な環境変数:
  NTFY_TOPIC : ntfyのトピック名 (未設定なら何もしない)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SITE_NAME = "コンビニポチ"
ROOT = Path(__file__).resolve().parent.parent
RUN_SUMMARY = ROOT / "data" / "run_summary.json"


def send(topic: str, title: str, message: str, click: str = "") -> None:
    payload: dict[str, str] = {"topic": topic, "title": title, "message": message}
    if click:
        payload["click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def _run_click() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""


def count_new_this_run() -> dict[str, int]:
    """直前の crawl.py 実行で新規に記録した商品数(data/run_summary.json)を読む。"""
    try:
        data = json.loads(RUN_SUMMARY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: int(v) for k, v in data.items()}


def main() -> int:
    args = sys.argv[1:]
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        print("NTFY_TOPIC未設定のため通知はスキップします")
        return 0

    if "--summary" in args:
        counts = count_new_this_run()
        total = sum(counts.values())
        if total == 0:
            print("新規0件のため通知はスキップします")
            return 0
        detail = " / ".join(f"{k} {v}件" for k, v in counts.items() if v)
        try:
            send(topic, f"{SITE_NAME}: 新商品 {total}件",
                 f"今回の巡回で新しく記録した商品: {detail}", _run_click())
            print(f"要約を通知しました({total}件)")
        except (urllib.error.URLError, OSError) as e:
            print(f"[warn] ntfy通知に失敗しました: {e}", file=sys.stderr)
        return 0

    if "--failure" in args:
        try:
            send(topic, f"{SITE_NAME}: 更新に失敗しました",
                 "本日の巡回が失敗しました。サイトは前回の内容のままです。実行ログを確認してください。",
                 _run_click())
            print("失敗を通知しました")
        except (urllib.error.URLError, OSError) as e:
            print(f"[warn] ntfy通知に失敗しました: {e}", file=sys.stderr)
        return 0

    print("usage: notify.py --summary|--failure", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
