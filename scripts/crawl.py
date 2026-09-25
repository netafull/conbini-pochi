#!/usr/bin/env python3
"""3社の新商品を巡回し、data/products/{chain}/{id}.json に1商品1ファイルで
蓄積する。公式サイトは販売終了した商品ページを消してしまうため、
「いつ何が出たか」を後から引ける記録を残すことが目的。

既存ファイルは上書きしない(アーカイブなので初回取得が正)。例外は
セブンの地域別variants: 一覧に新しい地域URLが出てきたら追記する。

取得失敗(通信エラー・想定外の構造・セブンのblacklist状態)は
data/pending.json に社ごとに隔離して記録し、次回再試行する。
1社がこけても他社の処理は続行する。
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import parsers  # noqa: E402
from fetch import BlockedError, fetch  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
PRODUCTS = DATA / "products"
PENDING_PATH = DATA / "pending.json"
JST = datetime.timezone(datetime.timedelta(hours=9))

UA = CONFIG.get("user_agent", "ConbiniPochi/1.0")
INTERVALS = CONFIG.get("request_interval_seconds", {})

# 食品以外(キャラクターくじ・雑貨、コスメ、アパレル等)は取得も掲載もしない方針。
# カテゴリ情報があるのはファミマのみなので、ファミマの一覧パース直後(詳細ページを
# 取得する前)にこのカテゴリと一致する商品を弾く。セブン・ローソンは新商品一覧が
# 実質食品のみなので対象外。
FAMILYMART_EXCLUDE_CATEGORIES = set(CONFIG.get("familymart_exclude_categories", []))


def is_familymart_excluded(category: str | None) -> bool:
    return bool(category) and category in FAMILYMART_EXCLUDE_CATEGORIES


def now_iso() -> str:
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


def load_pending() -> dict:
    try:
        return json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_pending(pending: dict) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(
        json.dumps(pending, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def product_path(chain: str, product_id: str) -> Path:
    return PRODUCTS / chain / f"{product_id}.json"


def save_product(chain: str, product: dict) -> None:
    p = product_path(chain, product["product_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(product, ensure_ascii=False, indent=1), encoding="utf-8")


def load_product(chain: str, product_id: str) -> dict | None:
    p = product_path(chain, product_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


class Throttle:
    """社ごとに最後のリクエスト時刻を覚え、間隔を守って待つ。"""

    def __init__(self):
        self.last: dict[str, float] = {}

    def wait(self, chain: str) -> None:
        interval = INTERVALS.get(chain, 3)
        last = self.last.get(chain)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
        self.last[chain] = time.monotonic()


THROTTLE = Throttle()


def get(chain: str, url: str) -> str:
    THROTTLE.wait(chain)
    return fetch(url, UA)


# ---------------------------------------------------------------------------
# セブンイレブン
# ---------------------------------------------------------------------------

SEVEN_REGION_RE = re.compile(r"/products/a/item/\d{6}/([a-z]+)/?$")


def crawl_seven(pending: dict, stats: dict) -> None:
    base = "https://www.sej.co.jp"
    chain = "seven"
    pending.setdefault(chain, {})
    stats.setdefault(chain, {"list_requests": 0, "detail_requests": 0, "new_products": 0,
                              "new_variants": 0, "failed": 0, "blocked": False})

    all_entries: list[dict] = []
    for list_path, source_list in (("/products/a/thisweek/", "thisweek"),
                                    ("/products/a/nextweek/", "nextweek")):
        try:
            html = get(chain, base + list_path)
            stats[chain]["list_requests"] += 1
        except BlockedError as e:
            stats[chain]["blocked"] = True
            print(f"[blocked] seven list {list_path}: {e}", file=sys.stderr)
            return
        except Exception as e:  # noqa: BLE001
            print(f"[warn] seven list {list_path} 取得失敗: {e}", file=sys.stderr)
            continue
        for entry in parsers.parse_seven_list(html):
            entry["source_list"] = source_list
            all_entries.append(entry)

    by_code: dict[str, list[dict]] = {}
    for e in all_entries:
        by_code.setdefault(e["product_id"], []).append(e)

    for code, entries in by_code.items():
        existing = load_product(chain, code)
        variants = dict(existing.get("variants") or {}) if existing else {}
        representative = None  # 商品共通欄(説明文・栄養成分等)に使う最初に取れた詳細
        changed = False

        for e in entries:
            m = SEVEN_REGION_RE.search(e["detail_url"])
            region_key = m.group(1) if m else e["detail_url"]
            if region_key in variants:
                continue  # 既に取得済みの地域ページは再取得しない
            try:
                html = get(chain, base + e["detail_url"])
                stats[chain]["detail_requests"] += 1
            except BlockedError as ex:
                stats[chain]["blocked"] = True
                print(f"[blocked] seven detail {e['detail_url']}: {ex}", file=sys.stderr)
                return
            except Exception as ex:  # noqa: BLE001
                stats[chain]["failed"] += 1
                pending[chain].setdefault(code, {})[region_key] = {
                    "url": e["detail_url"], "reason": f"fetch_error: {ex}",
                    "checked_at": now_iso(),
                }
                continue
            detail = parsers.parse_seven_detail(html)
            if detail is None:
                stats[chain]["failed"] += 1
                pending[chain].setdefault(code, {})[region_key] = {
                    "url": e["detail_url"], "reason": "blacklist_or_empty",
                    "checked_at": now_iso(),
                }
                continue
            excl, incl = parsers.parse_price_pair(detail["price_text"])
            variants[region_key] = {
                "region_text": detail["region_text"],
                "regions": detail["regions"],
                "price_excl_tax": excl,
                "price_incl_tax": incl,
                "url": base + e["detail_url"],
            }
            if representative is None:
                representative = detail
            changed = True
            pending[chain].get(code, {}).pop(region_key, None)

        if not variants:
            continue
        if not changed and existing is not None:
            continue  # 新規地域も無く、変化なし

        first_region_key, first_region = next(iter(variants.items()))
        product = existing or {
            "chain": chain,
            "product_id": code,
            "name": entries[0]["name"],
            "category": None,
            "price_excl_tax": first_region.get("price_excl_tax"),
            "price_incl_tax": first_region.get("price_incl_tax"),
            "launch_date": parsers.parse_launch_date_iso(entries[0]["launch_text"]),
            "launch_text": entries[0]["launch_text"],
            "regions": None,
            "region_text": None,
            "description": None,
            "nutrition": None,
            "allergens": None,
            "spec": None,
            "variants": {},
            "official_url": base + entries[0]["detail_url"],
            "official_urls": [],
            "first_seen_at": now_iso(),
            "fetched_at": now_iso(),
            "source_list": entries[0]["source_list"],
            "image": None,
        }
        if existing is None:
            stats[chain]["new_products"] += 1
        else:
            stats[chain]["new_variants"] += 1
        product["variants"] = variants
        product["official_urls"] = sorted({v["url"] for v in variants.values()})
        # 説明文・栄養成分・アレルゲンは商品共通(地域差はほぼ無い)なので、
        # 初めて取れた地域の値を代表として使う。既存商品で未設定なら埋める
        if representative is not None and product.get("description") is None:
            product["description"] = representative["description"]
            product["nutrition"] = representative["nutrition"]
            product["allergens"] = representative["allergens"]
            product["regions"] = representative["regions"]
            product["region_text"] = representative["region_text"]
        product["fetched_at"] = now_iso()
        save_product(chain, product)


# ---------------------------------------------------------------------------
# ファミリーマート
# ---------------------------------------------------------------------------

def crawl_familymart(pending: dict, stats: dict) -> None:
    base = "https://www.family.co.jp"
    chain = "familymart"
    pending.setdefault(chain, {})
    stats.setdefault(chain, {"list_requests": 0, "detail_requests": 0, "new_products": 0,
                              "failed": 0, "blocked": False, "excluded_category": 0})

    all_entries: list[dict] = []
    list_week_by_source: dict[str, str | None] = {}
    today = datetime.datetime.now(JST).date()
    for list_path, source_list in (
        ("/goods/newgoods.html", "thisweek"),
        ("/goods/newgoods/nextweek.html", "nextweek"),
        ("/goods/newgoods/lastweek.html", "lastweek"),
    ):
        try:
            html = get(chain, base + list_path)
            stats[chain]["list_requests"] += 1
        except BlockedError as e:
            stats[chain]["blocked"] = True
            print(f"[blocked] familymart list {list_path}: {e}", file=sys.stderr)
            return
        except Exception as e:  # noqa: BLE001
            print(f"[warn] familymart list {list_path} 取得失敗: {e}", file=sys.stderr)
            continue
        list_week_by_source[source_list] = parsers.parse_familymart_list_week_start(html, today)
        for entry in parsers.parse_familymart_list(html):
            entry["source_list"] = source_list
            all_entries.append(entry)

    seen: set[str] = set()
    for e in all_entries:
        pid = e["product_id"]
        if pid in seen:
            continue
        seen.add(pid)
        if is_familymart_excluded(e.get("category")):
            # 食品以外は詳細ページを取得せずスキップ(リクエスト節約)
            stats[chain]["excluded_category"] += 1
            pending[chain].pop(pid, None)
            continue
        if load_product(chain, pid) is not None:
            continue  # 既存商品は再取得しない(アーカイブなので初回取得が正)

        try:
            html = get(chain, e["detail_url"])
            stats[chain]["detail_requests"] += 1
        except BlockedError as ex:
            stats[chain]["blocked"] = True
            print(f"[blocked] familymart detail {e['detail_url']}: {ex}", file=sys.stderr)
            return
        except Exception as ex:  # noqa: BLE001
            stats[chain]["failed"] += 1
            pending[chain][pid] = {
                "url": e["detail_url"], "reason": f"fetch_error: {ex}",
                "checked_at": now_iso(),
            }
            continue

        detail = parsers.parse_familymart_detail(html)
        if detail is None:
            stats[chain]["failed"] += 1
            pending[chain][pid] = {
                "url": e["detail_url"], "reason": "empty_detail",
                "checked_at": now_iso(),
            }
            continue

        excl, incl = parsers.parse_price_pair(detail["price_text"] or e["price_text"])
        product = {
            "chain": chain,
            "product_id": pid,
            "name": e["name"],
            "category": e["category"] or None,
            "price_excl_tax": excl,
            "price_incl_tax": incl,
            "launch_date": parsers.parse_launch_date_iso(detail["launch_text"]),
            "launch_text": detail["launch_text"],
            "regions": detail["regions"] or None,
            "region_text": e.get("region_prefix"),
            "description": detail["description"],
            "nutrition": detail["nutrition"],
            "allergens": detail["allergens"],
            "spec": detail["spec_text"] or None,
            "variants": None,
            "official_url": e["detail_url"],
            "first_seen_at": now_iso(),
            "fetched_at": now_iso(),
            "source_list": e["source_list"],
            # 発売日が詳細ページに無い商品(キャラクターくじ・雑貨等)を週別
            # アーカイブへ正しく振り分けるための、一覧見出しから取った週開始日
            "list_week_start": list_week_by_source.get(e["source_list"]),
            "image": None,
        }
        save_product(chain, product)
        stats[chain]["new_products"] += 1
        pending[chain].pop(pid, None)


# ---------------------------------------------------------------------------
# ローソン
# ---------------------------------------------------------------------------

def crawl_lawson(pending: dict, stats: dict) -> None:
    base = "https://www.lawson.co.jp"
    chain = "lawson"
    pending.setdefault(chain, {})
    stats.setdefault(chain, {"list_requests": 0, "detail_requests": 0, "new_products": 0,
                              "failed": 0, "blocked": False})

    try:
        redirect_html = get(chain, base + "/recommend/new/")
        stats[chain]["list_requests"] += 1
    except BlockedError as e:
        stats[chain]["blocked"] = True
        print(f"[blocked] lawson redirect: {e}", file=sys.stderr)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] lawson 起点ページ取得失敗: {e}", file=sys.stderr)
        return

    target = parsers.parse_meta_refresh(redirect_html)
    if not target:
        print("[warn] lawson meta refresh が見つかりません", file=sys.stderr)
        return

    to_visit = [target]
    visited: set[str] = set()
    all_entries: list[dict] = []

    while to_visit:
        path = to_visit.pop(0)
        if path in visited:
            continue
        visited.add(path)
        try:
            html = get(chain, base + path)
            stats[chain]["list_requests"] += 1
        except BlockedError as e:
            stats[chain]["blocked"] = True
            print(f"[blocked] lawson list {path}: {e}", file=sys.stderr)
            return
        except Exception as e:  # noqa: BLE001
            print(f"[warn] lawson list {path} 取得失敗: {e}", file=sys.stderr)
            continue
        for entry in parsers.parse_lawson_list(html):
            entry["source_list"] = path
            all_entries.append(entry)
        for nav_url in parsers.parse_lawson_nav_urls(html):
            if nav_url not in visited:
                to_visit.append(nav_url)

    seen: set[str] = set()
    for e in all_entries:
        pid = e["product_id"]
        if pid in seen:
            continue
        seen.add(pid)
        if load_product(chain, pid) is not None:
            continue

        try:
            html = get(chain, base + e["detail_url"])
            stats[chain]["detail_requests"] += 1
        except BlockedError as ex:
            stats[chain]["blocked"] = True
            print(f"[blocked] lawson detail {e['detail_url']}: {ex}", file=sys.stderr)
            return
        except Exception as ex:  # noqa: BLE001
            stats[chain]["failed"] += 1
            pending[chain][pid] = {
                "url": e["detail_url"], "reason": f"fetch_error: {ex}",
                "checked_at": now_iso(),
            }
            continue

        detail = parsers.parse_lawson_detail(html)
        if detail is None:
            stats[chain]["failed"] += 1
            pending[chain][pid] = {
                "url": e["detail_url"], "reason": "empty_detail",
                "checked_at": now_iso(),
            }
            continue

        # 発売日は発売後に詳細ページから消えるため、一覧側の値を必ず使う
        excl, incl = parsers.parse_price_pair(detail["price_text"] or e["price_text"])
        product = {
            "chain": chain,
            "product_id": pid,
            "name": e["name"],
            "category": None,
            "price_excl_tax": excl,
            "price_incl_tax": incl,
            "launch_date": parsers.parse_launch_date_iso(e["launch_text"]),
            "launch_text": e["launch_text"],
            "regions": None,
            "region_text": e.get("note") or None,
            "description": detail["description"],
            "nutrition": detail["nutrition"],
            "allergens": detail["allergens"],
            "spec": detail["spec_text"] or None,
            "variants": None,
            "official_url": base + e["detail_url"],
            "first_seen_at": now_iso(),
            "fetched_at": now_iso(),
            "source_list": e["source_list"],
            "image": None,
        }
        save_product(chain, product)
        stats[chain]["new_products"] += 1
        pending[chain].pop(pid, None)


def main() -> int:
    pending = load_pending()
    stats: dict[str, dict] = {}

    for fn in (crawl_seven, crawl_familymart, crawl_lawson):
        try:
            fn(pending, stats)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {fn.__name__} で例外: {e}", file=sys.stderr)

    save_pending(pending)

    print("=== クロール結果 ===")
    for chain, s in stats.items():
        excluded = s.get("excluded_category", 0)
        excluded_text = f" / 対象外カテゴリ除外{excluded}件" if excluded else ""
        print(
            f"{chain}: 一覧{s.get('list_requests', 0)}件 / 詳細{s.get('detail_requests', 0)}件 "
            f"/ 新規{s.get('new_products', 0)}件 / 失敗{s.get('failed', 0)}件 "
            f"/ ブロック={'あり' if s.get('blocked') else 'なし'}{excluded_text}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
