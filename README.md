# コンビニポチ

セブン-イレブン・ファミリーマート・ローソンの新商品を1日1回巡回し、
商品情報をアーカイブとして蓄積し続けるサイト（公開予定 https://konbini.netaful.jp/ ）。
各社の公式サイトは販売終了した商品のページを削除してしまうため、
「いつ・何が・いくらで発売されたか」を後から引けることが価値の中心。

姉妹サイト [電書ポチ](https://book.netaful.jp/)・[家電ポチ](https://kaden.netaful.jp/)・
[林檎ポチ](https://apple.netaful.jp/)・[漫画ポチ](https://manga.netaful.jp/)と同じく、
pip依存なし(Python標準ライブラリのみ、HTML解析は正規表現)で作られている。

## 使い方

```bash
python3 scripts/crawl.py          # 3社の新商品を取得して data/products/ に蓄積
python3 scripts/generate_site.py  # docs/ 一式(index.html, 商品ページ, RSS等)を生成
python3 -m unittest discover tests -v   # tests/fixtures/ を相手にしたパーサのテスト
```

GitHub Actionsで1日1回自動実行する想定（`.github/workflows/update.yml`）。
ただし本リポジトリはまだGitHub上には作成しておらず、Cloud Scheduler等の
定期実行設定もこれから（運営者の確認後に行う）。

## サイトの構成

- **トップ** — 最新週の新商品を3社まとめて表示。並び替え(発売日順/カロリー順)・
  社別絞り込みのJS付き
- **週別アーカイブ**(`/weeks/YYYY-MM-DD/`) — 月曜始まりの週ごとに新商品を蓄積
- **社別一覧**(`/chains/{seven|familymart|lawson}/`)
- **商品ページ**(`/items/{chain}-{product_id}.html`) — 価格・発売日・販売地域・
  栄養成分・アレルゲンを表示。説明文は `<blockquote cite="公式URL">` で引用し、
  出典と取得日・公式ページへのリンクを添える。「現在は公式ページが削除されて
  いる、または内容が変更されている場合があります」という一言も添える
- **キーワード検索**(トップページ内) — `docs/search-index.json` を使ったクライアント
  サイドの簡易検索
- RSS(`rss.xml`)・サイトマップ・robots.txt・OGP・`CNAME`(konbini.netaful.jp)・
  `.nojekyll`

商品画像は公式のものを保存・表示しない。将来AI生成の「イメージ画像」を
付ける構想があるため、商品データに `image` フィールドをnullで用意してある。

## クロールのマナー

- リクエスト間隔: セブン6秒以上、ファミリーマート・ローソン3秒以上
  （`config.json` の `request_interval_seconds`、`scripts/fetch.py` のThrottleクラス
  が実際の待ち時間を管理する）
- タイムアウト20秒、リトライ1回まで(`scripts/fetch.py`)
- User-Agentは正直なもの(`KonbiniPochi/1.0 (+https://konbini.netaful.jp/)`)を使う。
  403やImperva/Incapsulaのチャレンジページを検知した場合は`BlockedError`を
  投げて処理を打ち切る。**UA偽装などの回避策は実装していない**(判断は運営者)
- 取得済み商品の詳細ページは再取得しない。セブンだけは例外で、同じ商品の
  未取得の地域URL(`variants`)が一覧に新しく現れたときだけ追加で取得する

## データ構造

`data/products/{seven|familymart|lawson}/{product_id}.json` に1商品1ファイル
(gitの差分を小さくするため)。主なフィールド:

```jsonc
{
  "chain": "seven",
  "product_id": "044723",          // セブンは6桁コード、ファミマはURL末尾7桁、
                                    // ローソンはURLの"_"前の数字
  "name": "...",
  "category": null,                 // カテゴリがあるのはファミマのみ
  "price_excl_tax": 248,            // 無ければnull(ローソンは税込のみ表示のため常にnull)
  "price_incl_tax": 267.84,
  "launch_date": "2026-09-22",      // ISO日付。パース失敗時はnull
  "launch_text": "2026年09月22日（火）以降順次発売",  // 原文
  "regions": ["北海道"],            // 無ければnull
  "region_text": "...",
  "description": "...",             // 商品ページでは出典明記の引用として表示
  "nutrition": { "kcal": 176.0, "protein_g": 4.8, "fat_g": 0.9,
                 "carbs_g": 37.8, "sugar_g": 36.3, "fiber_g": 1.5,
                 "salt_g": 1.4, "raw": "..." },  // 無ければnull(おむすび等)
  "allergens": [],                  // なしなら空配列、項目自体が無ければnull
  "spec": null,
  "variants": { "hokkaido": {...} },// セブンのみ。地域別の価格・販売地域
  "official_url": "https://...",
  "official_urls": ["https://..."], // セブンのみ、地域別URLの一覧
  "first_seen_at": "2026-09-25T20:35:59+09:00",
  "fetched_at": "2026-09-25T20:35:59+09:00",
  "source_list": "thisweek",
  "image": null                     // 常にnull(将来のAI生成画像用の予約)
}
```

取得失敗(通信エラー・想定外の構造・セブンのblacklist状態)は
`data/pending.json` に社ごとに隔離して記録し、次回`crawl.py`実行時に
そのURLだけ再試行する。1社の失敗が他社の処理を止めないよう、
`crawl.py`の3社分の処理は互いに独立した例外境界を持つ。

既存ファイルは上書きしない(アーカイブなので初回取得が正)。
唯一の例外はセブンの`variants`で、一覧に新しい地域URLが出てきたときだけ追記する。

## 各社の取得元と実装メモ

### セブン-イレブン
一覧(`/products/a/thisweek/` `/products/a/nextweek/`)は同じ6桁コードが
地域別URL(`/hokkaido/` `/kanto/`等)で複数回登場する。初回発見時に全地域URLを
1回ずつ取得し、`variants`にまとめる。詳細ページのテンプレートには常に
非表示の`blacklist_error`ブロック(`-item-code-hide-NNNNNN`)があり、表示用
ブロック(`-item-code-NNNNNN`、hideを含まない方)の本文が空なら取得失敗として
`pending.json`に記録し次回再試行する。

### ファミリーマート
一覧(`newgoods.html` / `newgoods/nextweek.html` / `newgoods/lastweek.html`)の
商品名には「【北海道・東海】」のような地域プレフィックスが付くことがあり、
表示名から分離して`region_text`として保持する。栄養成分・アレルゲンは
カテゴリによって存在しない(おむすび等)。**これは異常ではなく正常**なので、
`nutrition`/`allergens`がnullでも取得失敗として扱わない。実際に確認した
栄養成分の内訳は「熱量・たんぱく質・脂質・炭水化物(合計のみ)・食塩相当量」の
5項目で、事前情報にあった炭水化物の糖質・食物繊維の内訳はファミマの
テーブルには無かった(`sugar_g`/`fiber_g`は常にnull)。

### ローソン
`/recommend/new/`はmeta refreshで発売日別ページへ飛び、そのページの
`ul.contentsNav`から他の発売日別ページ(実測で約3〜4週間分)を芋づる式に
たどる。発売日は発売後に詳細ページから消えるため、一覧側の`launch_text`を
必ず保存する(詳細ページ側にも`p.ico_new`として発売前は出ているが、
これに依存すると発売後に取れなくなる)。

## 事前情報とHTML構造の差異

実際に取得したHTMLを確認した範囲では、指示書に書かれた要素・クラス名は
ほぼそのまま一致していた。唯一の差異はファミリーマートの栄養成分テーブルで、
上記のとおり「糖質・食物繊維の内訳」列が実際には存在しなかった(合計の
炭水化物のみ)。この点はデータ構造上`sugar_g`/`fiber_g`をnullのまま許容する
ことで対応している。

## 未着手

- GitHubリポジトリ作成・push・GitHub Pages設定・Cloud Scheduler設定
  (運営者の確認後に行う)
- 画像(ファビコン・OGP・ロゴ)。`docs/assets/`に配置すれば自動で参照される
  (`favicon.png` 32px / `apple-touch-icon.png` 180px / `ogp.jpg` 1200x630 /
  `logo.png` 96px)。無い間は参照しない
- GA4測定ID(`config.json`の`ga_measurement_id`が空文字の間はタグを出力しない)
- 商品のAI生成イメージ画像(`image`フィールドは既に用意済み)
