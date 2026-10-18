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
  python3 scripts/notify.py --summary   新規取得件数と、巡回の問題(ブロック・一覧取得失敗・例外)を送る
                                        (新規0件で問題も無ければ送らない)
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
CONFIG = ROOT / "config.json"


def send(topic: str, title: str, message: str, click: str = "", priority: int = 0) -> None:
    payload: dict = {"topic": topic, "title": title, "message": message}
    if click:
        payload["click"] = click
    if priority:
        payload["priority"] = priority
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


def load_chain_names() -> dict[str, str]:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        return {c["slug"]: c["name"] for c in cfg.get("chains", [])}
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


def load_run_summary() -> dict:
    """直前の crawl.py 実行の結果(data/run_summary.json)を読み、新旧どちらの形式でも
    {"new", "blocked", "list_failed", "errors", "streaks", "detail_failed"} に揃えて返す。
    古い形式は {社: 新規件数} だけの平たい辞書。"""
    empty = {"new": {}, "blocked": [], "list_failed": [], "errors": [],
             "streaks": {}, "detail_failed": {}}
    try:
        data = json.loads(RUN_SUMMARY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    try:
        if isinstance(data.get("new"), dict):
            return {
                "new": {k: int(v) for k, v in data["new"].items()},
                "blocked": list(data.get("blocked") or []),
                "list_failed": list(data.get("list_failed") or []),
                "errors": list(data.get("errors") or []),
                "streaks": {k: int(v) for k, v in (data.get("streaks") or {}).items()},
                "detail_failed": {k: int(v) for k, v in (data.get("detail_failed") or {}).items()},
            }
        return {**empty, "new": {k: int(v) for k, v in data.items()}}
    except (TypeError, ValueError):
        return empty


def count_new_this_run() -> dict[str, int]:
    """直前の crawl.py 実行で新規に記録した商品数を読む。"""
    return load_run_summary()["new"]


PROBLEM_LABELS = (("blocked", "ブロックされた"), ("list_failed", "一覧の取得に失敗"),
                  ("errors", "例外で停止"))


def problem_lines(summary: dict, names: dict[str, str]) -> tuple[list[str], list[str]]:
    """問題のあった社の(表示名リスト, 本文の行リスト)。同じ社に複数の種別があれば1行にまとめる。"""
    kinds: dict[str, list[str]] = {}
    for key, label in PROBLEM_LABELS:
        for chain in summary[key]:
            kinds.setdefault(chain, []).append(label)
    shown, lines = [], []
    for chain, labels in kinds.items():
        name = names.get(chain, chain)
        days = summary["streaks"].get(chain, 0)
        streak = f"({days}日連続)" if days >= 2 else ""
        shown.append(name)
        lines.append(f"{name}: {'・'.join(labels)}{streak}")
    return shown, lines


def build_summary_message(summary: dict, names: dict[str, str]) -> tuple[str, str, int] | None:
    """(タイトル, 本文, 優先度)を返す。新規0件で問題も無ければ None。"""
    counts = summary["new"]
    total = sum(counts.values())
    shown, lines = problem_lines(summary, names)
    if total == 0 and not shown:
        return None
    parts = []
    if total:
        detail = " / ".join(f"{names.get(k, k)} {v}件" for k, v in counts.items() if v)
        parts.append(f"今回の巡回で新しく記録した商品: {detail}")
    if shown:
        parts.append("巡回の問題:\n" + "\n".join(lines)
                     + "\n公式サイトの一覧は入れ替わるため、続くと記録が失われます。実行ログを確認してください。")
        df = sum(n for c, n in summary["detail_failed"].items())
        if df:
            parts.append(f"(詳細ページの取得失敗 {df}件は翌日に自動で再試行します)")
    if total:
        title = f"{SITE_NAME}: 新商品 {total}件"
        if shown:
            title += f"(問題あり: {'・'.join(shown)})"
    else:
        title = f"{SITE_NAME}: 巡回に問題({'・'.join(shown)})"
    return title, "\n\n".join(parts), 4 if shown else 0


def main() -> int:
    args = sys.argv[1:]
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        print("NTFY_TOPIC未設定のため通知はスキップします")
        return 0

    if "--summary" in args:
        msg = build_summary_message(load_run_summary(), load_chain_names())
        if msg is None:
            print("新規0件で問題も無いため通知はスキップします")
            return 0
        title, body, priority = msg
        try:
            send(topic, title, body, _run_click(), priority)
            print(f"要約を通知しました: {title}")
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
