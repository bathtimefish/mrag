# インデックスの構造を調査する — `mrag inspect`

このドキュメントでは、インデックス済みのドキュメント・チャンク・セクション構造を確認するための `mrag inspect` コマンド群について解説します。

`mrag inspect` は **読み取り専用** のコマンドで、データベースの内容を直接 SQLite で読まなくても、CLI から構造化された結果を取り出せるように設計されています。すべてのサブコマンドが `--json` によるJSON出力をサポートしているため、AI エージェントでの分析にも向いています。

> 前提：`mrag inspect` はプロジェクトディレクトリの中（`mrag.yaml` と `mrag.db` がある場所）で実行します。少なくとも 1 つのドキュメントが `mrag add` → `mrag index` されている必要があります。


## 4 つのサブコマンド

| サブコマンド | 使うとき |
|---|---|
| `mrag inspect document <doc-id>` | 1 つのドキュメントについて「どのプロファイルで何チャンクできたか、拡張は健全か」を知る |
| `mrag inspect chunks <doc-id>` | チャンク 1 件ずつのメタデータを一覧して、どれを掘るか決める |
| `mrag inspect chunk <chunk-id>` | 1 つのチャンクの本文・LLM 生成コンテキスト・メタデータを全部見る |
| `mrag inspect sections <doc-id>` | 見出し階層や `parent_child` の親子構造を可視化する |


## `mrag inspect document` — ドキュメント単位のサマリ

```bash
# 通常の表形式
mrag inspect document <doc-id>

# JSON出力
mrag inspect document <doc-id> --json

# 特定プロファイルだけに絞る
mrag inspect document <doc-id> --profile default
```

出力に含まれる主な情報：

- ファイル名、ソース形式（pdf / md / txt）、抽出プロバイダ（pymupdf など）
- **プロファイルごと**のチャンク件数（`parent_child` プロファイルでは親・子のカウントも分かれて表示）
- **拡張処理（augmentation）のステータス**：`succeeded` / `raw_fallback` の件数
- **Embedding 処理のステータス**（v0.21.0+）：`embedded` / `fallback_no_vector` の件数

> 拡張ステータスの集計は「実際に拡張処理が走った variant」だけが対象です。`augmentation.strategy: none` のプロファイルでは Augmentation Status セクションは出力されません。

> Embedding Status セクションは **`fallback_no_vector` が 1 件以上ある場合のみ表示** されます。全チャンクが正常に埋め込まれた場合はセクション自体が出力されません（augmentation と同じ省略ルール）。`fallback_no_vector` のチャンクは Qdrant に point を持たないため vector 検索ではヒットしませんが、FTS5 keyword 検索ではヒットします（→ [contextual-retrieval-ja.md](./contextual-retrieval-ja.md) の "Embedding 失敗時の挙動" 節）。

`--profile` を省略すると、そのドキュメントをインデックスしている**すべてのプロファイル**が表示されます。


## `mrag inspect chunks` — チャンク一覧

```bash
# プロファイル配下の全チャンクを一覧（デフォルトは件数制限なし）
mrag inspect chunks <doc-id> --profile default --json

# 大量チャンクのドキュメントを 50 件ずつページング
mrag inspect chunks <doc-id> --profile default --limit 50 --offset 0 --json
mrag inspect chunks <doc-id> --profile default --limit 50 --offset 50 --json

# 本文も含めて出力（デフォルトは本文を含まない軽量出力）
mrag inspect chunks <doc-id> --profile default --show-content --json

# LLM が生成したコンテキスト文も一緒に出力
mrag inspect chunks <doc-id> --profile default --show-context --json
```

出力の各エントリには、`chunk_id` / `chunk_type` / `chunk_index` / `parent_chunk_id` / `char_count` / `token_count` などのメタデータが含まれます。`--show-content` / `--show-context` を付けるとそれぞれ本文と LLM コンテキスト文も追加されます。

`variant` オブジェクトには次のフィールドが含まれます：

- `type` — `raw` / `contextual`
- `qdrant_collection` — このチャンクが属する Qdrant コレクション名
- `augmentation_status` — `fallback_raw` / `null`
- **`embedding_status`** — `fallback_no_vector` / `null`（v0.21.0+。`fallback_no_vector` のチャンクは vector 検索でヒットしない）
- **`has_qdrant_point`** — `true` / `false`（v0.21.0+。fallback チャンクは `false`）

> ページングの目安：数百チャンクを超える大型 PDF などでは `--limit 50` でターミナルを溢れさせない運用がおすすめです。小〜中規模のドキュメントなら省略（全件出力）で問題ありません。

> プロファイル解決：`--profile` を省略した場合、そのドキュメントが**ちょうど 1 つのプロファイル**でしかインデックスされていなければ自動選択されます。複数プロファイルでインデックスされている場合は exit 1 で候補一覧が表示されるので、`--profile <name>` で指定し直してください。


## `mrag inspect chunk` — 1 チャンクの内容を表示

```bash
mrag inspect chunk <chunk-id> --json
```

このサブコマンドは常に**本文と context_text を含めて**返します。`chunk_id` は DB のプライマリキーなので、`--profile` は不要です（チャンクは特定のプロファイル配下に必ず属するため）。

典型的なフローは「`mrag search --json` の結果から `chunk_id` を取り、そのまま `mrag inspect chunk` に渡して全文と LLM コンテキストを確認する」というパターンです。


## `mrag inspect sections` — 見出し階層 / 親子構造

```bash
# 通常プロファイルの見出し階層を可視化
mrag inspect sections <doc-id> --profile default

# parent_child プロファイルの親 → 子レイヤを可視化
mrag inspect sections <doc-id> --profile parent-child
```

- 通常プロファイルでは `preserve_heading_path: true` の場合に、`H1 > H2 > H3` のような階層がツリー表示されます
- `parent_child` プロファイルでは親チャンクと、その下にぶら下がる子チャンク群がレイヤ表示されます

> `preserve_heading_path: false`（=見出しメタデータがない）のプロファイルで実行すると、コマンドは exit 1 で「セクション構造なし」と返します。その場合はフラットな一覧として `mrag inspect chunks` を使ってください。


## 推奨パターン — 2 段階ワークフロー

エージェントから利用する場合や、ドキュメントをスキャンしたい場合は、**メタデータを取得 → チャンクの可視化** の 2 段階で進めると効率的です。

```bash
# Stage 1: メタデータだけを取得し、興味のあるチャンクをフィルタする
mrag inspect chunks <doc-id> --profile default --json \
  | jq '.chunks[] | select(.metadata.contains_table or .metadata.contains_code)'

# Stage 2: 候補チャンクの本文・LLM コンテキストを取得する
mrag inspect chunk <chunk-id> --json
```

この手順にすると：

- Stage 1 は軽量メタデータだけなので、数百チャンクあっても出力量を抑えられる
- Stage 2 で本文を取るのは「実際に深掘りしたい一握り」だけなので、エージェントのコンテキストウィンドウも節約できる


## ドキュメント ID の調べ方

`mrag add` 時に表示される `document_id` をメモしておくのが一番楽ですが、後から確認する手段もあります：

```bash
# SQLite を直接見る
sqlite3 mrag.db "SELECT id, filename FROM documents;"

# API 経由
GET /api/v1/documents
```


## Tips

- すべてのサブコマンドが `--json` を持ち、**stdout はペイロード／stderr は警告とエラー**の分離を守ります。パイプ前提のスクリプトを書きやすい設計になっています
- `mrag inspect chunks --show-context --json | jq` は、コンテキスチュアル拡張の結果（`context_text`）を俯瞰するときに便利です（→ [contextual-retrieval-ja.md](./contextual-retrieval-ja.md)）
- `parent_child` プロファイルの構造を確認したいときは `mrag inspect sections` が有効です。チャンクの粒度（親 / 子）と件数バランスが分かりやすいです
- 抽出結果（`mrag inspect document` で見えるファイル名・抽出プロバイダ）を確認すれば、ドキュメントが意図どおりに取り込まれているかを `mrag index` 前後で検証できます
