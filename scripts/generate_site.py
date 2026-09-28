#!/usr/bin/env python3
"""data/products/*/*.json から docs/ 一式を生成する静的サイトジェネレータ。

林檎ポチ(apple-refurb-site)の骨格を踏襲しつつ、コンビニポチは「入荷イベント」
ではなく「アーカイブ」が軸なので、週(月曜始まり)ごとのページを中心に構成する。
"""

from __future__ import annotations

import datetime
import html
import json
import shutil
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
PRODUCTS = DATA / "products"
DOCS = ROOT / "docs"
JST = datetime.timezone(datetime.timedelta(hours=9))

CHAINS = CONFIG["chains"]
CHAIN_NAME = {c["slug"]: c["name"] for c in CHAINS}
# セブンの地域別ページのURL名 → 表示名
SEVEN_AREA_NAMES = {
    "hokkaido": "北海道", "tohoku": "東北", "kanto": "関東", "koshinetsu": "甲信越",
    "hokuriku": "北陸", "tokai": "東海", "kinki": "近畿", "chugoku": "中国",
    "shikoku": "四国", "kyushu": "九州", "okinawa": "沖縄",
}

REVIEWS_PATH = DATA / "netaful_reviews.json"


def load_reviews() -> dict:
    try:
        return json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"articles": {}, "matches": {}}


REVIEWS = load_reviews()
ARTICLES_BY_ID: dict[int, dict] = {}
for _chain, _arts in REVIEWS.get("articles", {}).items():
    for _a in _arts:
        _a = dict(_a)
        _a["chain"] = _chain
        ARTICLES_BY_ID[_a["id"]] = _a
del _chain, _arts


def item_reviews(item: dict) -> list[dict]:
    """商品にひも付いたネタフルのレビュー記事(新しい順)。"""
    key = f"{item['chain']}-{item['product_id']}"
    ids = REVIEWS.get("matches", {}).get(key, [])
    arts = [ARTICLES_BY_ID[i] for i in ids if i in ARTICLES_BY_ID]
    return sorted(arts, key=lambda a: a.get("date") or "", reverse=True)


def latest_reviews(chain: str | None, limit: int) -> list[dict]:
    arts = list(ARTICLES_BY_ID.values())
    if chain:
        arts = [a for a in arts if a["chain"] == chain]
    return sorted(arts, key=lambda a: a.get("date") or "", reverse=True)[:limit]


def render_review_card(a: dict) -> str:
    thumb = a.get("thumb") or {}
    img_html = ""
    if thumb.get("url"):
        w, h = thumb.get("width"), thumb.get("height")
        wh_attr = f' width="{int(w)}" height="{int(h)}"' if w and h else ""
        img_html = (
            f'<img src="{esc(thumb["url"])}" alt="{esc(a.get("title") or "")}" '
            f'loading="lazy"{wh_attr}>'
        )
    return f"""<a class="review-card" href="{esc(a.get('link') or '')}" target="_blank" rel="noopener">
{img_html}
<div class="rc-body">
<div class="rc-t">{esc(a.get('title') or '')}</div>
<div class="rc-date">{esc((a.get('date') or '')[:10])}</div>
</div>
</a>"""


def render_review_cards(title: str, articles: list[dict]) -> str:
    """ネタフルのレビュー記事を画像付きカードのグリッドで表示する。"""
    if not articles:
        return ""
    cards = "\n".join(render_review_card(a) for a in articles)
    return f"""<h2>{esc(title)}</h2>
<div class="review-grid">
{cards}
</div>"""


def nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


RELATED_KEYWORDS: list[dict] = CONFIG.get("related_keywords") or []


def match_keyword_for_name(name: str, keywords: list[dict] | None = None) -> dict | None:
    """商品名にマッチする関連キーワードのうち、一致した語が最も長い(具体的な)
    ものを返す。excludeに指定された語を含む場合、その語による一致は無視する。
    複数のキーワードが同じ長さで一致した場合はkeywordsに書かれた順を優先する。"""
    keywords = RELATED_KEYWORDS if keywords is None else keywords
    name_n = nfkc(name)
    best_kw = None
    best_len = -1
    for kw in keywords:
        excludes = kw.get("exclude") or []
        for w in kw.get("words") or []:
            if w not in name_n:
                continue
            if any(ex in name_n for ex in excludes):
                continue
            if len(w) > best_len:
                best_len = len(w)
                best_kw = kw
            break  # このキーワード内では最初にマッチした語で十分(長さはword単位)
    return best_kw


def articles_for_keyword(keyword: dict, articles: list[dict]) -> list[dict]:
    """記事タイトルにキーワードのいずれかの語を含む記事(除外語を含むものは除く)。"""
    words = keyword.get("words") or []
    excludes = keyword.get("exclude") or []
    result = []
    for a in articles:
        title_n = nfkc(a.get("title"))
        if any(ex in title_n for ex in excludes):
            continue
        if any(w in title_n for w in words):
            result.append(a)
    return result


def select_related_articles(
    item: dict, articles_by_id: dict[int, dict], keywords: list[dict] | None = None,
    limit: int = 4,
) -> tuple[str, list[dict]] | None:
    """商品名から関連キーワードを判定し、そのキーワードに該当する記事(直接ひも
    付いたレビュー記事を除く)を、同じコンビニ優先・新しい順に最大limit件返す。
    該当キーワードが無い、または記事が1件も無ければNoneを返す。"""
    kw = match_keyword_for_name(item.get("name", ""), keywords)
    if not kw:
        return None
    candidates = articles_for_keyword(kw, list(articles_by_id.values()))
    linked_ids = {a["id"] for a in item_reviews(item)}
    candidates = [a for a in candidates if a["id"] not in linked_ids]
    if not candidates:
        return None
    # 日付の新しい順に並べた後、同じコンビニのものを優先する安定ソート
    candidates.sort(key=lambda a: a.get("date") or "", reverse=True)
    candidates.sort(key=lambda a: a.get("chain") != item.get("chain"))
    return kw["label"], candidates[:limit]


def esc(s) -> str:
    return html.escape(s or "", quote=True)


def has_asset(name: str) -> bool:
    return (DOCS / "assets" / name).is_file()


def load_all_products() -> list[dict]:
    items = []
    for chain in CHAIN_NAME:
        d = PRODUCTS / chain
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                items.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return items


def week_start(date_str: str | None) -> str | None:
    """月曜始まりの週の開始日(ISO)を返す。"""
    if not date_str:
        return None
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        return None
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def effective_date(item: dict) -> str | None:
    """発売日が無ければ、一覧見出しの週開始日(list_week_start。ファミマの
    キャラクターくじ・雑貨等、詳細ページに発売日が無い商品向け)、
    それも無ければ初回検出日で代替する。

    first_seen_atだけに頼ると、先週の一覧で見つけた商品がクロール実行日を
    基準に今週へ入ってしまう(週アーカイブの取り違え)ため、list_week_startを
    優先する。
    """
    return (
        item.get("launch_date")
        or item.get("list_week_start")
        or (item.get("first_seen_at") or "")[:10]
        or None
    )


def item_url(item: dict) -> str:
    return f"items/{item['chain']}-{item['product_id']}.html"


def price_html(item: dict) -> str:
    incl = item.get("price_incl_tax")
    excl = item.get("price_excl_tax")
    if incl is None and excl is None:
        return ""
    parts = []
    if incl is not None:
        v = int(incl) if float(incl) == int(incl) else incl
        parts.append(f"税込{v}円")
    if excl is not None:
        v = int(excl) if float(excl) == int(excl) else excl
        parts.append(f"（税抜{v}円）")
    return "".join(parts)


def render_card(item: dict) -> str:
    chain_name = CHAIN_NAME.get(item["chain"], item["chain"])
    date = effective_date(item) or ""
    kcal = (item.get("nutrition") or {}).get("kcal")
    kcal_html = f'<span class="kcal">{kcal:g}kcal</span>' if kcal is not None else ""
    review_html = '<div class="review-badge">レビューあり</div>' if item_reviews(item) else ""
    return f"""<a class="item" href="{esc(item_url(item))}" data-kcal="{kcal if kcal is not None else ''}"
   data-date="{esc(date)}" data-chain="{esc(item['chain'])}">
  <div class="badge">{esc(chain_name)}</div>
  <div class="t">{esc(item['name'])}</div>
  <div class="price">{price_html(item)}{kcal_html}</div>
  <div class="meta">{esc(date)} 発売{('・' + esc(item['category'])) if item.get('category') else ''}</div>
  {review_html}
</a>"""


CSS = """
:root {
  --bg: #fafaf7; --card: #ffffff; --text: #1a1a1a; --muted: #6b6b6b;
  --accent: #0071e3; --line: #e5e2dc;
  --seven: #e06a00; --familymart: #00a650; --lawson: #0058a3;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #14151a; --card: #1e2027; --text: #e8e8e6; --muted: #9a9a96;
    --line: #2c2e36; --accent: #2997ff; }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text);
  font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; line-height: 1.6; }
a { color: inherit; }
header { padding: 24px 16px 12px; max-width: 980px; margin: 0 auto; }
header h1 a { text-decoration: none; font-size: 22px;
  display: inline-flex; align-items: center; gap: 8px; }
/* ロゴは文字とほぼ同じ高さに揃える(96px画像を縮小して表示。姉妹サイトと同じ) */
header h1 img { width: 32px; height: 32px; }
header p { color: var(--muted); font-size: 13px; margin-top: 4px; }
nav.crumbs { font-size: 12px; color: var(--muted); margin-top: 8px; }
nav.crumbs a { text-decoration: none; color: var(--accent); }
.sites { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; }
.sites .lbl { font-size: 12px; color: var(--muted); }
.sites a { font-size: 12px; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card); text-decoration: none; }
main { max-width: 980px; margin: 0 auto; padding: 8px 16px 48px; }
h2 { font-size: 18px; padding-left: 10px; border-left: 4px solid var(--accent); margin: 24px 0 12px; }
.chain-tabs { display: flex; gap: 8px; margin: 16px 0; flex-wrap: wrap; }
.chain-tabs button { font-size: 13px; padding: 6px 14px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card); cursor: pointer; font-family: inherit; }
.chain-tabs button[aria-selected="true"] { background: var(--text); color: var(--bg); font-weight: 600; }
.sort-bar { display: flex; gap: 8px; margin: 8px 0 16px; flex-wrap: wrap; align-items: center; }
.sort-bar select, .sort-bar input { font-size: 13px; padding: 5px 8px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--card); color: var(--text); font-family: inherit; }
.chain-group h3 { font-size: 15px; margin: 18px 0 8px; padding-left: 8px; border-left: 4px solid var(--line); }
.chain-group[data-chain="seven"] h3 { border-color: var(--seven); }
.chain-group[data-chain="familymart"] h3 { border-color: var(--familymart); }
.chain-group[data-chain="lawson"] h3 { border-color: var(--lawson); }
.grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
.item { display: block; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 12px; text-decoration: none; }
.item:hover { border-color: var(--accent); }
.item .badge { display: inline-block; font-size: 10px; font-weight: 700; color: #fff;
  border-radius: 4px; padding: 1px 6px; margin-bottom: 6px; }
.item[data-chain="seven"] .badge { background: var(--seven); }
.item[data-chain="familymart"] .badge { background: var(--familymart); }
.item[data-chain="lawson"] .badge { background: var(--lawson); }
.item .t { font-size: 14px; font-weight: 600; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 2.6em; }
.item .price { margin-top: 6px; font-size: 13px; }
.item .price .kcal { margin-left: 8px; color: var(--muted); }
.item .meta { font-size: 11px; color: var(--muted); margin-top: 4px; }
.weeklist { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
.weeklist a { text-decoration: none; padding: 10px 14px; background: var(--card);
  border: 1px solid var(--line); border-radius: 8px; font-size: 14px; }
.weeklist a:hover { border-color: var(--accent); }
.weeklist .n { color: var(--muted); font-size: 12px; margin-left: 8px; }
.chainlist { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.chainlist a { text-decoration: none; padding: 8px 16px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card); font-size: 13px; }
.detail { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 20px; margin-top: 12px; }
.detail h1 { font-size: 20px; margin-bottom: 8px; }
.detail .price { font-size: 18px; font-weight: 700; margin: 8px 0; }
.detail table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
.detail table th, .detail table td { border-bottom: 1px solid var(--line); text-align: left;
  padding: 6px 4px; }
.detail table th { color: var(--muted); font-weight: 500; width: 40%; }
.detail blockquote { border-left: 3px solid var(--accent); margin: 12px 0; padding: 8px 14px;
  color: var(--text); background: var(--bg); border-radius: 4px; font-size: 14px; }
.detail .source { font-size: 12px; color: var(--muted); margin-top: 4px; }
.detail .warn { font-size: 12px; color: var(--muted); margin-top: 2px; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
.tags span { font-size: 11px; border: 1px solid var(--line); border-radius: 4px; padding: 1px 6px; }
.searchbox { margin: 16px 0; }
.searchbox input { width: 100%; font-size: 14px; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--card); color: var(--text); font-family: inherit; }
footer { max-width: 980px; margin: 0 auto; padding: 16px; color: var(--muted); font-size: 12px;
  border-top: 1px solid var(--line); }
.about { max-width: 980px; margin: 40px auto 0; padding: 20px 16px 0; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 13px; line-height: 1.9; }
.about h2 { font-size: 14px; border-left-width: 3px; margin-bottom: 8px; color: var(--text); }
.about p { margin-top: 8px; }
.empty { color: var(--muted); font-size: 14px; padding: 12px 0; }
.item .review-badge { display: inline-block; margin-top: 4px; font-size: 10px; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 4px; padding: 0 5px; }
.review-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px; margin-top: 8px; }
.review-card { display: block; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; overflow: hidden; text-decoration: none; color: inherit; }
.review-card:hover { border-color: var(--accent); }
.review-card img { width: 100%; height: 110px; object-fit: cover; display: block; background: var(--line); }
.review-card .rc-body { padding: 8px 10px; }
.review-card .rc-t { font-size: 12.5px; font-weight: 600; display: -webkit-box;
  -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.4; }
.review-card .rc-date { color: var(--muted); font-size: 11px; margin-top: 4px; }
.detail .review-grid { margin-top: 4px; }
@media (max-width: 480px) {
  .review-grid { grid-template-columns: repeat(2, 1fr); }
}
"""


def page_shell(title: str, description: str, body: str, canonical: str, extra_head: str = "") -> str:
    site_url = CONFIG.get("site_url", "")
    page_title = f'{esc(title)}｜{esc(CONFIG["site_title"])}' if title != CONFIG["site_title"] else esc(title)

    ga_id = CONFIG.get("ga_measurement_id")
    ga_tag = ""
    if ga_id:
        ga_tag = (
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={esc(ga_id)}"></script>\n'
            "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            f"gtag('js',new Date());gtag('config','{esc(ga_id)}');</script>"
        )

    # 見出しの左に置くポチシリーズ共通のアイコン。ファビコン等と同じく、
    # 画像が置かれていなければ何も出さない
    logo_img = (
        '<img src="/assets/logo.png" alt="" width="32" height="32">'
        if has_asset("logo.png") else ""
    )
    icon_tags = []
    if has_asset("favicon.png"):
        icon_tags.append('<link rel="icon" type="image/png" href="/assets/favicon.png">')
    if has_asset("apple-touch-icon.png"):
        icon_tags.append('<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">')
    ogp_tags = []
    if has_asset("ogp.jpg"):
        ogp_tags = [
            f'<meta property="og:image" content="{esc(site_url)}assets/ogp.jpg">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            '<meta name="twitter:card" content="summary_large_image">',
        ]

    related = CONFIG.get("related_sites") or []
    links = "\n".join(
        f'<a href="{esc(s["url"])}">{esc(s["name"])}'
        + (f'<span class="lbl"> {esc(s["desc"])}</span>' if s.get("desc") else "")
        + "</a>"
        for s in related
    )
    related_html = f'<nav class="sites"><span class="lbl">関連サイト</span>\n{links}\n</nav>' if related else ""

    policy_url = CONFIG.get("policy_url", "")
    policy_link = (
        f'｜ <a href="{esc(policy_url)}">メディアポリシー</a>\n' if policy_url else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
{ga_tag}
{chr(10).join(icon_tags)}
<meta property="og:type" content="website">
<meta property="og:title" content="{page_title}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:site_name" content="{esc(CONFIG['site_title'])}">
<meta property="og:locale" content="ja_JP">
{chr(10).join(ogp_tags)}
<link rel="alternate" type="application/rss+xml" title="RSS" href="/rss.xml">
<style>{CSS}</style>
{extra_head}
</head>
<body>
<header>
<h1><a href="/">{logo_img}{esc(CONFIG["site_title"])}</a></h1>
<p>{esc(CONFIG["site_description"])}</p>
{related_html}
</header>
<main>
{body}
</main>
<footer>
価格・発売日・在庫は取得時点のものです。最新の状況は各社公式サイトでご確認ください。
当サイトはセブン-イレブン・ジャパン、ファミリーマート、ローソン各社とは関係のない非公式サイトです。
{policy_link}｜ <a href="/rss.xml">RSS</a> ｜ <a href="/weeks/">週別アーカイブ</a>
{related_html}
</footer>
</body>
</html>
"""


SORT_JS = """
<script>
(function () {
  // 社ごとのグループ(.chain-group)内でだけ並び替える。絞り込みはグループ単位で隠す
  var grids = Array.prototype.slice.call(document.querySelectorAll('[data-sortable]'));
  if (!grids.length) return;
  var select = document.getElementById('sort-select');
  var chainSel = document.getElementById('chain-filter');
  function apply() {
    var mode = select ? select.value : 'date';
    var chain = chainSel ? chainSel.value : 'all';
    grids.forEach(function (grid) {
      var group = grid.closest('.chain-group');
      if (group) group.style.display = (chain === 'all' || group.dataset.chain === chain) ? '' : 'none';
      var items = Array.prototype.slice.call(grid.querySelectorAll('.item'));
      items.sort(function (a, b) {
        if (mode === 'kcal') {
          return parseFloat(b.dataset.kcal || '-1') - parseFloat(a.dataset.kcal || '-1');
        }
        return (b.dataset.date || '').localeCompare(a.dataset.date || '');
      });
      items.forEach(function (el) { grid.appendChild(el); });
    });
  }
  if (select) select.addEventListener('change', apply);
  if (chainSel) chainSel.addEventListener('change', apply);
  apply();
})();
</script>
"""


def render_grouped(items: list[dict], sortable: bool = True) -> str:
    """商品を社ごと(config.jsonのchains順)にまとめ、各社の中は発売日の新しい順に並べる。"""
    attr = " data-sortable" if sortable else ""
    sections = []
    for c in CHAINS:
        group = [it for it in items if it["chain"] == c["slug"]]
        if not group:
            continue
        group.sort(key=lambda it: effective_date(it) or "", reverse=True)
        cards = "\n".join(render_card(it) for it in group)
        sections.append(
            f'<section class="chain-group" data-chain="{esc(c["slug"])}">\n'
            f'<h3>{esc(c["name"])} ({len(group)}件)</h3>\n'
            f'<div class="grid"{attr}>\n{cards}\n</div>\n</section>'
        )
    return "\n".join(sections)


SEARCH_JS = """
<script>
(function () {
  var box = document.getElementById('search-input');
  var results = document.getElementById('search-results');
  if (!box || !results) return;
  var index = null;
  box.addEventListener('input', function () {
    var q = box.value.trim();
    if (!q) { results.innerHTML = ''; return; }
    function render() {
      var lower = q.toLowerCase();
      var hits = index.filter(function (it) {
        return it.n.toLowerCase().indexOf(lower) !== -1;
      }).slice(0, 50);
      results.innerHTML = hits.map(function (it) {
        return '<a class="item" data-chain="' + it.c + '" href="' + it.u + '">' +
          '<div class="badge">' + it.cn + '</div><div class="t">' + it.n +
          '</div><div class="meta">' + (it.d || '') + '</div></a>';
      }).join('');
    }
    if (index) { render(); return; }
    fetch('/search-index.json').then(function (r) { return r.json(); }).then(function (data) {
      index = data; render();
    });
  });
})();
</script>
"""


def build_search_index(items: list[dict]) -> list[dict]:
    return [
        {
            "n": it["name"],
            "u": "/" + item_url(it),
            "c": it["chain"],
            "cn": CHAIN_NAME.get(it["chain"], it["chain"]),
            "d": effective_date(it) or "",
        }
        for it in items
    ]


def render_grid_page(title: str, description: str, items: list[dict], canonical: str,
                      show_filters: bool = True, review_chain: str | None = None) -> str:
    reviews_section = render_review_cards("ネタフルの最新レビュー", latest_reviews(review_chain, 5))
    if not items:
        body = f"<h2>{esc(title)}</h2><p class='empty'>商品がありません。</p>\n{reviews_section}"
        return page_shell(title, description, body, canonical)
    filters = ""
    if show_filters:
        filters = """<div class="sort-bar">
<label>並び替え: <select id="sort-select"><option value="date">発売日順</option>
<option value="kcal">カロリー順</option></select></label>
<label>絞り込み: <select id="chain-filter"><option value="all">すべて</option>
<option value="seven">セブンイレブン</option><option value="familymart">ファミリーマート</option>
<option value="lawson">ローソン</option></select></label>
</div>"""
    body = f"""<h2>{esc(title)} ({len(items)}件)</h2>
{filters}
{render_grouped(items)}
{SORT_JS if show_filters else ""}
{reviews_section}"""
    return page_shell(title, description, body, canonical)


def render_top(items: list[dict]) -> str:
    weeks = sorted({w for it in items if (w := week_start(effective_date(it)))}, reverse=True)
    weeks_set = set(weeks)

    # トップは「今日(Asia/Tokyo)を含む週」を主に表示する。来週発売分だけの
    # 週が最大の週になっていても、それをトップに出さない(未来週バグ対策)。
    # 今週が0件なら直近の過去週を出す。
    today_week = week_start(datetime.datetime.now(JST).date().isoformat())
    if today_week in weeks_set:
        current_week = today_week
    else:
        past_weeks = [w for w in weeks if w <= today_week]
        current_week = past_weeks[0] if past_weeks else (weeks[0] if weeks else None)
    current_items = [it for it in items if week_start(effective_date(it)) == current_week]

    next_week = None
    next_items: list[dict] = []
    if today_week:
        candidate_next = (
            datetime.date.fromisoformat(today_week) + datetime.timedelta(days=7)
        ).isoformat()
        if candidate_next in weeks_set and candidate_next != current_week:
            next_week = candidate_next
            next_items = [it for it in items if week_start(effective_date(it)) == next_week]

    site_url = CONFIG.get("site_url", "")
    search_html = """<div class="searchbox"><input id="search-input" type="search"
placeholder="商品名で検索（例: おむすび、チョコ）"></div>
<div class="grid" id="search-results"></div>"""

    weeks_link = f'<p><a href="/weeks/">週別アーカイブ一覧（全{len(weeks)}週）を見る →</a></p>' if weeks else ""

    chain_links = "\n".join(
        f'<a href="/chains/{c["slug"]}/">{esc(c["name"])}</a>' for c in CHAINS
    )

    about = CONFIG.get("about") or []
    about_html = ""
    if about:
        paras = "\n".join(f"<p>{esc(x)}</p>" for x in about)
        about_html = f'<section class="about"><h2>{esc(CONFIG["site_title"])}について</h2>\n{paras}\n</section>'

    current_label = f"{current_week} の週の新商品" if current_week else "新商品"
    next_section = ""
    if next_items:
        next_section = f"""<h2>来週の新商品 ({len(next_items)}件)</h2>
{render_grouped(next_items, sortable=False)}"""

    body = f"""<div class="chainlist">
{chain_links}
</div>
<h2>商品名で検索</h2>
{search_html}
{SEARCH_JS}
{render_review_cards("ネタフルの最新レビュー", latest_reviews(None, 6))}
<h2>{esc(current_label)} ({len(current_items)}件)</h2>
<div class="sort-bar">
<label>並び替え: <select id="sort-select"><option value="date">発売日順</option>
<option value="kcal">カロリー順</option></select></label>
<label>絞り込み: <select id="chain-filter"><option value="all">すべて</option>
<option value="seven">セブンイレブン</option><option value="familymart">ファミリーマート</option>
<option value="lawson">ローソン</option></select></label>
</div>
{render_grouped(current_items)}
{SORT_JS}
{next_section}
{weeks_link}
{about_html}"""
    return page_shell(CONFIG["site_title"], CONFIG["site_description"], body, site_url)


def render_weeks_index(items: list[dict]) -> str:
    by_week: dict[str, int] = {}
    for it in items:
        w = week_start(effective_date(it))
        if w:
            by_week[w] = by_week.get(w, 0) + 1
    rows = "\n".join(
        f'<a href="/weeks/{w}/">{w} の週<span class="n">{n}件</span></a>'
        for w, n in sorted(by_week.items(), reverse=True)
    )
    body = f"""<h2>週別アーカイブ ({len(by_week)}週)</h2>
<div class="weeklist">
{rows}
</div>"""
    return page_shell("週別アーカイブ", "週ごとの新商品一覧", body,
                       CONFIG.get("site_url", "") + "weeks/")


def render_chains_top() -> str:
    links = "\n".join(f'<a href="/chains/{c["slug"]}/">{esc(c["name"])}</a>' for c in CHAINS)
    body = f'<h2>コンビニ一覧</h2><div class="chainlist">{links}</div>'
    return page_shell("コンビニ一覧", "セブンイレブン・ファミリーマート・ローソンの商品一覧", body,
                       CONFIG.get("site_url", "") + "chains/")


def render_item_page(item: dict) -> str:
    chain_name = CHAIN_NAME.get(item["chain"], item["chain"])
    nutrition = item.get("nutrition")
    nut_rows = ""
    if nutrition:
        labels = [
            ("kcal", "熱量", "kcal"), ("protein_g", "たんぱく質", "g"),
            ("fat_g", "脂質", "g"), ("carbs_g", "炭水化物", "g"),
            ("sugar_g", "　糖質", "g"), ("fiber_g", "　食物繊維", "g"),
            ("salt_g", "食塩相当量", "g"),
        ]
        trs = []
        for key, label, unit in labels:
            v = nutrition.get(key)
            if v is None:
                continue
            trs.append(f"<tr><th>{esc(label)}</th><td>{v:g}{unit}</td></tr>")
        if trs:
            nut_rows = f"<h3 style='margin-top:16px;font-size:14px'>栄養成分</h3><table>{''.join(trs)}</table>"

    allergen_html = ""
    allergens = item.get("allergens")
    if allergens is not None:
        text = "、".join(allergens) if allergens else "特定原材料8品目は含まれていません"
        allergen_html = f"<p class='tags'><strong>アレルゲン：</strong>{esc(text)}</p>"

    spec_html = f"<p><strong>規格：</strong>{esc(item['spec'])}</p>" if item.get("spec") else ""
    region_html = ""
    # セブンは地域別ページごとに販売地域の書き方が少し違う(後から九州が加わる等)ので和集合をとる
    all_regions = list(item.get("regions") or [])
    for v in (item.get("variants") or {}).values():
        for r in v.get("regions") or []:
            if r not in all_regions:
                all_regions.append(r)
    if all_regions:
        region_html = f"<p><strong>販売地域：</strong>{esc('・'.join(all_regions))}</p>"
    elif item.get("region_text"):
        region_html = f"<p><strong>販売地域：</strong>{esc(item['region_text'])}</p>"

    # セブンは同じ商品が地域別ページ(URLの /hokkaido/ 等)に重複して載る。
    # 価格が地域で違うときだけ、地域名つきで価格を並べる
    variants_html = ""
    variants = item.get("variants") or {}
    if len({v.get("price_incl_tax") for v in variants.values()}) > 1:
        rows = []
        for key, v in variants.items():
            price = f"税込{v['price_incl_tax']:g}円" if v.get("price_incl_tax") is not None else ""
            rows.append(f"<tr><th>{esc(SEVEN_AREA_NAMES.get(key, key))}</th><td>{price}</td></tr>")
        variants_html = f"<h3 style='margin-top:16px;font-size:14px'>地域別の価格</h3><table>{''.join(rows)}</table>"

    official_url = item.get("official_url") or (item.get("official_urls") or [None])[0]
    quote_html = ""
    if item.get("description"):
        fetched = (item.get("fetched_at") or "")[:10]
        quote_html = f"""<blockquote cite="{esc(official_url or '')}">{esc(item['description'])}</blockquote>
<p class="source">出典：{esc(chain_name)}公式サイト（{esc(fetched)}時点） ・
<a href="{esc(official_url or '')}" target="_blank" rel="noopener">公式ページを見る</a></p>
<p class="warn">※現在は公式ページが削除されている、または内容が変更されている場合があります。</p>"""

    date = effective_date(item) or ""
    title = item["name"]
    canonical = CONFIG.get("site_url", "") + item_url(item)

    reviews_html = ""
    reviews = item_reviews(item)
    if reviews:
        reviews_html = f"""<h3 style='margin-top:16px;font-size:14px'>ネタフルのレビュー</h3>
<div class="review-grid">
{chr(10).join(render_review_card(a) for a in reviews)}
</div>"""

    related_html = ""
    related = select_related_articles(item, ARTICLES_BY_ID)
    if related:
        label, related_articles_list = related
        related_html = f"""<h3 style='margin-top:16px;font-size:14px'>関連するネタフルの記事（{esc(label)}）</h3>
<div class="review-grid">
{chr(10).join(render_review_card(a) for a in related_articles_list)}
</div>"""

    body = f"""<nav class="crumbs"><a href="/">トップ</a> &gt; <a href="/chains/{esc(item['chain'])}/">{esc(chain_name)}</a></nav>
<div class="detail">
<div class="badge" style="display:inline-block;font-size:11px;font-weight:700;color:#fff;
border-radius:4px;padding:2px 8px;background:var(--{esc(item['chain'])})">{esc(chain_name)}</div>
<h1>{esc(item['name'])}</h1>
<p class="price">{price_html(item)}</p>
<p><strong>発売日：</strong>{esc(date)}{('（' + esc(item.get('launch_text','')) + '）') if item.get('launch_text') else ''}</p>
{region_html}
{spec_html}
{quote_html}
{nut_rows}
{allergen_html}
{variants_html}
{reviews_html}
{related_html}
</div>"""
    return page_shell(title, item.get("description") or CONFIG["site_description"], body, canonical)


# AIの学習データ集め専用のクローラーは断る。商品説明は各社公式サイトからの引用なので、
# 引用元への配慮として学習用にまとめて持っていかれるのは避ける(2026-09-25 コグレ判断)。
# 検索やAI検索の表示に使うクローラー(Googlebot, Bingbot, OAI-SearchBot, ChatGPT-User,
# PerplexityBot 等)は集客の入口なので止めない。
# 漫画ポチ(manga.netaful.jp)は通信量課金対策で調査系ボットも止めているが、
# GitHub Pages は通信量に課金されないので、その理由はここには当てはまらない。
AI_TRAINING_BOTS = [
    "GPTBot", "CCBot", "Google-Extended", "Applebot-Extended", "anthropic-ai",
    "ClaudeBot", "Bytespider", "meta-externalagent", "cohere-training-data-crawler",
    "Diffbot", "Omgilibot",
]


def generate_robots() -> str:
    lines = [f"User-agent: {bot}" for bot in AI_TRAINING_BOTS]
    lines += ["Disallow: /", "", "User-agent: *", "Allow: /", "",
              f"Sitemap: {CONFIG.get('site_url', '')}sitemap.xml", ""]
    return "\n".join(lines)


def generate_rss(items: list[dict]) -> str:
    site_url = CONFIG.get("site_url", "")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    def sort_key(it):
        return it.get("first_seen_at") or ""

    recent = sorted(items, key=sort_key, reverse=True)[: CONFIG.get("rss_max_items", 100)]
    entries = []
    for it in recent:
        chain_name = CHAIN_NAME.get(it["chain"], it["chain"])
        title = f"【{chain_name}】{it['name']}"
        link = f"{site_url}{item_url(it)}"
        guid = f"{it['chain']}-{it['product_id']}"
        try:
            pub = datetime.datetime.fromisoformat(it.get("first_seen_at"))
            pub_html = "\n<pubDate>" + pub.strftime("%a, %d %b %Y %H:%M:%S %z") + "</pubDate>"
        except (TypeError, ValueError):
            pub_html = ""
        entries.append(
            f"""<item>
<title>{esc(title)}</title>
<link>{esc(link)}</link>
<guid isPermaLink="false">{esc(guid)}</guid>
<category>{esc(chain_name)}</category>{pub_html}
</item>"""
        )
    items_xml = "\n".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>{esc(CONFIG["site_title"])}</title>
<link>{esc(site_url)}</link>
<description>{esc(CONFIG["site_description"])}</description>
<lastBuildDate>{now}</lastBuildDate>
{items_xml}
</channel>
</rss>
"""


def generate_sitemap(items: list[dict], weeks: list[str]) -> str:
    site_url = CONFIG.get("site_url", "")
    today = datetime.date.today().isoformat()
    urls = [site_url, site_url + "weeks/", site_url + "chains/"]
    for c in CHAINS:
        urls.append(f"{site_url}chains/{c['slug']}/")
    for w in weeks:
        urls.append(f"{site_url}weeks/{w}/")
    for it in items:
        urls.append(site_url + item_url(it))
    body = "\n".join(
        f"<url><loc>{esc(u)}</loc><lastmod>{today}</lastmod></url>" for u in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{body}
</urlset>
"""


def main() -> int:
    items = load_all_products()
    DOCS.mkdir(parents=True, exist_ok=True)

    (DOCS / "index.html").write_text(render_top(items), encoding="utf-8")

    weeks = sorted({w for it in items if (w := week_start(effective_date(it)))})
    (DOCS / "weeks").mkdir(parents=True, exist_ok=True)
    (DOCS / "weeks" / "index.html").write_text(render_weeks_index(items), encoding="utf-8")
    for w in weeks:
        week_items = [it for it in items if week_start(effective_date(it)) == w]
        wdir = DOCS / "weeks" / w
        wdir.mkdir(parents=True, exist_ok=True)
        page = render_grid_page(
            f"{w} の週の新商品", f"{w}の週に発売された新商品一覧", week_items,
            CONFIG.get("site_url", "") + f"weeks/{w}/",
        )
        (wdir / "index.html").write_text(page, encoding="utf-8")

    # 商品削除(対象外カテゴリの掃除等)で商品が0件になった週は、既存のディレクトリが
    # 残ったままにならないよう削除する
    stale_weeks = 0
    if (DOCS / "weeks").is_dir():
        current_weeks = set(weeks)
        for wdir in (DOCS / "weeks").iterdir():
            if wdir.is_dir() and wdir.name not in current_weeks:
                shutil.rmtree(wdir)
                stale_weeks += 1

    (DOCS / "chains").mkdir(parents=True, exist_ok=True)
    (DOCS / "chains" / "index.html").write_text(render_chains_top(), encoding="utf-8")
    for c in CHAINS:
        cdir = DOCS / "chains" / c["slug"]
        cdir.mkdir(parents=True, exist_ok=True)
        chain_items = sorted(
            [it for it in items if it["chain"] == c["slug"]],
            key=lambda it: effective_date(it) or "", reverse=True,
        )
        page = render_grid_page(
            c["name"], f"{c['name']}の新商品一覧", chain_items,
            CONFIG.get("site_url", "") + f"chains/{c['slug']}/",
            review_chain=c["slug"],
        )
        (cdir / "index.html").write_text(page, encoding="utf-8")

    (DOCS / "items").mkdir(parents=True, exist_ok=True)
    for it in items:
        (DOCS / item_url(it)).write_text(render_item_page(it), encoding="utf-8")

    # 商品削除で不要になった旧itemページ(オーファン)を掃除する
    valid_item_files = {(DOCS / item_url(it)).name for it in items}
    stale_items = 0
    for f in (DOCS / "items").glob("*.html"):
        if f.name not in valid_item_files:
            f.unlink()
            stale_items += 1

    (DOCS / "search-index.json").write_text(
        json.dumps(build_search_index(items), ensure_ascii=False), encoding="utf-8"
    )
    (DOCS / "rss.xml").write_text(generate_rss(items), encoding="utf-8")
    (DOCS / "sitemap.xml").write_text(generate_sitemap(items, weeks), encoding="utf-8")
    (DOCS / "robots.txt").write_text(generate_robots(), encoding="utf-8")
    (DOCS / "CNAME").write_text(
        CONFIG.get("site_url", "").replace("https://", "").strip("/") + "\n", encoding="utf-8"
    )
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    print(
        f"generated: index.html, {len(weeks)} weeks, {len(CHAINS)} chain pages, "
        f"{len(items)} item pages, rss.xml, sitemap.xml, search-index.json"
        + (f" / removed {stale_items} stale item pages" if stale_items else "")
        + (f" / removed {stale_weeks} stale week dirs" if stale_weeks else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
