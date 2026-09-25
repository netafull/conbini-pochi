#!/usr/bin/env python3
"""3社（セブン-イレブン・ファミリーマート・ローソン）のHTMLから商品情報を
取り出す純粋関数群。ネットワークには一切触れない（テストからfixtureを渡して
呼べるようにするため）。html.parserは使わず正規表現で十分な程度に構造が
単純なので正規表現で抜き出す。

各 parse_*_detail() は取得失敗（テンプレートはあるが本文が無い等）のとき
None を返す。呼び出し側はNoneを「次回再試行」対象として扱う。
"""

from __future__ import annotations

import html as html_lib
import re


def _unescape(s: str | None) -> str | None:
    if s is None:
        return None
    return html_lib.unescape(s).strip()


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


# ---------------------------------------------------------------------------
# 共通: 栄養成分・価格
# ---------------------------------------------------------------------------

def parse_price_yen(text: str) -> int | None:
    m = re.search(r"([\d,]+)\s*円", text or "")
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def parse_price_pair(text: str) -> tuple[float | None, float | None]:
    """「248円（税込267.84円）」「221円（税込238円）」「297円(税込)」を
    (税抜, 税込) に分解する。税抜が無い(ローソン)場合は None。
    """
    if not text:
        return None, None
    text = text.replace("（", "(").replace("）", ")")
    incl_m = re.search(r"税込\s*([\d,]+(?:\.\d+)?)\s*円", text)
    incl = float(incl_m.group(1).replace(",", "")) if incl_m else None
    excl_m = re.match(r"\s*([\d,]+(?:\.\d+)?)\s*円", text)
    excl = float(excl_m.group(1).replace(",", "")) if excl_m else None
    if excl is not None and excl == int(excl):
        excl = int(excl)
    if incl is not None and incl == int(incl):
        incl = int(incl)
    # 「297円(税込)」のように税込だけの表記では、先頭の数字が税抜ではなく
    # 税込そのものを指す。"(税込)"が直後に続き、その中に金額が無い場合は
    # 税込専用表記と判断し、先頭の数字をinclに回してexclは無しとする
    if re.search(r"円\s*\(税込\)\s*$", text) and incl is None:
        incl = excl
        excl = None
    return excl, incl


def parse_launch_date_iso(text: str) -> str | None:
    """「2026年09月22日」「2026年9月22日」「2026.09.29」を ISO 日付にする。"""
    if not text:
        return None
    m = re.search(r"(\d{4})[年.](\d{1,2})[月.](\d{1,2})", text)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}"


# ---------------------------------------------------------------------------
# セブン-イレブン
# ---------------------------------------------------------------------------

SEVEN_ITEM_BLOCK_RE = re.compile(
    r'<div class="list_inner\s+-item-code-(\d{6})">(.*?)</div>\s*</div>\s*</div>',
    re.S,
)

# 一覧はカードごとに固定要素が並ぶので、コードで区切ってから個々を正規表現で抜く
SEVEN_CARD_SPLIT_RE = re.compile(r'-item-code-(\d{6})"')


def parse_seven_list(html: str) -> list[dict]:
    """一覧HTMLから商品カードを抜き出す。1商品につき地域ごとに複数行になりうる
    (セブンは地域別に別URLを持つため、同じ6桁コードが複数回現れる)。
    """
    items: list[dict] = []
    # list_inner を起点に、次の list_inner の手前までを1カードとして扱う
    starts = [
        (m.start(), m.group(1))
        for m in re.finditer(r'<div class="list_inner\s+-item-code-(\d{6})">', html)
    ]
    for i, (pos, code) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(html)
        block = html[pos:end]

        name_m = re.search(r'item_ttl">\s*<p>\s*<a[^>]*>(.*?)</a>', block, re.S)
        price_m = re.search(r'item_price">\s*<p>(.*?)</p>', block, re.S)
        launch_m = re.search(r'item_launch">\s*<p>(.*?)</p>', block, re.S)
        region_m = re.search(r'item_region">\s*<p>(.*?)</p>', block, re.S)
        url_m = re.search(r'<figure>\s*<a href="([^"]+)"', block)

        if not (name_m and url_m):
            continue
        items.append(
            {
                "product_id": code,
                "name": _unescape(_strip_tags(name_m.group(1))),
                "price_text": _unescape(_strip_tags(price_m.group(1))) if price_m else "",
                "launch_text": _unescape(_strip_tags(launch_m.group(1))) if launch_m else "",
                "region_text": _unescape(_strip_tags(region_m.group(1))) if region_m else "",
                "detail_url": url_m.group(1),
            }
        )
    return items


def parse_seven_detail(html: str) -> dict | None:
    """商品詳細（地域別ページ1枚ぶん）を解析する。

    テンプレートには非表示の `blacklist_error` ブロック(-item-code-hide-NNNNNN)
    が必ず存在する。表示用ブロック(-item-code-NNNNNN、hideを含まない)の中身が
    無ければ取得失敗として None を返す。
    """
    # 可視ブロック（クラス名に "-hide-" を含まない方）を取り出す
    m = re.search(
        r'<div class="detail_wrap -item-code-(?!hide-)(\d{6})">(.*?)<div class="allergy">(.*?)</div>\s*</div>',
        html,
        re.S,
    )
    if not m:
        return None
    code, body, allergy_block = m.group(1), m.group(2), m.group(3)

    name_m = re.search(r'item_ttl">\s*<h1>(.*?)</h1>', body, re.S)
    text_m = re.search(r'item_text">\s*<p>(.*?)</p>', body, re.S)
    price_m = re.search(r'item_price">\s*<p>(.*?)</p>', body, re.S)
    launch_m = re.search(r'item_launch">\s*<p>(.*?)</p>', body, re.S)
    region_m = re.search(r'item_region">\s*<p>(.*?)</p>', body, re.S)

    if not (text_m and price_m):
        # 本文が無ければ取得失敗（blacklist状態）とみなす
        return None

    # アレルゲン: 特定原材料8品目 dd のテキスト。「なし」ならリストは空
    allergen_m = re.search(r"特定原材料8品目</dt>\s*<dd>(.*?)</dd>", allergy_block, re.S)
    allergen_text = _unescape(_strip_tags(allergen_m.group(1))) if allergen_m else ""
    allergens = [] if (not allergen_text or allergen_text == "なし") else re.split(
        r"[・,、]", allergen_text
    )

    # 栄養成分: <tr><th>栄養成分</th><td>...raw...</td></tr>
    nutrition_m = re.search(r"栄養成分</th>\s*<td>(.*?)</td>", allergy_block, re.S)
    nutrition_raw = _unescape(_strip_tags(nutrition_m.group(1))) if nutrition_m else ""
    nutrition = parse_nutrition_seven(nutrition_raw) if nutrition_raw else None

    region_text = _unescape(_strip_tags(region_m.group(1))) if region_m else ""
    region_text = re.sub(r"^販売地域[：:]\s*", "", region_text or "")
    regions = [r for r in re.split(r"[・,、]", region_text) if r] if region_text else []

    return {
        "product_id": code,
        "name": _unescape(_strip_tags(name_m.group(1))) if name_m else None,
        "description": _unescape(_strip_tags(text_m.group(1))),
        "price_text": _unescape(_strip_tags(price_m.group(1))),
        "launch_text": _unescape(_strip_tags(launch_m.group(1))) if launch_m else "",
        "region_text": region_text,
        "regions": regions,
        "allergens": allergens,
        "nutrition": nutrition,
    }


def parse_nutrition_seven(raw: str) -> dict:
    """「熱量：329kcal、たんぱく質：16.0g、脂質：5.0g、炭水化物：57.5g
    （糖質：52.5g、食物繊維：5.0g）、食塩相当量：1.4g」を分解する。
    """
    def grab(label: str) -> float | None:
        m = re.search(label + r"[：:]\s*([\d.]+)", raw)
        return float(m.group(1)) if m else None

    return {
        "kcal": grab("熱量"),
        "protein_g": grab("たんぱく質"),
        "fat_g": grab("脂質"),
        "carbs_g": grab("炭水化物"),
        "sugar_g": grab("糖質"),
        "fiber_g": grab("食物繊維"),
        "salt_g": grab("食塩相当量"),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# ファミリーマート
# ---------------------------------------------------------------------------

FAMIMA_REGION_LABELS = [
    "北海道", "東北", "関東", "東海", "北陸", "関西", "中国・四国", "九州", "沖縄",
]


def split_famima_name(raw_name: str) -> tuple[str, str | None]:
    """「【北海道・東海】青磯海苔　海老天むす」から地域プレフィックスを分離する。"""
    m = re.match(r"^【([^】]+)】\s*(.*)$", raw_name)
    if m:
        return m.group(2), m.group(1)
    return raw_name, None


def parse_familymart_list(html: str) -> list[dict]:
    """商品カード一覧を抜き出す。

    ネスト構造(div>div>div)が深く`</div>`を数えて閉じを探すのは煩雑なので、
    セブン・ローソンと同じく次のカード開始位置までをカード本体とみなす。
    """
    items: list[dict] = []
    starts = [
        m.start() for m in re.finditer(r'<div class="ly-mod-infoset3[^"]*">', html)
    ]
    for i, pos in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(html)
        block = html[pos:end]
        meta_m = re.search(r'name="metaData" value="([^"]*)"', block)
        url_m = re.search(
            r'<a href="(https://www\.family\.co\.jp/goods/[a-z0-9]+/\d+\.html)"', block
        )
        cate_m = re.search(r'ly-mod-infoset3-cate">([^<]*)</p>', block)
        ttl_m = re.search(r'ly-mod-infoset3-ttl">(.*?)</h3>', block, re.S)
        price_m = re.search(r'ly-mod-infoset3-txt">(.*?)</p>', block, re.S)
        notes = re.findall(r'ly-mod-infoset3-notes">\s*(.*?)\s*</p>', block, re.S)

        if not (url_m and ttl_m):
            continue
        raw_name = _unescape(_strip_tags(ttl_m.group(1)))
        name, region_prefix = split_famima_name(raw_name)
        items.append(
            {
                "product_id": url_m.group(1).rsplit("/", 1)[-1].replace(".html", ""),
                "name": name,
                "region_prefix": region_prefix,
                "category": _unescape(_strip_tags(cate_m.group(1))) if cate_m else "",
                "price_text": _unescape(_strip_tags(price_m.group(1))) if price_m else "",
                "sales_area_meta": meta_m.group(1) if meta_m else "",
                "notes": [_unescape(_strip_tags(n)) for n in notes],
                "detail_url": url_m.group(1),
            }
        )
    return items


def parse_familymart_detail(html: str) -> dict | None:
    lead_m = re.search(r'ly-goods-lead">(.*?)</p>', html, re.S)
    price_m = re.search(r'ly-kakaku-usual">(.*?)</span>', html, re.S)
    spec_lis = re.findall(r'ly-goods-spec">.*?</ul>', html, re.S)
    launch_m = re.search(r"発売日[：:]\s*(\d{4}年\d{1,2}月\d{1,2}日)", html)

    if not lead_m:
        # 本文が無ければ取得失敗として扱う
        return None

    # 地域タグ: ly-reg-tag(該当) / ly-reg-tag-none(非該当)。先頭2つ("注目"
    # "発売地域"のラベル)を除いた9件が北海道〜沖縄に対応する
    tag_matches = re.findall(
        r'<p class="(ly-reg-tag(?:-none)?)\s*">([^<]*)</p>', html
    )
    regions: list[str] = []
    pref_tags = [t for t in tag_matches if t[1].strip() in FAMIMA_REGION_LABELS]
    for cls, label in pref_tags:
        if cls == "ly-reg-tag":
            regions.append(label.strip())

    nutrition = None
    nut_m = re.search(r'item_nutritional_info">(.*?)</div>', html, re.S)
    if nut_m:
        titles = re.findall(r'class="tit_nut">([^<]+)<br', nut_m.group(1))
        values = re.findall(r'class="con_nut">([\d.]+)', nut_m.group(1))
        table = dict(zip([t.strip() for t in titles], values))
        nutrition = {
            "kcal": _to_float(table.get("熱量")),
            "protein_g": _to_float(table.get("たんぱく質")),
            "fat_g": _to_float(table.get("脂質")),
            "carbs_g": _to_float(table.get("炭水化物")),
            "sugar_g": None,
            "fiber_g": None,
            "salt_g": _to_float(table.get("食塩相当量")),
            "raw": nut_m.group(1),
        }

    allergens = None
    alg_m = re.search(r'item_allergen">(.*?)</div>\s*</div>', html, re.S)
    if alg_m:
        allergens = re.findall(r'alt="([^"]+)"', alg_m.group(1))

    return {
        "description": _unescape(_strip_tags(lead_m.group(1))),
        "price_text": _unescape(_strip_tags(price_m.group(1))) if price_m else "",
        "launch_text": launch_m.group(1) if launch_m else "",
        "regions": regions,
        "spec_text": _unescape(_strip_tags(spec_lis[0])) if spec_lis else "",
        "nutrition": nutrition,
        "allergens": allergens,
    }


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ローソン
# ---------------------------------------------------------------------------

def parse_meta_refresh(html: str) -> str | None:
    m = re.search(
        r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*content=["\'][^;]*;\s*URL=([^"\']+)',
        html,
        re.I,
    )
    return m.group(1) if m else None


def parse_lawson_nav_urls(html: str) -> list[str]:
    """発売日別ページへのリンク一覧(現在のページ自身は含まれないので、
    呼び出し側で最初にたどり着いたURLも合わせて対象にすること)。"""
    nav_m = re.search(r'<ul class="contentsNav">(.*?)</ul>', html, re.S)
    if not nav_m:
        return []
    return re.findall(r'<a href="([^"]+)">', nav_m.group(1))


def parse_lawson_list(html: str) -> list[dict]:
    """商品カード一覧を抜き出す。

    各カードは `<li><a href="/recommend/original/detail/...">...</a>
    [<div class="smalltxt">...(自身も<li>を含む)...</div>]</li>` という形で、
    末尾の注記divが入れ子の<li>を持つことがあるため、`</li>`を頼りに閉じを
    探すと注記側の内側の</li>で止まってしまう。代わりにカード開始位置
    (`<a href="/recommend/original/detail/...`)で区切る。
    """
    items: list[dict] = []
    list_m = re.search(r'<ul class="col-3\s+heightLineParent">', html)
    if not list_m:
        return items
    # 商品リンクは col-3 のグリッド内にしか現れないため、開始位置以降の
    # 全体から拾えばよい(終端を`</ul>`で探すと、末尾の注記div自身が持つ
    # 入れ子の`<ul><li>...</li></ul>`に引っかかって早期に止まってしまう)
    body = html[list_m.end():]

    starts = [
        m.start()
        for m in re.finditer(r'<a href="(/recommend/original/detail/[^"]+)"', body)
    ]
    for i, pos in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        block = body[pos:end]
        url_m = re.search(r'<a href="([^"]+)"', block)
        ttl_m = re.search(r'class="ttl">([^<]*)</p>', block)
        price_m = re.search(r'class="price">(.*?)</p>', block, re.S)
        date_m = re.search(r'class="date">.*?<span>([^<]*)</span>', block, re.S)
        note_m = re.search(r'class="smalltxt"[^>]*>(.*?)</div>', block, re.S)
        if not (url_m and ttl_m):
            continue
        url = url_m.group(1)
        pid = url.rsplit("/", 1)[-1].split("_", 1)[0]
        items.append(
            {
                "product_id": pid,
                "name": _unescape(ttl_m.group(1)),
                "price_text": _unescape(_strip_tags(price_m.group(1))) if price_m else "",
                "launch_text": _unescape(date_m.group(1)) if date_m else "",
                "note": _unescape(_strip_tags(note_m.group(1))) if note_m else "",
                "detail_url": url,
            }
        )
    return items


LAWSON_ALLERGEN_28 = [
    "えび", "かに", "くるみ", "小麦", "そば", "卵", "乳成分",
    "落花生", "アーモンド", "あわび", "いか", "いくら", "オレンジ", "カシューナッツ",
    "キウイフルーツ", "牛肉", "ごま", "さけ", "さば", "大豆", "鶏肉",
    "バナナ", "豚肉", "マカダミアナッツ", "もも", "やまいも", "りんご", "ゼラチン",
]


def parse_lawson_detail(html: str) -> dict | None:
    text_m = re.search(r'<p class="text">(.*?)</p>', html, re.S)
    price_m = re.search(r'<dl class="price">.*?<dd>(.*?)</dd>', html, re.S)
    if not (text_m and price_m):
        return None

    spec_m = re.search(r'<dl class="detail">(.*?)</dl>', html, re.S)
    spec_text = ""
    if spec_m:
        dt = re.search(r"<dt>([^<]*)</dt>", spec_m.group(1))
        dd = re.search(r"<dd>([^<]*)</dd>", spec_m.group(1))
        if dt and dd:
            spec_text = f"{_unescape(dt.group(1))}: {_unescape(dd.group(1))}"

    nutrition = None
    nut_m = re.search(
        r'class="nutritionFacts_table[^"]*">(.*?)</div>\s*</div>\s*</div>', html, re.S
    )
    if nut_m:
        block = nut_m.group(1)
        pairs = re.findall(r"<dt>([^<]*)</dt>\s*<dd>([^<]*)</dd>", block)
        table = {k.strip(): v.strip() for k, v in pairs}
        nutrition = {
            "kcal": _to_float(re.sub("[^0-9.]", "", table.get("熱量", ""))),
            "protein_g": _to_float(re.sub("[^0-9.]", "", table.get("たんぱく質", ""))),
            "fat_g": _to_float(re.sub("[^0-9.]", "", table.get("脂質", ""))),
            "carbs_g": _to_float(re.sub("[^0-9.]", "", table.get("炭水化物", ""))),
            "sugar_g": _to_float(re.sub("[^0-9.]", "", table.get("糖質", ""))),
            "fiber_g": _to_float(re.sub("[^0-9.]", "", table.get("食物繊維", ""))),
            "salt_g": _to_float(re.sub("[^0-9.]", "", table.get("食塩相当量", ""))),
            "raw": block,
        }

    allergens = None
    alg_m = re.search(r'class="allergie_table[^"]*">(.*?)<ul>', html, re.S)
    if alg_m:
        cells = re.findall(r'<td([^>]*)>([^<]*)</td>', alg_m.group(1))
        on_names = [name for attrs, name in cells if 'class="on"' in attrs]
        allergens = on_names

    return {
        "description": _unescape(_strip_tags(text_m.group(1))),
        "price_text": _unescape(_strip_tags(price_m.group(1))),
        "spec_text": spec_text,
        "nutrition": nutrition,
        "allergens": allergens,
    }
