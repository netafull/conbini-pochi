# セイコーマートを6社目として追加（2026-11-25、ブランチ maru/add-seicomart）

調査仕様（2026-11-25確定）に基づき、コンビニポチにセイコーマートを追加した。
ミニストップ追加（a8a00d5b）の変更ファイル構成に倣っている。マージはオーナー確認後。

## 何を変えたか

- `config.json`: chains[] に `{slug: "seicomart", name: "セイコーマート", base_url: "https://www.seicomart.co.jp"}` を追加。`request_interval_seconds.seicomart = 3`。site_tagline・site_description・about の社名列挙を5社→6社に更新。
- `scripts/parsers.py`: `parse_seicomart_list(html, today)` と補助関数（`seicomart_price_text` 等）を追加。冒頭docstringの社名列挙も6社に更新。
  - 対象は https://www.seicomart.co.jp/instore/new.html 1ページのみ。商品ブロックは大型 `div.snd_large_ba2` とグリッド `div.grid_new` の2形式で、どちらも h5＝商品名、続く `<p>`＝説明文、`table.price_table`（地域／価格／発売日）。
  - **HTMLコメント内にコメントアウトされた過去週の商品ブロックが残っているため、パース前にコメントを除去する**（実ページでは除去前9件→除去後6件。テストで担保）。
  - 地域行は「北海道」「茨城県・埼玉県」の2行が基本。代表値は北海道行（価格・発売日。無い商品は最初の行）。全行は `variants` に保持し、`regions`／`region_text` は全行統合。
  - 価格は「1個 350円 (8%税込378.00円）」形式。先頭の数量表記（「N個」）を落としてから既存 `parse_price_pair()` で分解。
  - 発売日は「11月23日(月)」と年なし。既存 `resolve_month_day()`（デイリーヤマザキの見出しで使用）で実行日に最も近い年を補う。`parse_launch_date_iso()` は年を含む表記専用なので、年なし表記にはこちらが既存の流用先になる。
  - `product_id` は商品画像ファイル名の stem（`images/instore/new/20261123_01.jpg` → `20261123_01`）。
  - category は null（リボン画像のみでテキスト取得不可。アイコンは `icon_guess.py` が商品名から推定）。商品名は公式表記のまま（全角英字もそのまま）。
- `scripts/crawl.py`: `crawl_seicomart()` を追加し crawlers dict に登録。作りはデイリーヤマザキ型（一覧1ページのみ・詳細取得ループなし・既存商品は上書きしない・削除しない）。一覧が0件／取得失敗のときは `pending.json` の `seicomart.list` に記録し既存データに触れない。variants には地域別の価格に加えて発売日も保持し、`url` は一覧ページURL。
- `scripts/generate_site.py`: チェーン色 `--seicomart: #c2185b;` と `.chain-group[data-chain="seicomart"] h3`／`.item[data-chain="seicomart"] .badge` の2ルールを追加。フッターの免責文の社名列挙にセイコーマートを追加。チェーン列挙・チェーン別ページ・絞り込みUIは config 駆動で変更なし。
  - variants のキーは地域名（「北海道」「茨城県・埼玉県」）にした。セブンは地域別URLのスラッグだが、セイコーマートには地域別URLが無いため。商品ページの「地域別の価格」表は `SEVEN_AREA_NAMES.get(key, key)` でキーを表示する作りなので、地域名キーのままで正しく表示される。
- `data/pending.json`: `"seicomart": {}` を追加（既存の seven の保留エントリはそのまま）。
- `data/products/seicomart/`: 初回収集として最新週（2026年11月23日週）の6件を投入。
- `README.md`／`.github/workflows/update.yml`: 社名列挙・社数表記を6社に更新し、READMEにセイコーマートの取得元メモを追加。
- `tests/test_seicomart.py`（新規18件）: 通常ブロック（大型・グリッド両形式）、コメント内旧商品を拾わないこと、地域で価格・発売日が違う商品の代表値と variants、年跨ぎの発売日、北海道行が無い商品、クロールの保存・不変性、サイト生成（チェーン設定・地域別価格表・絞り込み）、実スナップショット（fixture がある環境のみ）。
- `tests/fixtures_manifest.txt`: `seicomart/new.html` を追記し、冒頭コメントの社名列挙を更新。
- `tests/test_ministop.py`: チェーン一覧を固定で検証していた `test_config_and_tags_list_five_chains` を6社に追随（メソッド名も `test_config_and_tags_list_all_chains` に変更）。セイコーマート追加に伴う必然の追随で、それ以外の検証内容は変えていない。
- `docs/`: `generate_site.py` で再生成（全ページにフッター・チェーン一覧が入るため差分は全ページ規模。ミニストップ追加時と同じ）。

## どう検証したか

- パーサ単体: 実ページ（2026-11-25取得）で6件を正しく解析。地域で価格が違う例（とよとみバターキャンディ 北海道220円／茨城・埼玉230円）、発売日が違う例（チョコモンブラン 北海道11月23日／茨城・埼玉11月24日）、全角商品名（ＢＩＧ！たっぷり焼きそばロール）の保持を確認。
- 初回収集: `python3 scripts/crawl.py seicomart` を実行し、一覧リクエスト1回で新規6件・失敗0件。`data/products/seicomart/` に6ファイル。
- サイト生成: `python3 scripts/generate_site.py` で再生成し、`docs/chains/seicomart/index.html`（6商品が表示される）、商品ページ6件（地域別価格表に「茨城県・埼玉県 税込248円」等）、トップページ、検索インデックス（6件）、サイトマップへの収録を確認。
- テスト: `tests/test_seicomart.py` は18件全通過（実スナップショット含む）。全体は fixtures の無い状態（fresh clone 相当）で151件全通過（main 時点133件＋新規18件、skipped 5）。
  - なお、この実行環境では `tests/fixtures/` にセイコーマート分しか無いため、fixtures ディレクトリが空でないことを条件に丸ごとスキップされる `test_parsers.py`／`test_crawl.py` が他社 fixture の不在でエラーになる（13件）。これは変更前からこの環境の部分 fixture が原因で起きるもので、今回の変更とは無関係（fixtures が揃った環境では通常どおり動く想定。未確認）。

## やらなかったこと・未確認のこと

- PR作成とマージはしていない（オーナー確認待ち）。ブランチ `maru/add-seicomart` は push 済み。
- `scripts/fetch_netaful_reviews.py` の TAGS は変更していない。ネタフルに「セイコーマート」タグが存在するか未確認のため（存在が確認できたら追加する）。
- チェーン色 `#c2185b`（マゼンタ系）は、既存5色（セブンのオレンジ `#e06a00` 含む）と被らないよう仮に選んだもの。最終判断はオーナー（ブランド色はオレンジ系だが、セブンと近接するため避けた）。
- 価格表記のバリエーションは現行週で確認できた「1個 ○○円」「○○円」の2形式のみ。「2個入」等の個数表記や、酒類など10%税率商品の表記は未確認。
- 地域行は現行週では全商品が「北海道／茨城・埼玉」の2行だった。片地域のみの商品や行構成の変化は継続観察が必要。
- 過去週分は公式にアーカイブが無いため初回収集は最新週1週分のみ。1週間まるごと巡回が失敗するとその週は永久に欠ける（異常検知は既存の仕組みに乗る）。
- ポチフル連携（pochi-full の sites.json・他サイトのフッター・ローテーター）はこのリポジトリ外の作業のため対象外。
