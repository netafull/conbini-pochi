#!/usr/bin/env python3
"""一回限りの修復スクリプト: ファミリーマート商品の税込価格nullバグ・
発売日nullの週振り分けを、一覧ページ3枚(今週・来週・先週、3リクエストのみ)
から埋め戻す。

背景: parsers.parse_familymart_detail の価格正規表現が、入れ子の
<span>（税込</span>...<span>）</span> で切れていたため、税込価格が
全167件nullになっていた(修正はscripts/parsers.py側で対応済み)。
詳細ページは公式サイトへの負荷を避けるため再取得しない。対象商品は
今週・来週・先週のいずれかの一覧に載っているはずなので、一覧側の
価格テキスト(こちらは元々正しくパースできている)で埋め戻す。

ついでに、一覧の週見出し(list_week_start)も同じ3リクエストで取得できる
ので、発売日が無い既存商品(67件)の週振り分け用に埋める。

使い方:
  python3 scripts/repair_familymart_price.py            # 実際に書き換える
  python3 scripts/repair_familymart_price.py --dry-run  # 件数だけ確認
"""

from __future__ import annotations

import datetime
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import parsers  # noqa: E402
from fetch import BlockedError, fetch  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
PRODUCTS = ROOT / "data" / "products" / "familymart"
UA = CONFIG.get("user_agent", "ConbiniPochi/1.0")
JST = datetime.timezone(datetime.timedelta(hours=9))

LIST_PAGES = (
    ("https://www.family.co.jp/goods/newgoods.html", "thisweek"),
    ("https://www.family.co.jp/goods/newgoods/nextweek.html", "nextweek"),
    ("https://www.family.co.jp/goods/newgoods/lastweek.html", "lastweek"),
)


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    today = datetime.datetime.now(JST).date()

    interval = CONFIG.get("request_interval_seconds", {}).get("familymart", 3)
    by_pid: dict[str, dict] = {}
    list_week_by_source: dict[str, str | None] = {}
    for i, (url, source_list) in enumerate(LIST_PAGES):
        if i > 0:
            time.sleep(interval)
        try:
            html = fetch(url, UA)
        except BlockedError as e:
            print(f"[blocked] {url}: {e}", file=sys.stderr)
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {url} 取得失敗: {e}", file=sys.stderr)
            continue
        list_week_by_source[source_list] = parsers.parse_familymart_list_week_start(html, today)
        for entry in parsers.parse_familymart_list(html):
            entry["source_list"] = source_list
            by_pid.setdefault(entry["product_id"], entry)  # 先勝ち(今週優先)

    files = sorted(PRODUCTS.glob("*.json"))
    price_filled = 0
    week_filled = 0
    not_found: list[str] = []

    for f in files:
        try:
            product = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        pid = product["product_id"]
        changed = False

        if product.get("price_incl_tax") is None:
            entry = by_pid.get(pid)
            if entry is not None:
                excl, incl = parsers.parse_price_pair(entry["price_text"])
                if incl is not None:
                    product["price_excl_tax"] = excl
                    product["price_incl_tax"] = incl
                    price_filled += 1
                    changed = True
                else:
                    not_found.append(pid)
            else:
                not_found.append(pid)

        if product.get("launch_date") is None and product.get("list_week_start") is None:
            entry = by_pid.get(pid)
            source_list = entry["source_list"] if entry else product.get("source_list")
            wk = list_week_by_source.get(source_list)
            if wk:
                product["list_week_start"] = wk
                week_filled += 1
                changed = True

        if changed and not dry_run:
            f.write_text(json.dumps(product, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"価格を埋めた: {price_filled}件")
    print(f"list_week_startを埋めた: {week_filled}件")
    print(f"価格が埋まらなかった件数: {len(set(not_found))}件")
    if dry_run:
        print("(--dry-run のため書き込みはしていません)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
