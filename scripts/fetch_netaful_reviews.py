#!/usr/bin/env python3
"""ネタフル(WordPress)の公開REST APIから、コンビニ3社タグの記事を取得し、
コンビニポチの商品(data/products/)とひも付けてキャッシュする。

認証不要のREST API (https://netaful.jp/wp-json/wp/v2/posts?tags=...) を使う。
タグID: セブンイレブン=911, ファミリーマート=910, ローソン=921。

初回(または --backfill 指定時)は各タグ全ページを取得する(12ページ程度)。
通常実行(毎日、crawl.pyの後)は各タグ最新1ページ(per_page=50)だけ取得して
キャッシュに追加し、全商品に対して再照合する(レビューは発売後に書かれる
ことが多く、過去の商品にも後から記事がつくため)。

ネタフル側の失敗はサイト更新全体を止めないよう、errorsに記録するだけで
処理を続ける。

保存先: data/netaful_reviews.json
  {
    "fetched_at": "...",
    "articles": {"seven": [{"id":.., "date":.., "link":.., "title":..,
                             "product_name":..}], "familymart": [...], "lawson": [...]},
    "matches": {"seven-044723": [209819, ...], ...},
    "errors": ["..."]
  }
"""

from __future__ import annotations

import datetime
import html as html_lib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
PRODUCTS = ROOT / "data" / "products"
CACHE_PATH = ROOT / "data" / "netaful_reviews.json"
JST = datetime.timezone(datetime.timedelta(hours=9))

UA = CONFIG.get("user_agent", "ConbiniPochi/1.0")
API_BASE = "https://netaful.jp/wp-json/wp/v2/posts"
REQUEST_INTERVAL = 1.0  # ネタフルへのリクエスト間隔(秒)、1秒以上

TAGS = {
    "seven": 911,
    "familymart": 910,
    "lawson": 921,
}

TITLE_NAME_RE = re.compile(r"「(.*?)」")

# 正規化で除去する記号・空白類(全角/半角の中黒・括弧・句読点等)
_STRIP_RE = re.compile(
    r"[\s　・:：/／,、。.!！?？\-—―~〜()（）\[\]【】「」『』\"'’”×%!]+"
)


def normalize_name(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = _STRIP_RE.sub("", s)
    return s.lower()


def http_get(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read(), dict(res.getheaders())


def fetch_tag_page(tag_id: int, page: int, per_page: int) -> tuple[list[dict], int]:
    url = f"{API_BASE}?tags={tag_id}&per_page={per_page}&page={page}&_fields=id,date,link,title"
    body, headers = http_get(url)
    total_pages = int(headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages") or 1)
    posts = json.loads(body.decode("utf-8"))
    return posts, total_pages


def extract_article(post: dict) -> dict:
    raw_title = html_lib.unescape(post.get("title", {}).get("rendered", ""))
    m = TITLE_NAME_RE.search(raw_title)
    product_name = m.group(1).strip() if m else None
    return {
        "id": post["id"],
        "date": post.get("date"),
        "link": post.get("link"),
        "title": raw_title,
        "product_name": product_name,
    }


def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"fetched_at": None, "articles": {c: [] for c in TAGS}, "matches": {}, "errors": []}
    except json.JSONDecodeError:
        return {"fetched_at": None, "articles": {c: [] for c in TAGS}, "matches": {}, "errors": []}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


def fetch_articles_for_chain(chain: str, backfill: bool, errors: list[str]) -> list[dict]:
    tag_id = TAGS[chain]
    per_page = 100 if backfill else 50
    articles: list[dict] = []
    page = 1
    total_pages = 1
    first = True
    while page <= total_pages:
        if not first:
            time.sleep(REQUEST_INTERVAL)
        first = False
        try:
            posts, total_pages = fetch_tag_page(tag_id, page, per_page)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            errors.append(f"{chain} tag={tag_id} page={page}: {e}")
            break
        except json.JSONDecodeError as e:
            errors.append(f"{chain} tag={tag_id} page={page}: JSON解析失敗 {e}")
            break
        articles.extend(extract_article(p) for p in posts)
        if not backfill:
            break  # 通常実行は最新1ページのみ
        page += 1
    return articles


def load_all_products() -> list[dict]:
    items = []
    for chain_dir in PRODUCTS.iterdir() if PRODUCTS.is_dir() else []:
        if not chain_dir.is_dir():
            continue
        for f in chain_dir.glob("*.json"):
            try:
                items.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return items


def product_effective_date(item: dict) -> str | None:
    return item.get("launch_date") or item.get("list_week_start") or (item.get("first_seen_at") or "")[:10] or None


def names_match(article_name_norm: str, product_name_norm: str) -> bool:
    if not article_name_norm or not product_name_norm:
        return False
    if article_name_norm == product_name_norm:
        return True
    shorter, longer = sorted([article_name_norm, product_name_norm], key=len)
    if len(shorter) >= 6 and shorter in longer:
        return True
    return False


def rematch(cache: dict) -> tuple[dict, list[tuple[str, dict, dict]], list[tuple[dict, dict, str]]]:
    """全商品×全記事を再照合する。

    戻り値: (matches辞書, ひも付いた例のリスト[(key, product, article)],
             惜しくも外れた例のリスト[(product, article)] ※デバッグ用)
    """
    products = load_all_products()
    by_chain: dict[str, list[dict]] = {}
    for p in products:
        by_chain.setdefault(p["chain"], []).append(p)

    matches: dict[str, list[int]] = {}
    matched_examples: list[tuple[str, dict, dict]] = []
    near_misses: list[tuple[dict, dict, str]] = []

    for chain, articles in cache["articles"].items():
        chain_products = by_chain.get(chain, [])
        prod_norms = [
            (p, normalize_name(p.get("name")), product_effective_date(p)) for p in chain_products
        ]
        for art in articles:
            art_name = art.get("product_name")
            if not art_name:
                continue
            art_norm = normalize_name(art_name)
            try:
                art_date = datetime.date.fromisoformat((art.get("date") or "")[:10])
            except ValueError:
                art_date = None

            best_near = None
            for p, p_norm, p_eff_date in prod_norms:
                if names_match(art_norm, p_norm):
                    # 日付フィルタ: 記事が商品発売日より14日以上前なら対象外
                    date_excluded = False
                    if art_date is not None and p_eff_date:
                        try:
                            eff = datetime.date.fromisoformat(p_eff_date)
                        except ValueError:
                            eff = None
                        if eff is not None and (eff - art_date).days > 14:
                            date_excluded = True
                    if date_excluded:
                        if len(near_misses) < 20:
                            near_misses.append((p, art, "日付フィルタで除外(14日超)"))
                        continue
                    key = f"{chain}-{p['product_id']}"
                    matches.setdefault(key, [])
                    if art["id"] not in matches[key]:
                        matches[key].append(art["id"])
                        if len(matched_examples) < 20:
                            matched_examples.append((key, p, art))
                elif art_norm and p_norm and not best_near:
                    # 惜しくも外れた例の簡易検出: 先頭4文字以上が共通するが不一致
                    common = len(_common_prefix(art_norm, p_norm))
                    if common >= 4:
                        best_near = (p, art, "名前不一致(先頭のみ類似)")
            if best_near and len(near_misses) < 20:
                near_misses.append(best_near)

    return matches, matched_examples, near_misses


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return a[:n]


def main() -> int:
    backfill = "--backfill" in sys.argv[1:] or not CACHE_PATH.exists()
    cache = load_cache()
    errors: list[str] = list(cache.get("errors") or [])

    for chain in TAGS:
        new_articles = fetch_articles_for_chain(chain, backfill, errors)
        if not new_articles:
            continue
        existing = {a["id"]: a for a in cache["articles"].get(chain, [])}
        for a in new_articles:
            existing[a["id"]] = a
        cache["articles"][chain] = sorted(existing.values(), key=lambda a: a.get("date") or "", reverse=True)
        time.sleep(REQUEST_INTERVAL)

    matches, matched_examples, near_misses = rematch(cache)
    cache["matches"] = matches
    cache["errors"] = errors[-50:]  # 直近分だけ保持
    cache["fetched_at"] = datetime.datetime.now(JST).isoformat(timespec="seconds")
    save_cache(cache)

    counts = {c: len(cache["articles"].get(c, [])) for c in TAGS}
    matched_products = len(matches)
    matched_by_chain: dict[str, int] = {}
    for key in matches:
        chain = key.split("-", 1)[0]
        matched_by_chain[chain] = matched_by_chain.get(chain, 0) + 1

    print(f"=== ネタフルレビュー取得結果 (backfill={backfill}) ===")
    print(f"記事キャッシュ件数: {counts}")
    print(f"ひも付いた商品数(社別): {matched_by_chain} / 合計{matched_products}件")
    if errors:
        print(f"エラー: {len(errors)}件（直近: {errors[-3:]}）", file=sys.stderr)

    print("\n--- ひも付いた例 ---")
    for key, p, art in matched_examples[:5]:
        print(f"  {key}: 商品「{p['name']}」 <-> 記事「{art['product_name']}」({art['link']})")

    print("\n--- 惜しくも外れた例 ---")
    for p, art, reason in near_misses[:5]:
        print(f"  商品「{p['name']}」({p['chain']}) <-> 記事「{art['product_name']}」({art['link']}) [{reason}]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
