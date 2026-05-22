# 検索ストラテジー

このドキュメントでは、mrag に実装されている 4 つの検索ストラテジー（retrieval strategy）について解説します。

検索ストラテジーは「インデックスされたチャンクに対して検索する方法」を決める設定です。ドキュメントの種類やクエリの傾向（自然文かキーワード列か、表記揺れがあるかなど）によって、mragではナレッジベースの性質に応じて複数の検索ストラテジーを適用することが可能です。

mrag では `profiles/<profile-name>.yaml` の `retrieval.strategy` フィールドで戦略を指定します。


## 4 つの戦略

| 戦略 | ひとことで言うと | 必要なもの |
|------|------------------|------------|
| `keyword` | SQLite FTS5 BM25 による語彙ベースの全文検索 | SQLite のみ |
| `vector` | Qdrant のコサイン類似度によるベクター類似検索 | Qdrant + Embedding Model |
| `hybrid` | `keyword` と `vector` の結果を融合（デフォルト） | Qdrant + Embedding Model |
| `parent_child` | 子チャンクで検索し親チャンクで返す Small-to-Big 方式 | Qdrant + Embedding Model／`chunking.strategy: parent_child` と必ずペア |

ざっくりとした選び方：

- **クエリに一致する単語・専門用語をピンポイントで引きたい** → `keyword`
- **表記揺れや言い換えに強くしたい・自然文クエリを使いたい** → `vector`
- **どちらの強みも欲しい・最初に試すならこれ** → `hybrid`
- **長文ドキュメントで広い文脈ごとヒットを返したい** → `parent_child`

> `parent_child` を選ぶ場合は、`chunking.strategy` も `parent_child` に合わせる必要があります（片方だけ設定するとプロファイル検証エラーになります）。


## `keyword` — BM25 によるキーワード検索

SQLite FTS5 の BM25 スコアリングを使った、語彙ベースの全文検索です。Qdrant も Embedding Model も不要で、軽量に動きます。

```yaml
retrieval:
  strategy: keyword
  top_k: 8                 # 最終的に返す件数
```

挙動：

1. クエリテキストを NFKC 正規化（インデックス時と同じ正規化を適用）
2. FTS5 トークナイザー（vaporetto または trigram）を経由して `MATCH` クエリを実行
3. BM25 スコアの良い順に最大 `top_k` 件を返す

得意・不得意：

- **得意**：固有名詞や型番、コマンド名など、表記が固定された語彙の検索
- **不得意**：言い換えや同義語（「メモリ」と「RAM」など）。語彙そのものがチャンク内に出現しないとヒットしません

> vaporetto トークナイザーを使っていると、空白を含まない連続日本語テキスト（例：`温度センサの基本仕様`）は形態素列のフレーズマッチとして解釈されます。長文の自然文を投げると 1 つのフレーズ扱いになり、ほぼヒットしません。キーワード検索では `温度 センサ 仕様` のように**空白でトークンを区切って投げる**のがコツです。多くの場合、AI エージェントは `AGENTS.md`、`SKILL.md` に基づいて適切なクエリを投げます。


## `vector` — 埋め込みベクトルによる意味検索

Embedding Model で計算した埋め込みベクトル同士のコサイン類似度を Qdrant で検索します。

```yaml
retrieval:
  strategy: vector
  top_k: 8
```

挙動：

1. クエリテキストのベクトル化（インデックス時と同じモデル）
2. Qdrant にコサイン類似度で問い合わせ、上位 `top_k` 件を取得
3. 各ヒットの `chunk_id` から SQLite で本文を引き、結果として返す

得意・不得意：

- **得意**：表記揺れ・同義語・言い換えに強い。自然文クエリでも意味的に近いチャンクを拾える
- **不得意**：固有名詞や型番など「文字列として一致してほしい」検索。意味は近いが指している対象が違う候補が混ざりやすい

> ベクター検索はクエリ側の NFKC 正規化を行いません。多言語モデル（bge-m3 など）は半角全角や異体字の影響を受けにくいですが、極端に特殊な文字を含むクエリでは結果が微妙にぶれる可能性があります。


## `hybrid` — keyword + vector の融合（デフォルト）

`keyword` と `vector` をそれぞれ独立に走らせ、その結果を**融合**して 1 つのランキングにまとめます。多くのナレッジベースで最初に試すべきストラテジーです。

```yaml
retrieval:
  strategy: hybrid
  fusion: rrf              # rrf | weighted
  top_k: 8                 # 融合後に返す最終件数
  dense_top_k: 20          # vector 検索段階で取得する候補数
  keyword_top_k: 20        # keyword 検索段階で取得する候補数
```

挙動：

1. `vector_search()` を `dense_top_k` 件取得
2. `keyword_search()` を `keyword_top_k` 件取得
3. 設定された融合方式で結果を統合
4. 統合後の上位 `top_k` 件を返す

### 融合方式 — `rrf`（デフォルト）

**Reciprocal Rank Fusion**。順位（rank）だけを使うシンプルかつ堅牢な融合方式です。各リストでの順位の逆数を足し合わせる方式で、スコアスケールが異なる検索手法を組み合わせるのに向いています。

- 重み調整は不要（順位ベースなのでスコアの絶対値に依存しない）
- 多くのケースで `weighted` より破綻が少なく、迷ったらこれ

> RRF はスコアが構造的に小さい値（最大でも 0.03 程度）に圧縮されます。`Score stats` の数値が小さいのは仕様であり、検索精度の良し悪しとは関係ありません。

### 融合方式 — `weighted`

`vector` と `keyword` のスコアをそれぞれ [0, 1] に正規化し、重みをかけて加算する方式です。「vector を重めに見たい」「keyword を効かせたい」といったバイアスを明示できます。

```yaml
retrieval:
  strategy: hybrid
  fusion: weighted
  weights: [0.3, 0.7]      # [vector, keyword] の順。合計 > 0 であれば値の意味は相対比
  top_k: 8
  dense_top_k: 20
  keyword_top_k: 20
```

- `weights` は `[vector, keyword]` の順で 2 要素必須
- 合計が 0 以下になる指定はバリデーションエラー
- `weights` は検索時のパラメータなので、変更しても再インデックスは不要


## `parent_child` — 子で当て、親で返す

小さな子チャンクで精密にヒットさせ、表示は周辺文脈を含む親チャンクで返す Small-to-Big 方式です。**`chunking.strategy: parent_child` と必ずペアで使用**します。

```yaml
chunking:
  strategy: parent_child
  parent:
    strategy: fixed_size
    max_chars: 3000
  child:
    strategy: recursive
    chunk_size: 600
    overlap: 100

retrieval:
  strategy: parent_child
  top_k: 8
  dense_top_k: 60          # 子チャンク → 親チャンクへの集約で減るので余裕を持って
  keyword_top_k: 60
```

挙動：

1. 検索フェーズは内部で `hybrid_search()` が動き、子チャンクを対象にスコアリング
2. ヒットした子チャンクから `parent_chunk_id` を引き、所属する親チャンクの本文に置換
3. 同じ親に属する複数の子がヒットした場合は 1 件に重複排除（最高スコアの子の hit が代表として残る）
4. 上位 `top_k` 件の親チャンクを返す

得意・不得意：

- **得意**：長文ドキュメントで「ピンポイントなクエリで当てたいが、返ってくる文脈は広く欲しい」というケース
- **不得意**：短いドキュメントや短い親チャンクで運用するケース — `parent_child` の構造的なメリットが薄れます

> 重要：`dense_top_k` / `keyword_top_k` は子チャンク段の候補数です。集約で親が重複排除される分、最終 `top_k` より十分大きく（目安 `top_k × 3` 以上）取らないと、結果件数が `top_k` に届かなくなります。

> 注意：`parent_child` プロファイルで `rerank.enabled: true` を併用すると、リランカーが約 3000 文字の親チャンクを 512 トークンに切り詰めるため、スコアの信頼性が低下します。`parent_child` を使う場合はこの点に留意してください。

> `parent_child` のチャンキング側の詳細は [chunking-strategies-ja.md](./chunking-strategies-ja.md#parent_child--検索精度と文脈量の両立) を参照してください。


## デフォルト値の早見表

設定を省略したときに採用される値です。

| フィールド | デフォルト | 意味 |
|------------|-----------|------|
| `retrieval.top_k` | `8` | 最終的に返す件数 |
| `retrieval.dense_top_k` | `20` | `hybrid` / `parent_child` で vector 段が取得する候補数 |
| `retrieval.keyword_top_k` | `20` | `hybrid` / `parent_child` で keyword 段が取得する候補数 |
| `retrieval.fusion` | `rrf` | `hybrid` の融合方式 |
| `retrieval.weights` | `null`（均等） | `fusion: weighted` 時の重み |


## CLI でのストラテジーの切り替え

プロファイル設定を変えずに、検索コマンドのオプションで戦略・件数を切り替えることもできます。

```bash
# 戦略を切り替える
mrag search "クエリ" --strategy keyword
mrag search "クエリ" --strategy vector
mrag search "クエリ" --strategy hybrid

# 件数を絞る
mrag search "クエリ" --top-k 3

# リランキングを無効化
mrag search "クエリ" --no-rerank
```

これにより、プロファイルを編集して再インデックスをしなくても、別のストラテジーをその場で試せます。
