# mrag — Micro RAG

**ローカルファーストの軽量 RAG 検索ランタイム**

mrag は、シンプルで使い捨て可能なプロジェクト単位の RAG ツールです。コマンド1つで自己完結型のナレッジベースを作成し、ドキュメントをインデックスして、CLI または HTTP API で検索できます。すべてのデータはローカルに保存され、SQLite が信頼できる唯一のソースです。Qdrant のベクターインデックスはいつでも再構築できます。

---

## 特徴

- **ハイブリッド検索** — キーワード（FTS5 BM25）、ベクター（密ベクトル）、RRF 融合ハイブリッドの3方式に対応
- **日本語対応トークナイザー** — [sqlite-vaporetto](https://github.com/daac-tools/sqlite-vaporetto)（形態素解析）を自動検出し、見つからない場合は SQLite 組み込みの trigram トークナイザーにフォールバック
- **多言語 Embedding** — デフォルトで [Ollama](https://ollama.com) 経由の `bge-m3` を使用（Ollama 対応モデルであれば差し替え可能）
- **差分インデックス** — `mrag index` を再実行しても、既インデックス済みのドキュメントはスキップ
- **検索プロファイル** — プロジェクトごとの YAML ファイルでチャンキング・Embedding・検索戦略を独立して管理
- **リランキング** — 検索後に CrossEncoder（sentence-transformers）による再スコアリングをオプションで適用。`--no-rerank` でリクエスト単位の無効化も可能
- **検索品質評価** — `mrag eval` でスコア分布・重複チャンク・ドキュメント分布・マルチプロファイル比較が可能
- **FastAPI サーバー** — オプションの Bearer トークン認証付きで HTTP API として公開可能
- **Dify 対応** — `mrag serve` は [Dify 外部ナレッジ API](https://docs.dify.ai/ja/use-dify/knowledge/external-knowledge-api) 仕様を実装済み。アダプター不要で Dify の外部ナレッジソースとして利用可能
- **Qdrant 依存ゼロ** — デフォルトでプロセス内組み込み動作（`mode: local`）。Docker や外部サーバー不要。必要時のみ `mode: server` に切り替え可能。

---

## 必要な環境

| コンポーネント | 備考 |
|--------------|------|
| Python 3.11+ | |
| [Ollama](https://ollama.com) | 起動済みであること。デフォルトで `bge-m3` を使用 |
| [Qdrant](https://qdrant.tech) | **任意** — `mode: server` 時のみ必要。デフォルトの `mode: local` はプロセス内で動作するため Docker 不要。 |
| `libsqlite_vaporetto` | 任意。日本語形態素解析を有効にする |
| `apsw >= 3.43` | vaporetto トークナイザー使用時のみ必要 |

---

## インストール

mrag はソースからインストールします。[uv](https://docs.astral.sh/uv/) の使用を推奨します。

```bash
git clone https://github.com/bathtimefish/mrag.git
cd mrag
uv venv
uv pip install -e "."
```

### vaporetto を使う場合（日本語ドキュメントに推奨）

```bash
uv pip install -e ".[vaporetto]"
```

次に、`libsqlite_vaporetto.dylib`（macOS）または `libsqlite_vaporetto.so`（Linux）を以下のディレクトリに配置します：

```
~/.mrag/extensions/
```

または環境変数でカスタムパスを指定できます：

```bash
export MRAG_VAPORETTO_LIB=/path/to/libsqlite_vaporetto.dylib
```

> **注意:** macOS のシステム `sqlite3` は `OMIT_LOAD_EXTENSION` フラグ付きでビルドされているため、vaporetto を使用するには `apsw`（拡張ローディングを常にサポートする代替 SQLite バインディング）が必要です。`uv pip install -e ".[vaporetto]"` を実行すると自動的にインストールされます。

### Marker を使う場合（スキャン PDF・複雑なレイアウトの PDF 向け）

```bash
uv pip install -e ".[marker]"
```

### リランカーを使う場合（CrossEncoder による再スコアリング）

```bash
uv pip install -e ".[reranker]"
```

プロファイル YAML で `rerank.enabled: true` を設定すると有効になります。`sentence-transformers` の CrossEncoder モデルを使用します。デフォルトモデルは `hotchpotch/japanese-reranker-cross-encoder-small-v1` です。

### デフォルト Embedding モデルの取得

```bash
ollama pull bge-m3
```

`bge-m3` は日本語・英語を含む多言語対応の Embedding モデルです（1024 次元）。プロファイル YAML を編集することで、Ollama に対応した任意のモデルに変更できます。

> **Qdrant の Docker セットアップは不要です。** mrag はデフォルトで `qdrant.mode: local` を使用し、Qdrant をプロセス内に組み込んで動作させます。ベクターデータはプロジェクトの `qdrant/` ディレクトリに保存されます。Docker が必要なのは `qdrant.mode: server` を明示的に設定した場合のみです。

---

## クイックスタート

### 1. プロジェクトを初期化する

```bash
mrag init --name my-project
cd my-project
```

`mrag init` はカレントディレクトリに `my-project/` サブディレクトリを作成します：

- `mrag.yaml` — プロジェクト設定
- `profiles/default.yaml` — 検索プロファイル
- `mrag.db` — SQLite データベース
- `data/`、`qdrant/`、`cache/` などのサポートディレクトリ

初期化時にトークナイザーが自動検出されます。vaporetto が見つかった場合は自動的に設定されます：

```
✓ vaporetto tokenizer detected (libsqlite_vaporetto.dylib)
✓ Created directory structure
✓ Generated mrag.yaml
✓ Generated profiles/default.yaml
✓ Initialized mrag.db
```

### 2. ドキュメントを追加する

```bash
mrag add report.pdf
mrag add manual.pdf notes.txt
```

ドキュメントはテキスト抽出されて `data/documents/` に保存されます。対応フォーマット：PDF、プレーンテキスト、Markdown。

**エクストラクターオプション（PDF のみ）**

| エクストラクター | オプション | 説明 |
|--------------|----------|------|
| PyMuPDF | `--extractor pymupdf` | デフォルト。テキストレイヤーの高速抽出。スキャン/画像ベースの PDF と判定された場合は警告を出力する。 |
| Marker | `--extractor marker` | 複雑なレイアウトに対応した高精度抽出。`uv pip install -e ".[marker]"` が必要。 |

```bash
# デフォルトエクストラクター（PyMuPDF）を使用
mrag add report.pdf

# スキャン PDF や複雑なレイアウトの PDF に Marker を使用
mrag add scanned.pdf --extractor marker

# 登録済みのドキュメントを再追加（抽出内容を上書き）
mrag add report.pdf --force
```

プロジェクト全体のデフォルトエクストラクターは `mrag.yaml` の `default_extraction.pdf.provider` で設定できます。

### 3. インデックスを構築する

```bash
mrag index
```

未インデックスのドキュメントを Embedding してFTS5 + Qdrant インデックスを構築します：

```
✓ Indexed: 12  Skipped: 0
```

新しいファイルを追加してから `mrag index` を再実行すると、新規分のみが処理されます。

### 4. 検索する

```bash
# ハイブリッド（デフォルト）
mrag search "熱電対の温度測定"

# キーワードのみ
mrag search "接点出力 ON OFF" --strategy keyword

# ベクターのみ
mrag search "temperature sensing" --strategy vector

# 件数を指定
mrag search "Bluetooth LE" --top-k 3

# リランキングを無効化して検索
mrag search "熱電対の温度測定" --no-rerank
```

### 5. 検索品質を評価する（任意）

```bash
mrag eval "熱電対の温度測定"
```

スコア分布・重複チャンク・ドキュメント分布を表示します。複数プロファイルの比較も可能です：

```bash
mrag eval "熱電対の温度測定" --profile default --profile second --strategy vector
```

### 6. API サーバーとして起動する

```bash
mrag serve

# すべての API リクエストでリランキングを無効化
mrag serve --no-rerank
```

`http://127.0.0.1:8000` で FastAPI サーバーが起動します。詳細は [API リファレンス](#api-リファレンス) を参照してください。

---

## CLI リファレンス

| コマンド | 説明 |
|---------|------|
| `mrag init [--name NAME]` | サブディレクトリに新しいプロジェクトを作成 |
| `mrag add <file> [file…] [--extractor pymupdf\|marker] [--force]` | ドキュメントを追加（テキスト抽出のみ。インデックスはしない） |
| `mrag index [--profile P]` | 差分インデックス（最新のドキュメントはスキップ） |
| `mrag reindex [--profile P]` | プロファイルのインデックスを強制再構築 |
| `mrag search <query>` | 検索（`--strategy keyword\|vector\|hybrid`、`--top-k N`、`--no-rerank`） |
| `mrag eval <query>` | 検索品質評価（`--profile P`、`--strategy S`、`--top-k N`、`--no-rerank`） |
| `mrag serve` | FastAPI サーバー起動（`--host`、`--port`、`--no-rerank`） |
| `mrag remove <doc-id>` | ドライラン削除（実際に削除するには `--force`） |
| `mrag profiles list` | DBに登録済みのプロファイル一覧 |
| `mrag profiles show <name>` | プロファイルの設定を表示 |
| `mrag extract <file>` | 抽出テキストのプレビュー（保存なし） |
| `mrag show-extracted <doc-id>` | 保存済みの抽出テキストを表示 |
| `mrag export-extracted <doc-id>` | 抽出テキストをファイルにエクスポート |
| `mrag doctor` | 環境チェック（SQLite、vaporetto、Qdrant、Ollama） |

---

## API リファレンス

`mrag serve` でサーバーを起動した後、以下のエンドポイントを呼び出せます：

### `POST /api/v1/retrieve`

ナレッジベースからチャンクを検索します。

**リクエストボディ:**

```json
{
  "query": "接点出力の制御方法",
  "strategy": "hybrid",
  "top_k": 5,
  "profile": "default"
}
```

`strategy` — `"hybrid"`（デフォルト）、`"keyword"`、`"vector"` のいずれか  
`profile` — プロファイル名。省略時はプロジェクトの `default_profile` を使用

**レスポンス:**

```json
{
  "query": "接点出力の制御方法",
  "profile": "default",
  "strategy": "hybrid",
  "results": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "filename": "manual.pdf",
      "score": 6.39,
      "content": "…接点出力ポートに対してON/OFF制御を…",
      "metadata": {}
    }
  ]
}
```

`POST /api/v1/search` は同じエンドポイントのエイリアスです。

### `GET /api/v1/documents`

ナレッジベース内のドキュメント一覧を返します。

### `GET /api/v1/documents/{document_id}`

ドキュメントの詳細（チャンク数を含む）を返します。

### `GET /api/v1/profiles`

DBに登録済みの検索プロファイル一覧を返します。

### `GET /api/v1/profiles/{profile_name}`

プロファイルの完全な設定を返します。

### 認証

サーバー起動前に `MRAG_API_KEY` 環境変数を設定すると、Bearer トークン認証が有効になります：

```bash
MRAG_API_KEY=your-secret-key mrag serve
```

以降のリクエストにはヘッダーが必要です：

```
Authorization: Bearer your-secret-key
```

有効なキーがない場合は `401 Unauthorized` が返ります。

---

## Dify 外部ナレッジ API

`mrag serve` は [Dify 外部ナレッジ API](https://docs.dify.ai/ja/use-dify/knowledge/external-knowledge-api) 仕様をそのまま実装しています。起動中の mrag インスタンスをアダプターなしで Dify の外部ナレッジソースとして接続できます。

### `POST /retrieval`

**リクエストボディ:**

```json
{
  "knowledge_id": "<your-knowledge-id>",
  "query": "接点出力の制御方法",
  "retrieval_setting": {
    "top_k": 5,
    "score_threshold": 0.5
  }
}
```

`knowledge_id` — `mrag.yaml` の `knowledge_id` と一致する必要があります  
`top_k` — 最大取得件数（1〜100）  
`score_threshold` — 正規化スコアの下限（0.0〜1.0）。この値未満の結果は除外されます

**レスポンス:**

```json
{
  "records": [
    {
      "content": "…接点出力ポートに対してON/OFF制御を…",
      "score": 0.87,
      "title": "manual.pdf",
      "metadata": {}
    }
  ]
}
```

スコアはすべて `[0, 1]` に正規化されます。BM25 キーワードスコアは `score / (1 + score)` で変換、ベクターおよびハイブリッドスコアは元から範囲内でクランプされます。

**エラーレスポンス**は Dify 仕様に準拠します：

| HTTP | `error_code` | 内容 |
|------|-------------|------|
| 404 | 2001 | `knowledge_id` が見つからない |
| 401 | 1001 | `Authorization` ヘッダーが欠落または不正 |
| 401 | 1002 | API キーが間違っている |

### Dify への接続手順

1. Docker からアクセスできるよう、すべてのインターフェースにバインドしてサーバーを起動します：
   ```bash
   MRAG_API_KEY=your-secret-key mrag serve --host 0.0.0.0 --port 8000
   ```
2. Dify で **ナレッジ → 外部ナレッジ API → 追加** を開きます。
3. **エンドポイント URL** を設定します。Dify の動作環境によって異なります：

   | Dify の環境 | エンドポイント URL |
   |------------|-----------------|
   | Docker Desktop（macOS / Windows） | `http://host.docker.internal:8000` |
   | Linux の Docker | `http://172.17.0.1:8000`（docker0 ブリッジ） |
   | 同じ LAN / VM | `http://<ホストの LAN IP>:8000` |

   > `http://127.0.0.1:8000` は**使用不可**です。Dify コンテナ内の `127.0.0.1` はコンテナ自身を指すため、ホストマシンの mrag に到達できません。

4. **API キー** に `MRAG_API_KEY` の値を設定します（認証なしの場合は空欄）。
5. Dify でナレッジベースを作成する際は、`mrag.yaml` の `knowledge_id` を使用します。

---

## 検索プロファイル

プロファイルは `profiles/` 内の YAML ファイルで、チャンキング・Embedding・検索を制御します。`mrag init` で生成されるデフォルトプロファイル：

```yaml
name: default

chunking:
  strategy: recursive
  source_format: text
  chunk_size: 800
  overlap: 120

embedding:
  provider: ollama
  model: bge-m3
  endpoint: http://localhost:11434

retrieval:
  strategy: hybrid
  top_k: 8
  dense_top_k: 20
  keyword_top_k: 20
  fusion: rrf

keyword:
  provider: sqlite_fts5
  tokenizer: vaporetto       # init 時に自動設定。vaporetto 未検出時は trigram
  fallback_tokenizer: trigram
```

`profiles/` に新しい YAML を配置して `mrag index --profile <name>` を実行することで、複数のプロファイルを使い分けられます。

### チャンキングストラテジー

`chunking.strategy` フィールドで、インデックス前にドキュメントをどのように分割するかを制御します。2種類のストラテジーが使用できます：

| ストラテジー | 説明 |
|------------|------|
| `recursive` | 段落 → 改行 → 文のような区切り文字の階層で再帰的に分割。プレーンテキストおよび PDF に最適。**デフォルト。** |
| `markdown_recursive` | まず Markdown の見出し構造で分割し、各セクション内でさらに再帰分割。`source_format: markdown` と組み合わせて使用。 |

**設定フィールド:**

```yaml
chunking:
  strategy: recursive       # recursive | markdown_recursive
  source_format: text       # text | markdown
  chunk_size: 800           # チャンクの目標文字数
  overlap: 120              # 隣接チャンク間のオーバーラップ文字数
```

**ストラテジーの選び方:**

- **`recursive`** — プレーンテキストや PDF に使用。日本語を含む全言語で安定して動作します。
- **`markdown_recursive`** — 見出し構造が明確なドキュメント（技術仕様書、Markdown でエクスポートした Wiki など）に使用。セクションのコンテキストをチャンク内に保持するため、検索精度が向上しやすいです。

> **注意:** `chunking.strategy`、`chunk_size`、`overlap` を変更すると既存のインデックスが無効になります。チャンキング設定を変更した後は `mrag reindex` で最初から再構築してください。

### 検索ストラテジー

プロファイルの `retrieval.strategy` フィールドで検索方式を切り替えます。3種類のストラテジーが使用できます：

| ストラテジー | 説明 |
|------------|------|
| `hybrid` | キーワード（BM25）とベクター検索の結果を Reciprocal Rank Fusion（RRF）で統合。**ほとんどのユースケースで推奨するデフォルト設定。** |
| `keyword` | SQLite FTS5 BM25 によるフルテキスト検索のみ。高速で、クエリ時に Embedding 不要。 |
| `vector` | Qdrant コサイン類似度によるベクター検索のみ。クエリと文書の表現が異なる場合（言い換え・意味検索）に強い。 |

**ストラテジーごとの設定フィールド:**

```yaml
retrieval:
  strategy: hybrid      # hybrid | keyword | vector
  top_k: 8              # 最終的に返す件数
  dense_top_k: 20       # 融合前に Qdrant から取得する候補数（hybrid / vector で使用）
  keyword_top_k: 20     # 融合前に FTS5 から取得する候補数（hybrid / keyword で使用）
  fusion: rrf           # 融合アルゴリズム（現在 rrf のみ対応）
```

`dense_top_k` / `keyword_top_k` は対応するサブ検索が有効なときのみ使用されます。`top_k` より大きい値を設定することで融合ステップに渡す候補が増え、精度が向上します（レイテンシはわずかに増加）。

**ストラテジーの選び方:**

- **`hybrid`** — ほとんどの場面でベストな選択。製品コードや日本語キーワードのような完全一致クエリと、意味的なクエリの両方に対して安定した結果を返します。
- **`keyword`** — クエリが文書中の正確な表現を含む場合に適しています（型番、エラーコードなど）。Ollama / Qdrant が利用できない環境でも動作します。
- **`vector`** — クエリが文書の表現と異なる言い回しの場合に有効（概念に関する質問など）。クエリ時に Ollama が起動している必要があります。

> **注意:** ストラテジーはグローバル設定ではなく**プロファイル単位**で設定します。異なるストラテジーを持つ複数のプロファイルを用意し、`--profile <name>` で切り替えることができます。

### リランキング

`rerank.enabled: true` を設定すると、検索後に CrossEncoder による再スコアリングが行われ、結果の順序が改善されます。`top_n` 件の候補を取得してから再スコアし、最終的に `top_k` 件を返します。

```yaml
rerank:
  enabled: true
  provider: sentence-transformers
  model: hotchpotch/japanese-reranker-cross-encoder-small-v1
  top_n: 30      # リランキング前に取得する候補数
  top_k: 8       # リランキング後に返す件数
```

リランキングはクエリ時にのみ適用されます。`rerank` の設定を変更しても再インデックスは不要です。使用には `uv pip install -e ".[reranker]"` が必要です。

`mrag search`・`mrag eval`・`mrag serve` の `--no-rerank` オプションで実行時に無効化できます。

---

## アーキテクチャ

```
mrag CLI
  ├── mrag add      → テキスト抽出 → SQLite（documents テーブル）
  ├── mrag index    → チャンキング → Embedding（Ollama） → SQLite（chunks）+ Qdrant + FTS5
  └── mrag search   → キーワード（FTS5 BM25）+ ベクター（Qdrant）→ RRF 融合 → 結果

mrag serve  → FastAPI → 同じ検索パイプラインを HTTP で公開
```

- **SQLite** — ドキュメント、チャンク、プロファイル、FTS5 インデックスの信頼できる唯一のソース
- **Qdrant** — 再構築可能なベクターインデックス（`mrag reindex` で SQLite から再作成）。`mode: local`（デフォルト）ではプロセス内組み込み動作、`mode: server` では外部サーバーに接続。
- **FTS5 トークナイザー** — vaporetto（日本語形態素解析）または trigram（汎用）
- **apsw** — vaporetto 使用時に必須。macOS での SQLite 拡張ローディングを担う

---

## プロジェクト構成

```
my-project/
├── mrag.yaml                  # プロジェクト設定（名前、KB ID、トークナイザー、Qdrant 接続先）
├── mrag.db                    # SQLite データベース
├── profiles/
│   └── default.yaml           # 検索プロファイル
├── data/
│   └── documents/
│       └── <doc-id>/
│           ├── original.pdf   # 元ファイルのコピー
│           ├── extracted.txt  # 抽出されたプレーンテキスト
│           ├── extracted.md   # 抽出された Markdown
│           └── extraction_meta.json
├── qdrant/                    # Qdrant ベクターストレージ（mode: local 時にここへ保存）
└── cache/
    └── embeddings/            # Embedding キャッシュ（任意）
```

---

## Qdrant モード

`mrag.yaml` の `qdrant` セクションで動作モードを設定します：

```yaml
# デフォルト — Docker 不要
qdrant:
  mode: local

# 外部サーバー — 起動中の Qdrant インスタンスが必要
qdrant:
  mode: server
  host: localhost
  port: 6333
```

| モード | Qdrant プロセス | データ保存先 | 用途 |
|--------|---------------|------------|------|
| `local`（デフォルト） | プロセス内組み込み | プロジェクト内の `qdrant/` | 開発・CI・軽量デプロイ |
| `server` | 外部（Docker またはネイティブ） | Qdrant サーバーが管理 | 本番・複数プロジェクト共有 |

`mrag init` は常に `mode: local` を生成します。`mode` キーが存在しない既存プロジェクトは後方互換のため `mode: server` として扱われます。

---

## 別ホストへの移行

`mode: local` ではすべての Qdrant データがプロジェクトディレクトリ内に保存されるため、移行はディレクトリのコピーだけで完了します。`mrag reindex` は不要です。

```bash
# 移行元ホストでアーカイブを作成
tar -czf my-project.tar.gz my-project/

# 移行先ホストへ転送
scp my-project.tar.gz user@target-host:~/

# 移行先ホストで展開してすぐに使用
tar -xzf my-project.tar.gz
cd my-project
mrag search "クエリ"   # mrag reindex 不要
```

**転送対象:**

| パス | 内容 |
|------|------|
| `mrag.yaml` | プロジェクト設定 |
| `mrag.db` | SQLite（ドキュメント・チャンク・FTS5 インデックス） |
| `profiles/` | 検索プロファイル YAML |
| `data/documents/` | 元ファイル + 抽出テキスト |
| `qdrant/` | Qdrant ベクターデータ（local モード時） |

> **前提条件:** 移行先ホストで同じ Embedding モデルを持つ Ollama が起動していること（新規インデックスやベクター検索時に必要）。既存ドキュメントは `qdrant/` に事前構築済みのベクターが含まれるため、再 Embedding なしで即座に検索できます。

`mode: server` を使用している場合は `qdrant/` を除いてコピーし、移行先で Qdrant サーバーを起動してから `mrag reindex` を実行してください。

---

## ライセンス

MIT License

Copyright (c) 2026 BathTimeFish KK.

以下に定める条件に従い、本ソフトウェアおよび関連文書のファイル（以下「ソフトウェア」）の複製を取得するすべての人に対し、ソフトウェアを無制限に扱うことを無償で許可します。これには、ソフトウェアの複製を使用、複写、変更、結合、掲載、頒布、サブライセンス、および/または販売する権利、およびソフトウェアを提供する相手に同じことを許可する権利も無制限に含まれます。

上記の著作権表示および本許諾表示を、ソフトウェアのすべての複製または重要な部分に記載するものとします。

ソフトウェアは「現状のまま」で、明示であるか暗黙であるかを問わず、何らの保証もなく提供されます。ここでいう保証とは、商品性、特定の目的への適合性、および権利非侵害についての保証も含みますが、それに限定されるものではありません。作者または著作権者は、契約行為、不法行為、またはそれ以外であろうと、ソフトウェアに起因または関連し、あるいはソフトウェアの使用またはその他の扱いによって生じる一切の請求、損害、その他の義務について何らの責任も負わないものとします。
