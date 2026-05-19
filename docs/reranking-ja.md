# リランキング

このドキュメントでは、mrag のリランキング（CrossEncoder による検索結果の並び替え）について解説します。

**リランキング**は、検索（retrieval）で取得した上位 N 件の候補に対して、別のモデル（CrossEncoder）を使ってクエリとの関連度をより精密に再評価し、並び順を組み直す処理です。検索段階で「広めに取った候補」を、最終的に「より精度の高い順序」で返したいときに使います。

mrag では、プロファイルの `rerank.enabled: true` を設定するだけで、`keyword` / `vector` / `hybrid` / `parent_child` いずれの検索戦略の結果にも追加適用できます。デフォルトは `false`（リランキングなし）です。


## 処理の概要

リランキングを有効にすると、検索フローは次のようになります：

1. 通常の検索戦略（`keyword` / `vector` / `hybrid` など）で `rerank.top_n` 件まで候補を取得
2. CrossEncoder モデルに `(クエリ, 候補チャンクの本文)` のペアを渡し、関連度スコアを 1 件ずつ算出
3. CrossEncoder のスコア降順で並び替え
4. 呼び出し側で要求された最終件数（CLI なら `--top-k`、API なら `top_k`）まで絞って返す

> 重要：リランキングは**検索時にしか動かない後段処理**です。インデックスの内容には触れないので、`rerank.*` を変更しても再インデックスは不要です。次回の検索から新設定が反映されます。


## 設定方法

`profiles/<profile-name>.yaml` で `rerank` セクションを次のように設定します。

```yaml
rerank:
  enabled: true                                              # false（デフォルト） | true
  provider: sentence-transformers
  model: hotchpotch/japanese-reranker-cross-encoder-small-v1 # デフォルト。日本語向け
  max_length: 512                                            # CrossEncoder のトークン上限（変更非推奨）
  top_n: 30                                                  # リランキング対象の候補数
```

各フィールドの意味：

- **`enabled`** — `true` でリランキングを有効化。`false` ならスキップして検索結果をそのまま返す
- **`provider`** — リランクの実装（現状は `sentence-transformers` のみ）
- **`model`** — CrossEncoder モデル名。HuggingFace のモデル ID。デフォルトは日本語向けの軽量モデル
- **`max_length`** — モデルに渡す 1 件あたりの最大トークン長。デフォルト 512（後述の注意あり）
- **`top_n`** — リランキング対象として取得する候補数。**最終 `top_k` より十分大きく**設定する


## インストール

リランキング機能はオプショナル依存です。利用する前に extras をインストールしてください：

```bash
uv pip install -e ".[reranker]"
```

これにより `sentence-transformers` が入り、初回検索時に指定モデルが HuggingFace から自動ダウンロードされます。


## `max_length` と BERT のトークン制限

`rerank.model` のデフォルト `hotchpotch/japanese-reranker-cross-encoder-small-v1` をはじめとする **BERT 系 CrossEncoder には、position embedding が 514 までという上限**があります。`rerank.max_length: 512`（デフォルト）は、トークナイザーが入力をこの上限内に切り詰める設定です。

> 注意：BERT 系モデルでは **`max_length` を 512 より大きくしないでください**。上限を超えると、推論時に「out-of-bounds」エラーで落ちます。長文を扱うために `max_length` を上げたい場合はより長文に対応するモデルへの変更を検討してください。


## `parent_child` プロファイルとの相性

`retrieval.strategy: parent_child` のプロファイルで `rerank.enabled: true` を併用する場合は以下の点に留意してください。

- `parent_child` は子チャンクで検索したあと、表示用に**約 3000 文字の親チャンク**に置換します
- CrossEncoder は親チャンクを受け取りますが、`max_length: 512` で**冒頭の約 512 トークン分のみ**を見てスコアリングします
- 結果として、親チャンクの後半に含まれる情報が評価に寄与せず、スコアの信頼性が低下する可能性があります

`parent_child` を使う場合は `rerank.enabled: false` で運用するほうが専ら安全です。


## CLI / API での一時無効化

プロファイルで `enabled: true` にしていても、検索の都度オプションで切ることができます。検索精度の差分を確認したいときや、レイテンシ優先のシーンで有効です。

```bash
# 単発の検索でリランキングを無効化
mrag search "クエリ" --no-rerank

# 評価ランでリランキングを無効化
mrag eval "クエリ" --no-rerank

# API サーバー全体でリランキングを無効化（このサーバーセッション中ずっとオフ）
mrag serve --no-rerank
```


## 検索結果メタデータの `retrieval_score`

リランキングが適用されると、各検索結果に **`retrieval_score`** が付与されます。これはリランキング**前**のスコア（検索戦略本来の関連度）で、CrossEncoder の再評価結果との差分を確認するのに使います。

格納される位置はインターフェースによって異なります：

- **`mrag search --json`** — `score` と並ぶ**トップレベルフィールド**として出力されます（メタデータ内にも同値が残ります）
- **`mrag serve` の API レスポンス** — 各結果の **`metadata.retrieval_score`** に入ります（トップレベルには出ません）
- **`mrag eval`** など人間向け出力 — `score=0.81  (retrieval=0.42)` のように `score` の隣にカッコ書きで表示されます

CLI の `--json` 出力で確認する例：

```bash
mrag search "クエリ" --json | jq '.results[] | {score, retrieval_score: .retrieval_score}'
```

- `score` — リランキング**後**のスコア（CrossEncoder の出力）
- `retrieval_score` — リランキング**前**のスコア（hybrid なら RRF、keyword なら BM25 など）

これにより「検索段階で 1 位だった候補が、リランキング後にどう動いたか」が観察できます。リランクのチューニングや効果検証に便利です。


## `top_n` の決め方

`rerank.top_n` はリランキング対象として取得する候補数で、デフォルトは **30** です。最終的に返す件数（`retrieval.top_k`、デフォルト 8）より大きく設定する必要があります。

選び方の目安：

- **`top_n = top_k × 3〜5`** くらいから始めてみる
- 大きくするほどリランキングの効果は出やすいが、CrossEncoder の推論コストが線形に増える
- 小さすぎると「検索で 10 位だったが本来は 1 位だった」候補を取りこぼす可能性がある

> リランキングは1件あたりの推論コストがそれなりにかかります。`top_n` を闇雲に大きくすると検索レイテンシが目に見えて増えるので、用途と許容レイテンシを見て決めてください。


## モデル選択

デフォルトの `hotchpotch/japanese-reranker-cross-encoder-small-v1` は日本語向けの軽量モデルです。用途に応じて変更できます：

- **日本語中心 / 軽量**：`hotchpotch/japanese-reranker-cross-encoder-small-v1`（デフォルト）
- **日本語中心 / 中精度**：`hotchpotch/japanese-reranker-cross-encoder-base-v1` 等
- **多言語**：`BAAI/bge-reranker-base` / `BAAI/bge-reranker-v2-m3` など

モデルを変更しても `rerank.*` は `profile_hash` の対象外なので、**再インデックスは不要**です。次回の検索から新モデルが読み込まれます（初回ロード時にダウンロードが入ります）。


## Tips

- リランキングは検索品質を上げる一方、**検索レイテンシを増やします**。ユーザー対話型ツールに組み込む場合はレイテンシ予算を確認してから有効化を判断してください
- `--no-rerank` での A/B 比較は **同じクエリで** `score` と `retrieval_score` を見比べると分かりやすいです
- `parent_child` 以外の戦略（`hybrid` / `vector` / `keyword`）では、リランキングは効果がはっきり出やすい傾向があります
- リランクのモデルキャッシュは HuggingFace のデフォルトキャッシュディレクトリ（`~/.cache/huggingface/`）に保存されます。スペースが気になる場合は不要なモデルを手で削除してください
