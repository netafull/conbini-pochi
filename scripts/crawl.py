#!/usr/bin/env python3
"""5社の新商品を巡回し、data/products/{chain}/{id}.json に1商品1ファイルで
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


def mark_list_failed(stats: dict, chain: str, reason: str) -> None:
    """一覧の取得・解析に失敗したことを記録する(通知用)。最初の理由を残す。
    詳細ページ単発の失敗(stats["failed"])とは別扱いで、こちらだけが通知対象になる。"""
    stats[chain].setdefault("list_failed", reason)


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
            mark_list_failed(stats, chain, f"fetch_error: {list_path}")
            continue
        for entry in parsers.parse_seven_list(html):
            entry["source_list"] = source_list
            all_entries.append(entry)

    if not all_entries and stats[chain]["list_requests"] and not stats[chain]["blocked"]:
        # 一覧は取れたのに1件も解析できない(サイトの作り変え)。既存データには触れない
        print("[warn] seven 一覧から商品を1件も取り出せませんでした", file=sys.stderr)
        mark_list_failed(stats, chain, "empty_list")

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
            mark_list_failed(stats, chain, f"fetch_error: {list_path}")
            continue
        list_week_by_source[source_list] = parsers.parse_familymart_list_week_start(html, today)
        for entry in parsers.parse_familymart_list(html):
            entry["source_list"] = source_list
            all_entries.append(entry)

    if not all_entries and stats[chain]["list_requests"]:
        print("[warn] familymart 一覧から商品を1件も取り出せませんでした", file=sys.stderr)
        mark_list_failed(stats, chain, "empty_list")

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
        mark_list_failed(stats, chain, "fetch_error: redirect")
        return

    target = parsers.parse_meta_refresh(redirect_html)
    if not target:
        print("[warn] lawson meta refresh が見つかりません", file=sys.stderr)
        mark_list_failed(stats, chain, "no_meta_refresh")
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
            mark_list_failed(stats, chain, f"fetch_error: {path}")
            continue
        for entry in parsers.parse_lawson_list(html):
            entry["source_list"] = path
            all_entries.append(entry)
        for nav_url in parsers.parse_lawson_nav_urls(html):
            if nav_url not in visited:
                to_visit.append(nav_url)

    if not all_entries and stats[chain]["list_requests"]:
        print("[warn] lawson 一覧から商品を1件も取り出せませんでした", file=sys.stderr)
        mark_list_failed(stats, chain, "empty_list")

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


# ---------------------------------------------------------------------------
# デイリーヤマザキ
# ---------------------------------------------------------------------------

def crawl_dailyyamazaki(pending: dict, stats: dict) -> None:
    """/new/ 1ページに全商品の詳細(モーダル)が載っているので、リクエストは1回だけ。
    詳細ページの取得ループは無い。

    公式は2026-09-01より前の分を載せておらず(ページ送りも無い)、掲載から消えた商品の
    ページも残らない。そのため、一覧から消えても保存済みの商品は絶対に削除しない
    (このクローラーは商品ファイルを消す処理を一切持たない)。既存商品は上書きしない。
    """
    base = "https://www.daily-yamazaki.jp"
    chain = "dailyyamazaki"
    pending.setdefault(chain, {})
    stats.setdefault(chain, {"list_requests": 0, "detail_requests": 0, "new_products": 0,
                              "failed": 0, "blocked": False})

    list_url = base + "/new/"
    try:
        html = get(chain, list_url)
        stats[chain]["list_requests"] += 1
    except BlockedError as e:
        stats[chain]["blocked"] = True
        print(f"[blocked] dailyyamazaki list: {e}", file=sys.stderr)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] dailyyamazaki list 取得失敗: {e}", file=sys.stderr)
        stats[chain]["failed"] += 1
        mark_list_failed(stats, chain, "fetch_error")
        pending[chain]["list"] = {
            "url": list_url, "reason": f"fetch_error: {e}", "checked_at": now_iso(),
        }
        return

    entries = parsers.parse_dailyyamazaki_list(html, datetime.datetime.now(JST).date())
    if not entries:
        # サイトの作り変えで1件も取れなくなった場合。既存データには触れない
        print("[warn] dailyyamazaki 一覧から商品を1件も取り出せませんでした", file=sys.stderr)
        stats[chain]["failed"] += 1
        mark_list_failed(stats, chain, "empty_list")
        pending[chain]["list"] = {
            "url": list_url, "reason": "empty_list", "checked_at": now_iso(),
        }
        return
    pending[chain].pop("list", None)

    seen: set[str] = set()
    for e in entries:
        pid = e["product_id"]
        if pid in seen:
            continue
        seen.add(pid)
        if load_product(chain, pid) is not None:
            continue  # 既存商品は上書きしない(アーカイブなので初回取得が正)

        excl, incl = parsers.parse_price_pair(e["price_text"])
        product = {
            "chain": chain,
            "product_id": pid,
            "name": e["name"],
            "category": e["category"],
            "price_excl_tax": excl,
            "price_incl_tax": incl,
            "launch_date": e["launch_date"],
            "launch_text": e["launch_text"],
            "regions": None,
            # 販売地域の専用項目は無い(説明文中の「※青森での販売はありません」等のみ)
            "region_text": None,
            "description": e["description"],
            "nutrition": e["nutrition"],
            "allergens": e["allergens"],
            "spec": None,
            "variants": None,
            "official_url": list_url,
            "first_seen_at": now_iso(),
            "fetched_at": now_iso(),
            "source_list": "new",
            "image": None,
        }
        save_product(chain, product)
        stats[chain]["new_products"] += 1


# ---------------------------------------------------------------------------
# ミニストップ
# ---------------------------------------------------------------------------

def crawl_ministop(pending: dict, stats: dict) -> None:
    """新商品一覧のJSON(/syohin/js/recommend.json、約2週間ぶん・十数件)を1回取得し、
    未保存の商品だけ詳細ページ(静的HTML)を取得する。

    公式は一覧から外れた商品の扱いが不明で、アーカイブ価値は「消える前の記録」にある。
    一覧から消えても保存済みの商品は絶対に削除しない(このクローラーは商品ファイルを消す
    処理を一切持たない)。既存商品は上書きしない。
    栄養成分・アレルゲンは新商品の詳細ページに載っていないので、nullのまま保存する。
    """
    base = "https://www.ministop.co.jp"
    chain = "ministop"
    pending.setdefault(chain, {})
    stats.setdefault(chain, {"list_requests": 0, "detail_requests": 0, "new_products": 0,
                              "failed": 0, "blocked": False})

    list_url = base + "/syohin/js/recommend.json"
    try:
        body = get(chain, list_url)
        stats[chain]["list_requests"] += 1
    except BlockedError as e:
        stats[chain]["blocked"] = True
        print(f"[blocked] ministop list: {e}", file=sys.stderr)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] ministop list 取得失敗: {e}", file=sys.stderr)
        stats[chain]["failed"] += 1
        mark_list_failed(stats, chain, "fetch_error")
        pending[chain]["list"] = {
            "url": list_url, "reason": f"fetch_error: {e}", "checked_at": now_iso(),
        }
        return

    try:
        entries = parsers.parse_ministop_recommend(body)
        reason = "empty_list"
    except ValueError as e:
        entries = []
        reason = "invalid_json"
        print(f"[warn] ministop 一覧のJSONが壊れています: {e}", file=sys.stderr)
    if not entries:
        # JSONが壊れた・0件になった場合(サイトの作り変え等)。既存データには触れない
        print("[warn] ministop 一覧から商品を1件も取り出せませんでした", file=sys.stderr)
        stats[chain]["failed"] += 1
        mark_list_failed(stats, chain, reason)
        pending[chain]["list"] = {"url": list_url, "reason": reason, "checked_at": now_iso()}
        return
    pending[chain].pop("list", None)

    seen: set[str] = set()
    for e in entries:
        pid = e["product_id"]
        if pid in seen:
            continue
        seen.add(pid)
        if load_product(chain, pid) is not None:
            continue  # 既存商品は上書きしない(アーカイブなので初回取得が正)

        detail_url = base + e["detail_url"]
        try:
            html = get(chain, detail_url)
            stats[chain]["detail_requests"] += 1
        except BlockedError as ex:
            stats[chain]["blocked"] = True
            print(f"[blocked] ministop detail {e['detail_url']}: {ex}", file=sys.stderr)
            return
        except Exception as ex:  # noqa: BLE001
            stats[chain]["failed"] += 1
            pending[chain][pid] = {
                "url": e["detail_url"], "reason": f"fetch_error: {ex}",
                "checked_at": now_iso(),
            }
            continue

        detail = parsers.parse_ministop_detail(html)
        if detail is None:
            stats[chain]["failed"] += 1
            pending[chain][pid] = {
                "url": e["detail_url"], "reason": "empty_detail",
                "checked_at": now_iso(),
            }
            continue

        region_text = detail["region_text"] or e["region_text"]
        excl, incl = parsers.parse_price_pair(detail["price_text"] or e["price_text"])
        product = {
            "chain": chain,
            "product_id": pid,
            "name": e["name"],
            "category": detail["category"],
            "price_excl_tax": excl,
            "price_incl_tax": incl,
            # 発売日は一覧(recommend.json)の値を正とし、無ければ詳細ページの値
            "launch_date": e["launch_date"] or detail["launch_date"],
            "launch_text": e["launch_text"] or detail["launch_text"] or "",
            "regions": parsers.ministop_regions(region_text),
            "region_text": region_text,
            "description": detail["description"],
            "nutrition": None,
            "allergens": None,
            "spec": None,
            "variants": None,
            "official_url": detail_url,
            "first_seen_at": now_iso(),
            "fetched_at": now_iso(),
            "source_list": "recommend",
            "image": None,
        }
        save_product(chain, product)
        stats[chain]["new_products"] += 1
        pending[chain].pop(pid, None)


RUN_SUMMARY = ROOT / "data" / "run_summary.json"


def collect_problems(stats: dict[str, dict]) -> dict[str, list[str]]:
    """この実行で問題があった社を種別ごとに集める(通知用)。
    詳細ページ単発の失敗(stats["failed"])は一時的なことが多く翌日自動で再試行されるので、
    ここには含めない。"""
    return {
        "blocked": [c for c, s in stats.items() if s.get("blocked")],
        "list_failed": [c for c, s in stats.items() if s.get("list_failed")],
        "errors": [c for c, s in stats.items() if s.get("error")],
    }


def update_health(pending: dict, stats: dict[str, dict], today: datetime.date) -> dict[str, int]:
    """社ごとの「連続で問題が出ている日数」を pending["_health"] に持ち、
    今回問題があった社の日数を返す。日付で数えるので同じ日の再実行では増えない。
    今回巡回しなかった社(社名指定の実行)は触らない。"""
    health = pending.setdefault("_health", {})
    problems = collect_problems(stats)
    bad = {c for names in problems.values() for c in names}
    streaks: dict[str, int] = {}
    for chain in stats:
        if chain not in bad:
            health.pop(chain, None)
            continue
        prev = health.get(chain) or {}
        last = prev.get("last_date")
        n = int(prev.get("days", 0))
        if last != today.isoformat():
            yesterday = (today - datetime.timedelta(days=1)).isoformat()
            n = n + 1 if last == yesterday else 1
        health[chain] = {"days": n, "last_date": today.isoformat()}
        streaks[chain] = n
    if not health:
        pending.pop("_health", None)
    return streaks


def save_run_summary(stats: dict[str, dict], streaks: dict[str, int] | None = None) -> None:
    """この実行の結果を書き出す。notify.py が読む。

    以前は notify.py が fetched_at の「今日」を数えていたが、実行環境のUTCと
    データの日本時間が1日ずれ、朝の実行で通知が出ない・手動実行で二重に通知が出る
    不具合があった(2026-09-29)。実行ごとに上書きされる一時ファイルで、gitには入れない。

    形式: {"new": {社: 新規件数}, "blocked": [社], "list_failed": [社], "errors": [社],
           "streaks": {社: 連続日数}, "detail_failed": {社: 詳細取得失敗件数}}
    (以前は {社: 新規件数} だけの平たい形式。notify.py は両方読める)
    """
    summary = {"new": {chain: s.get("new_products", 0) for chain, s in stats.items()}}
    summary.update(collect_problems(stats))
    summary["streaks"] = streaks or {}
    summary["detail_failed"] = {c: s["failed"] for c, s in stats.items()
                                if s.get("failed") and not s.get("list_failed")}
    RUN_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    pending = load_pending()
    stats: dict[str, dict] = {}

    # 引数に社名(seven, familymart, lawson, dailyyamazaki, ministop)を渡すと、その社だけ巡回する
    # (動作確認用。他社のサーバーに余計なリクエストを送らないため)
    only = set(sys.argv[1:])
    crawlers = {
        "seven": crawl_seven, "familymart": crawl_familymart,
        "lawson": crawl_lawson, "dailyyamazaki": crawl_dailyyamazaki,
        "ministop": crawl_ministop,
    }
    unknown = only - set(crawlers)
    if unknown:
        print(f"unknown chain: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1

    for name, fn in crawlers.items():
        if only and name not in only:
            continue
        try:
            fn(pending, stats)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {fn.__name__} で例外: {e}", file=sys.stderr)
            stats.setdefault(name, {})["error"] = f"{type(e).__name__}: {e}"

    streaks = update_health(pending, stats, datetime.datetime.now(JST).date())
    save_pending(pending)
    save_run_summary(stats, streaks)

    print("=== クロール結果 ===")
    for chain, s in stats.items():
        excluded = s.get("excluded_category", 0)
        excluded_text = f" / 対象外カテゴリ除外{excluded}件" if excluded else ""
        print(
            f"{chain}: 一覧{s.get('list_requests', 0)}件 / 詳細{s.get('detail_requests', 0)}件 "
            f"/ 新規{s.get('new_products', 0)}件 / 失敗{s.get('failed', 0)}件 "
            f"/ ブロック={'あり' if s.get('blocked') else 'なし'}{excluded_text}"
            + (f" / 一覧失敗={s['list_failed']}" if s.get("list_failed") else "")
            + (f" / 例外={s['error']}" if s.get("error") else "")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
