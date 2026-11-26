# チャンキング戦略

このドキュメントでは、mrag に実装されている4つのチャンキングストラテジーについて解説します。

チャンキングとは「ドキュメントを検索しやすいサイズに切り分ける」工程のことです。チュートリアルではデフォルトの設定で動かしましたが、扱うドキュメントの種類（プレーンテキスト・Markdown・テーブル混在の変換済みドキュメントなど）によって、適切な切り分け方は変わります。

mrag では `profiles/<profile-name>.yaml` の `chunking.strategy` フィールドで戦略を指定します。


## 4 つの戦略

| 戦略 | 想定するフォーマット | 説明 |
|------|--------------|------------------|
| `recursive` | プレーンテキスト | デフォルト。段落 → 行 → 空白 → 文字の順に再帰的に分割 |
| `markdown_recursive` | 見出しのある Markdown | 見出し境界を優先して分割 |
| `block_aware` | 表やコードブロックを含む Markdown | 表・コード・段落を「ブロック」として認識し、`chunk_size` に収まる表とコードは途中で分割しない |
| `parent_child` | 検索精度と文脈量の両立が必要なケース | 小さな子チャンクでヒットさせ、表示は大きな親チャンクで返す |

ざっくりとした選び方：

- **雑多な形式のプレーンテキストが多い** → `recursive`
- **見出しがはっきりしている** → `markdown_recursive`
- **マニュアルや仕様書のように表・コードが多い** → `block_aware`
- **検索時に、周辺文脈も広く検出したい** → `parent_child`

> `parent_child`を選択する場合、`retrieval.strategy`も`parent_child`に合わせる必要があります。

## `recursive` — デフォルト

プレーンテキストや構造を持たないテキストに対する最も汎用的な戦略です。

`mrag init` で作成したデフォルトプロファイルは、この戦略と下記のサイズを使います。あわせて `source_format: markdown` と 3 つの `preserve_*` オプション（いずれも `true`）も書き出されるため、このページの後半で説明するブロック対応前処理が上乗せされます。

```yaml
chunking:
  strategy: recursive
  chunk_size: 800       # 1 チャンクの最大文字数
  overlap: 120          # チャンク間の重複文字数（文脈断絶を緩和）
```

挙動：

1. まず段落（空行区切り）でテキストを分割しようとする
2. `chunk_size` に収まらない段落はさらに行単位で分割
3. 行でも収まらない場合は空白で分割し、空白のない並びがなお収まらない場合は文字単位で分割
4. 隣り合うチャンクは `overlap` 文字分だけ重複させる

「自然な区切り（段落 → 行）を優先しつつ、長文にも対応する」ため、汎用性が高いです。

> オプションについて：`chunk_size` を大きくすると 1 チャンクあたりの情報量は増えますが、ヒット精度（どの位置に何があるかの解像度）は下がります。`overlap` はチャンク境界で意味が途切れやすくなる現象を緩和するための余白で、`chunk_size` の 10〜20% 程度が無難です。


## `markdown_recursive` — Markdown 向け

見出し（`#`, `##`, ...）が構造化されている Markdown 文書向けの戦略です。**見出しの境界をまたぐチャンクが生成されないよう**、まず見出しでドキュメントを区切り、その後に各セクション内で `recursive` と同じ再帰分割を行います。

```yaml
chunking:
  strategy: markdown_recursive
  chunk_size: 800
  overlap: 120
```

挙動：

1. ドキュメントを見出しごとのセクションに分割
2. セクション内のテキストを `recursive` と同じロジックで分割
3. 結果として「複数の見出しにまたがる 1 チャンク」は発生しない


## `block_aware` — テーブル・コードブロックを保護

Markdown 内の **表とコードブロックを途中で分割しない**戦略です（`chunk_size` に収まる場合）。マニュアル・API リファレンス・データシートのように、表とコードが意味の中核を担うドキュメントに向きます。

```yaml
chunking:
  strategy: block_aware
  chunk_size: 800
  overlap: 120
```

挙動：

1. Markdown を「段落」「見出し」「テーブル」「コードブロック」など型付きブロックにパース
2. `chunk_size` に収まるテーブルとコードブロックは**分割しない**（前後のブロックと同じチャンクに入ることはある）。`chunk_size` を超えるものは分割する。テーブルは行単位で分割し、各分割に `[Table: H1 > H2 (n/N)]` のラベルを付けてヘッダー行を再掲する。コードブロックは複数の断片に分け、それぞれをコードフェンスで囲み直す
3. それ以外のブロックは `recursive` と同じロジックで分割
4. 各チャンクに見出しパス（`H1 > H2 > H3`）をメタデータとして付与


> `block_aware`の機能は`chunking.source_format: markdown`（デフォルトは`text`）を設定することで他のストラテジーの前処理として実行することも可能です。どの機能を適用するかは`chunking.preserve_heading_path`, `chunking.preserve_tables`, `chunking.preserve_code_blocks`で選びます（デフォルトは3つとも`true`）。


## `parent_child` — 検索精度と文脈量の両立

「**小さな子チャンクで検索ヒットを取り、提示は大きな親チャンクで返す**」方式の戦略です。長文ドキュメントで「ピンポイントなクエリで当てたいが、返ってくる文脈は広く欲しい」というケースに有効です。

```yaml
chunking:
  strategy: parent_child
  source_format: markdown        # 子チャンクにブロック対応前処理をかける場合
  parent:
    strategy: fixed_size         # fixed_size | section
    max_chars: 3000
  child:
    strategy: recursive
    chunk_size: 600
    overlap: 100

retrieval:
  strategy: parent_child         # ← セットで指定すること（重要）
  top_k: 8
  dense_top_k: 60                # 子チャンク → 親チャンクへの集約で減るので余裕を持って
  keyword_top_k: 60
```

挙動：

1. ドキュメントを**親チャンク**（`max_chars` ≒ 3000 文字程度）に分割
2. 各親チャンクをさらに**子チャンク**（`chunk_size` ≒ 600 文字程度）に分割
3. インデックスには**子チャンクだけ**を登録（埋め込みベクトルも子単位）
4. 検索時は子チャンクでマッチし、最終的に**所属する親チャンクを返す**
5. 同じ親に属する複数の子がヒットしても、親は 1 件に重複排除される

`parent.strategy` には 2 種類あります：

- **`fixed_size`** — 親も固定文字数で機械的に分割（デフォルト）
- **`section`** — Markdown の見出し境界で親を区切る（見出し付き Markdown に有効）

> 重要：`chunking.strategy: parent_child` と `retrieval.strategy: parent_child` は**必ずセットで指定**します。片方だけ設定するとプロファイル検証エラーになります。

> 注意：`dense_top_k` / `keyword_top_k` は最終的に `top_k` まで絞り込まれるので、子チャンクで広めに拾うために `top_k × 3` 程度を確保することをおすすめします（複数の子が同じ親に集約される分、検索段階では多めにとる必要があるため）。融合後の子チャンクは、親に置き換える前に上位 `top_k × 3` 件で打ち切られます。また、`dense_top_k`・`keyword_top_k` が `top_k` より小さいとプロファイル検証エラーになります。

> 注意：`parent_child` プロファイルで `rerank.enabled: true` を併用すると、リランカーが約 3000 文字の親チャンクを 512 トークンに切り詰めるため、スコアの信頼性が低下します。`parent_child` を使う場合はこの点に留意してください。


## ブロック対応前処理（全戦略共通）

`block_aware` で説明した表とコードブロックの扱い（`chunk_size` に収まれば分割せず、超えれば行単位またはフェンス付きの断片に分割）は、**任意の戦略**に追加できます。スイッチは `source_format` です。デフォルトは `text` で、`markdown` にすると前処理が有効になります。`preserve_*` オプションのデフォルトは `true` で、`false` にした項目だけが無効になります：

```yaml
chunking:
  strategy: recursive             # markdown_recursive や parent_child でも可
  source_format: markdown
  chunk_size: 800
  overlap: 120
  preserve_heading_path: true     # チャンクに H1 > H2 > H3 のパンくずを付与
  preserve_tables: true           # chunk_size に収まる表を途中で割らない
  preserve_code_blocks: true      # chunk_size に収まるフェンス付きコードブロックを途中で割らない
```

`parent_child` の場合、ここで適用される上限は `parent.max_chars` で、対象は親チャンクです。子チャンクは `child.chunk_size` で切られるため、表やコードブロックの途中で終わることがあります。

これにより、`recursive` を使いつつ「表だけは保護したい」「見出しパスだけ欲しい」といった部分的な制御ができます。


## 設定を変更したときに起こること

`chunking.*` のいずれかのフィールドを変更した場合、**プロファイルのハッシュ値が変わり**、次の `mrag index` 実行時に **そのプロファイルのインデックスが全件再構築**されます。

これはチャンキング条件が変わると過去のチャンクが意味を持たなくなるための安全機構です。再インデックスには時間がかかるので、大きなナレッジベースで設定を試行錯誤するときは、別プロファイルを新規作成して別のインデックスを作成し性能比較するほうが効率的です：

```bash
mrag index --profile experimental
mrag search "クエリ" --profile experimental
```
