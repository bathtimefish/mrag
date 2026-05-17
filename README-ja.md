# mrag — Micro RAG

**ローカルファーストの軽量 RAG 検索ランタイム**

mragは小規模なRAGナレッジベースを作成、運用するためのCLIです。ドキュメントのインデックス化から検索までの機能を提供し、ニーズに応じたカスタムRAGを作成するための様々なストラテジーを提供します。AIエージェント向けのskillを使って様々なAIエージェントにナレッジベースを提供できます。

---

## 特徴

- **ハイブリッド検索** — キーワード（FTS5 BM25）、ベクター（密ベクトル）、RRF 融合ハイブリッドの3方式に対応
- **Sqlite用日本語対応トークナイザー** — [sqlite-vaporetto](https://github.com/daac-tools/sqlite-vaporetto)（形態素解析）に対応
- **多言語 Embedding** — デフォルトで [Ollama](https://ollama.com) 経由の `bge-m3` を使用（Ollama 対応モデルであれば差し替え可能）
- **差分インデックス** — `mrag index` を再実行しても、既インデックス済みのドキュメントはスキップ
- **検索プロファイル** — プロジェクトごとの YAML ファイルでチャンキング・Embedding・検索戦略を独立して管理
- **ブロック認識チャンキング** — `source_format: markdown` を設定すると、任意のチャンキングストラテジーでテーブル・コードブロックの保護と見出しパスのメタデータ付与が有効になる。`mrag search` の結果に `section: H1 > H2 > H3` パンくず表示を追加
- **Parent-Child 検索** — 小さなチャイルドチャンクで精度の高い検索を行い、大きなペアレントチャンクをコンテキストとして返す。重複するペアレントを自動的に除去
- **コンテキスト拡張（Contextual Augmentation）** — インデックス時に Ollama LLM でチャンクごとのコンテキスト文を生成（Anthropic contextual retrieval パターン）。プロジェクトごとに `profiles/context_prompt.txt` でプロンプトをカスタマイズ可能
- **リランキング** — 検索後に CrossEncoder（sentence-transformers）による再スコアリングをオプションで適用。`--no-rerank` でリクエスト単位の無効化も可能
- **検索品質評価** — `mrag eval` でスコア分布・重複チャンク・ドキュメント分布・マルチプロファイル比較が可能
- **Dify 外部ナレッジAPI対応** — `mrag serve` で [Dify 外部ナレッジ API](https://docs.dify.ai/ja/use-dify/knowledge/external-knowledge-api) サーバーを起動。Dify の外部ナレッジソースとして利用可能

---

## 必要な環境

| コンポーネント | 備考 |
|--------------|------|
| Python 3.11+ | |
| [Ollama](https://ollama.com) | `ollama serve` が起動済みであること。`bge-m3`、`gemma4:e4b` を使用 |
| [Qdrant](https://qdrant.tech) | `mode: server` 時のみ docker 版 Qdrant が必要 |

---

## インストール

mrag はソースからインストールします。[uv](https://docs.astral.sh/uv/) の使用を推奨します。

```bash
git clone https://github.com/bathtimefish/mrag.git
cd mrag
uv venv
uv pip install -e ".[vaporetto,reranker]"
```

日本語形態素解析（`vaporetto`）と CrossEncoder リランキング（`reranker`）を標準構成として含むインストールコマンドです。

### vaporetto ネイティブライブラリ

`vaporetto` extra は `apsw`（macOS での SQLite 拡張ローディングに必要）をインストールしますが、ネイティブ共有ライブラリは別途配置が必要です：

1. [sqlite-vaporetto releases](https://github.com/hotchpotch/sqlite-vaporetto/releases) から、ご利用の OS・アーキテクチャに対応した最新の **`-with-model.tar.gz`** をダウンロードします。

   | OS / アーキテクチャ | ファイル名 |
   |-------------------|-----------|
   | macOS（Apple Silicon） | `sqlite-vaporetto-vX.Y.Z-macos-aarch64-with-model.tar.gz` |
   | macOS（Intel） | `sqlite-vaporetto-vX.Y.Z-macos-x86_64-with-model.tar.gz` |
   | Linux（x86_64） | `sqlite-vaporetto-vX.Y.Z-linux-x86_64-with-model.tar.gz` |

   > **`-with-model`** バリアントを使用してください。モデルデータが同梱されており、日本語形態素解析に必須です。

2. アーカイブを展開し、共有ライブラリを `~/.mrag/extensions/` に配置します：

   ```bash
   tar -xzf sqlite-vaporetto-vX.Y.Z-macos-aarch64-with-model.tar.gz
   EXTRACTED_DIR=$(tar -tzf sqlite-vaporetto-vX.Y.Z-macos-aarch64-with-model.tar.gz | head -1 | cut -d/ -f1)
   mkdir -p ~/.mrag/extensions
   cp "${EXTRACTED_DIR}/libsqlite_vaporetto.dylib" ~/.mrag/extensions/   # macOS
   # cp "${EXTRACTED_DIR}/libsqlite_vaporetto.so" ~/.mrag/extensions/    # Linux
   ```

または環境変数でカスタムパスを指定できます：

```bash
export MRAG_VAPORETTO_LIB=/path/to/libsqlite_vaporetto.dylib
```

`mrag init` 実行時に vaporetto が検出されない場合、trigram トークナイザーに自動フォールバックします。`mrag doctor` で検出状況を確認できます。

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
# 対話モード — プロジェクト名と KB ID をプロンプトで聞く
mrag init --name my-project
cd my-project

# 非対話モード — 未指定フィールドはすべてデフォルト値（プロンプトなし）
mrag init --name my-project --non-interactive
cd my-project
```

`mrag init` はカレントディレクトリに `my-project/` サブディレクトリを作成します：

- `mrag.yaml` — プロジェクト設定（ランタイム設定）
- `kb_information.yaml` — エージェント向けの KB メタデータ（description / tags / preferred profiles）
- `profiles/default.yaml` — 検索プロファイル
- `profiles/context_prompt.txt` — コンテキスト拡張用の LLM プロンプトテンプレート（編集可能）
- `mrag.db` — SQLite データベース
- `data/`、`qdrant/`、`cache/` などのサポートディレクトリ

初期化時にトークナイザーが自動検出されます。vaporetto が見つかった場合は自動的に設定されます：

```
✓ vaporetto tokenizer detected (libsqlite_vaporetto.dylib)
✓ Created directory structure
✓ Generated mrag.yaml
✓ Generated profiles/default.yaml
✓ Generated kb_information.yaml
✓ Initialized mrag.db
```

**LLM 主導のプロジェクト作成:** KB の説明を含む JSON ファイルを渡すと、完全な `kb_information.yaml` が生成されます。エージェント向けの推奨モード：

```bash
mrag init ./knowledges/kb-device --non-interactive --kb-info-json kb_info.json
```

ファイルの役割とスキーマについては下記の [`kb_information.yaml`](#kb-information-エージェント向け-kb-メタデータ) を参照してください。

### 2. ドキュメントを追加する

```bash
mrag add report.pdf
mrag add manual.pdf notes.txt
```

ドキュメントはテキスト抽出されて `data/documents/` に保存されます。対応フォーマット：PDF、プレーンテキスト、Markdown。

**PDF 抽出**には PyMuPDF を使用しており、テキストレイヤーを高速に抽出するとともに `find_tables()` によるテーブル検出を行います。スキャン/画像ベースの PDF と判定された場合は警告が出力されます。

```bash
# PDF、プレーンテキスト、Markdown ファイルを追加
mrag add report.pdf

# 登録済みのドキュメントを再追加（抽出内容を上書き）
mrag add report.pdf --force
```

### 3. インデックスを構築する

```bash
mrag index
```

未インデックスのドキュメントを Embedding してFTS5 + Qdrant インデックスを構築します：

```
✓ Indexed: 12  Up-to-date: 0  List-skipped: 0
Log: logs/20260514103000-index.json
```

実行のたびに `logs/` へ JSON ログが自動生成されます。新しいファイルを追加してから `mrag index` を再実行すると、新規分のみが処理されます。

常に失敗するドキュメント（巨大な PDF など）をスキップしたい場合は、前回のログをスキップリストとして渡します：

```bash
mrag index --skip-list-json logs/20260514103000-index.json
```

### 4. 検索する

```bash
# ハイブリッド（デフォルト）
mrag search "installation guide"

# キーワードのみ
mrag search "error handling retry" --strategy keyword

# ベクターのみ
mrag search "temperature sensing" --strategy vector

# 件数を指定
mrag search "Bluetooth LE" --top-k 3

# リランキングを無効化して検索
mrag search "installation guide" --no-rerank
```

### 5. 検索品質を評価する（任意）

`mrag search` はクエリごとにスコア統計（min/max/mean/σ）とドキュメント分布を出力します。σ が低い場合はクエリが曖昧すぎるサイン、高い場合は上位チャンクが明確に突出していることを示します。

より詳細な分析（重複チャンク検出・プロファイル比較）には `mrag eval` を使います：

```bash
mrag eval "installation guide" --profile default --profile second --strategy vector
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
| `mrag init [PROJECT_DIR] [--name NAME] [--kb-id ID] [--non-interactive] [--kb-info-json PATH] [--print-kb-info-schema] [--force]` | 新しいプロジェクトを作成。`--non-interactive` でプロンプトスキップ、`--kb-info-json` で LLM 主導の KB メタデータ生成、`--print-kb-info-schema` で入力 JSON Schema を出力 |
| `mrag add <file> [file…] [--force]` | ドキュメントを追加（テキスト抽出のみ。インデックスはしない） |
| `mrag index [--profile P] [--output-log PATH] [--skip-list-json PATH]` | 差分インデックス（最新のドキュメントはスキップ）。JSON ランログを常に出力 |
| `mrag reindex [--profile P] [--output-log PATH] [--skip-list-json PATH]` | プロファイルのインデックスを強制再構築。JSON ランログを常に出力 |
| `mrag search <query> [--json]` | 検索（`--strategy keyword\|vector\|hybrid`、`--top-k N`、`--no-rerank`、`--json` で機械可読出力） |
| `mrag eval <query>` | 検索品質評価（`--profile P`、`--strategy S`、`--top-k N`、`--no-rerank`） |
| `mrag serve` | FastAPI サーバー起動（`--host`、`--port`、`--no-rerank`） |
| `mrag remove <doc-id>` | ドライラン削除（実際に削除するには `--force`） |
| `mrag profiles list` | DBに登録済みのプロファイル一覧 |
| `mrag profiles show <name>` | プロファイルの設定を表示 |
| `mrag kb-info show` | 現プロジェクトの `kb_information.yaml` を表示 |
| `mrag kb-info validate` | 現プロジェクトの `kb_information.yaml` をバリデーション |
| `mrag kb-info schema` | `--kb-info-json` 入力用 JSON Schema を表示 |
| `mrag extract <file>` | 抽出テキストのプレビュー（保存なし） |
| `mrag show-extracted <doc-id>` | 保存済みの抽出テキストを表示 |
| `mrag export-extracted <doc-id>` | 抽出テキストをファイルにエクスポート |
| `mrag doctor` | mrag ランタイム環境チェック（SQLite、FTS5、vaporetto、Ollama）。プロジェクト非依存 |

---

## KB Information（エージェント向け KB メタデータ） <a id="kb-information-エージェント向け-kb-メタデータ"></a>

すべての mrag プロジェクトには **`kb_information.yaml`** が同梱されます。これは外部 AI エージェント（Agentic RAG ワークフロー）が読み取るための自己記述メタデータで、mrag 自身はランタイムでは参照しません。

### 役割分担

```
mrag.yaml             → ランタイム設定（mrag が読む）
kb_information.yaml   → エージェント向けの意味的記述（外部エージェントが読む）
```

`mrag.yaml` は実行制御（Qdrant モード、デフォルトプロファイル、トークナイザー等）を持ち、`kb_information.yaml` は「この KB が何のためのものか」を記述してエージェントが選択判断に使います。

### 例

```yaml
version: 1

knowledge_base:
  id: kb_device
  name: Device Development Knowledge
  description: >
    M5Stack、SIM7080G、MQTT、LTE、BraveJIG、組込みデバイス開発、
    現地トラブルシューティング向けのナレッジベース。

agent_usage:
  tags:
    - m5stack
    - sim7080g
    - mqtt
    - lte

  best_for:
    - SIM7080G / LTE モジュールのトラブルシューティング
    - MQTT publish 停止・keepalive 問題

  avoid_for:
    - 契約レビュー
    - 経理処理

  preferred_profiles:
    - default

  example_queries:
    - SIM7080G で MQTT publish が数時間で止まる
```

### init モード一覧

| モード | コマンド | 結果 |
|---|---|---|
| 対話 | `mrag init --name kb-device` | name / kb_id / description をプロンプト。他のフィールドは空のまま（後で編集） |
| 非対話 | `mrag init --name kb-device --non-interactive` | minimal テンプレート（description 空、preferred_profiles=["default"] のみ） |
| JSON 入力 | `mrag init --non-interactive --kb-info-json kb_info.json` | JSON から完全生成 — LLM エージェント推奨 |

### JSON 入力スキーマ

`--kb-info-json` 入力ファイル用の JSON Schema を取得：

```bash
mrag init --print-kb-info-schema
# または
mrag kb-info schema
```

必須フィールド: `project.name`, `knowledge_base.{id, name, description}`
任意フィールド: `agent_usage.{tags, best_for, avoid_for, preferred_profiles, example_queries}`

入力 JSON 例：

```json
{
  "project": {"name": "device-kb"},
  "knowledge_base": {
    "id": "kb_device",
    "name": "Device Development Knowledge",
    "description": "組込みデバイス開発ナレッジベース"
  },
  "agent_usage": {
    "tags": ["m5stack", "sim7080g"],
    "best_for": ["LTE トラブルシューティング"],
    "preferred_profiles": ["default"]
  }
}
```

### 表示・検証

```bash
mrag kb-info show       # 現プロジェクトの kb_information.yaml を表示
mrag kb-info validate   # v1 スキーマで検証
mrag kb-info schema     # JSON Schema 表示（--print-kb-info-schema と同等）
```

### 機械可読の検索出力

Agentic RAG の呼び出し向けに、`mrag search --json` は単一の JSON オブジェクトを stdout に出力します（ステータス行や警告は stderr へ）：

```bash
mrag search "MQTT keepalive" --json
```

ペイロードには `query`, `profile`, `strategy`, `reranked`, `result_count`, `results[]`, `score_stats`, `document_distribution` が含まれます。

---

## API リファレンス

`mrag serve` でサーバーを起動した後、以下のエンドポイントを呼び出せます：

### `POST /api/v1/retrieve`

ナレッジベースからチャンクを検索します。

**リクエストボディ:**

```json
{
  "query": "access control policy",
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
  "query": "access control policy",
  "profile": "default",
  "strategy": "hybrid",
  "results": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "filename": "manual.pdf",
      "score": 6.39,
      "content": "…access control policy defines the permitted operations…",
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
  "query": "access control policy",
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
      "content": "…access control policy defines the permitted operations…",
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
  source_format: markdown
  chunk_size: 800
  overlap: 120
  preserve_heading_path: true
  preserve_tables: true
  preserve_code_blocks: true

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

augmentation:
  strategy: none            # none（デフォルト）| contextual

keyword:
  provider: sqlite_fts5
  tokenizer: vaporetto       # init 時に自動設定。vaporetto 未検出時は trigram
  fallback_tokenizer: trigram
```

`profiles/` に新しい YAML を配置して `mrag index --profile <name>` を実行することで、複数のプロファイルを使い分けられます。

### チャンキングストラテジー

`chunking.strategy` フィールドで、インデックス前にドキュメントをどのように分割するかを制御します。4種類のストラテジーが使用できます：

| ストラテジー | 説明 |
|------------|------|
| `recursive` | 段落 → 改行 → 文のような区切り文字の階層で再帰的に分割。プレーンテキストおよび PDF に最適。**デフォルト。** |
| `markdown_recursive` | まず Markdown の見出し構造で分割し、各セクション内でさらに再帰分割。`source_format: markdown` と組み合わせて使用。 |
| `block_aware` | Markdown ブロック認識型。ドキュメントを見出し・段落・テーブル・コードブロック等の型付きブロックに分解してチャンクを組み立てる。テーブルとコードブロックはアトミック単位として保護（途中で分割しない）。見出しパスをチャンクメタデータに付与し、検索結果に `section:` 表示を追加。`source_format: markdown` と組み合わせて使用。 |
| `parent_child` | 精度の高い検索のために小さな**チャイルドチャンク**をインデックスし、コンテキストとして大きな**ペアレントチャンク**を返す。重複するペアレントを自動除去。`retrieval.strategy: parent_child` と組み合わせて使用。 |

**設定フィールド:**

```yaml
chunking:
  strategy: recursive       # recursive | markdown_recursive | block_aware | parent_child
  source_format: markdown   # text | markdown  （デフォルト: markdown）
  chunk_size: 800           # チャンクの目標文字数
  overlap: 120              # 隣接チャンク間のオーバーラップ文字数
  # --- ブロック認識オプション: source_format: markdown 時に任意のストラテジーで有効 ---
  preserve_heading_path: true   # 見出しパンくずをチャンクに付与（デフォルト: true）
  preserve_tables: true         # テーブルをアトミック単位として保護（デフォルト: true）
  preserve_code_blocks: true    # フェンスコードブロックをアトミック単位として保護（デフォルト: true）
  # --- parent_child のみ ---
  # parent:
  #   strategy: fixed_size   # fixed_size | section
  #   max_chars: 3000
  # child:
  #   strategy: recursive
  #   chunk_size: 600
  #   overlap: 100
```

**Parent ストラテジー（`parent_child` のみ）:**

- **`fixed_size`**（デフォルト）— `max_chars` 文字単位で再帰的区切り文字分割により parent を生成。任意のドキュメント形式に対応。
- **`section`** — Markdown 見出し境界で parent を分割。各見出しセクションが 1 parent となる。サイズ超過セクションは `fixed_size` ロジックで再分割。見出しが存在しない場合は自動的に `fixed_size` 相当の挙動にフォールバックする。技術ドキュメントや Wiki など見出し境界に意味的価値がある構造化 Markdown 文書に向く。

**ブロック認識前処理（全ストラテジーで使用可能）**

`source_format: markdown` を設定し、いずれかの `preserve_*` オプションを有効にすると、`strategy` の設定に関わらず内側のチャンカーにブロック認識前処理が適用されます。つまり `recursive`・`markdown_recursive`・`parent_child` のいずれも、Markdown ドキュメントで使用する際にテーブル/コードブロック保護と見出しパス付与の恩恵を受けられます：

```yaml
chunking:
  strategy: recursive          # または markdown_recursive, parent_child
  source_format: markdown      # ブロック認識ラッピングを有効化
  preserve_heading_path: true
  preserve_tables: true
  preserve_code_blocks: true
```

`block_aware` ストラテジー名は後方互換のために残されており、`strategy: recursive` + `source_format: markdown` + 全 preserve オプション有効と同等です。

**ストラテジーの選び方:**

- **`recursive`** — プレーンテキストや PDF に使用。日本語を含む全言語で安定して動作します。
- **`markdown_recursive`** — 見出し構造が明確なドキュメント（技術仕様書、Markdown でエクスポートした Wiki など）に使用。セクションのコンテキストをチャンク内に保持するため、検索精度が向上しやすいです。
- **`block_aware`** — テーブル・コードブロック・ネストされた見出しを含む Markdown ドキュメントに使用。テーブルとコードブロックはチャンク境界をまたいで分割されることがありません。すべてのチャンクに見出しパスメタデータが付与され、`mrag search` の結果に `section: H1 > H2 > H3` パンくずが表示されます。
- **`parent_child`** — 精度の高いチャイルドチャンクマッチングと、より豊富なペアレントチャンクのコンテキストが必要な場合に使用。`retrieval.strategy: parent_child` と組み合わせます。重複除去後のペアレント候補を十分確保するため `dense_top_k` / `keyword_top_k` は `top_k × 3` 以上に設定してください。

**見出しパスメタデータを持つ検索結果の表示例:**

```
[1] score=0.8421  doc=manual.md  chunk=a3f2b1c4...
    section: SIM7080G > MQTT > KeepAlive
    MQTT の keepalive 設定は AT+CMQTTKEEPALIVE で変更できます...
```

> **注意:** `chunking.strategy`、`chunk_size`、`overlap`、または `preserve_*` フラグを変更すると既存のインデックスが無効になります。チャンキング設定を変更した後は `mrag reindex` で最初から再構築してください。

### 検索ストラテジー

プロファイルの `retrieval.strategy` フィールドで検索方式を切り替えます。4種類のストラテジーが使用できます：

| ストラテジー | 説明 |
|------------|------|
| `hybrid` | キーワード（BM25）とベクター検索の結果を Reciprocal Rank Fusion（RRF）で統合。**ほとんどのユースケースで推奨するデフォルト設定。** |
| `keyword` | SQLite FTS5 BM25 によるフルテキスト検索のみ。高速で、クエリ時に Embedding 不要。 |
| `vector` | Qdrant コサイン類似度によるベクター検索のみ。クエリと文書の表現が異なる場合（言い換え・意味検索）に強い。 |
| `parent_child` | チャイルドチャンクを検索し、ペアレントチャンクに解決して重複除去した上で返す。`chunking.strategy: parent_child` と組み合わせて使用。 |

**ストラテジーごとの設定フィールド:**

```yaml
retrieval:
  strategy: hybrid      # hybrid | keyword | vector
  top_k: 8              # 最終的に返す件数
  dense_top_k: 20       # 融合前に Qdrant から取得する候補数（hybrid / vector で使用）
  keyword_top_k: 20     # 融合前に FTS5 から取得する候補数（hybrid / keyword で使用）
  fusion: rrf           # rrf（デフォルト）| weighted
  # weights: [0.7, 0.3] # fusion=weighted の場合のみ。[vector, keyword] の順
```

`dense_top_k` / `keyword_top_k` は対応するサブ検索が有効なときのみ使用されます。`top_k` より大きい値を設定することで融合ステップに渡す候補が増え、精度が向上します（レイテンシはわずかに増加）。

**融合アルゴリズム:**

- **`rrf`**（デフォルト）— Reciprocal Rank Fusion。順位のみを使う（`score = Σ 1/(k+rank)`、k=60）。vector と keyword のスコアレンジ差の影響を受けない頑健な手法。チューニング不要で、ほとんどのケースで推奨。
- **`weighted`** — 各リストのスコアを min-max で `[0, 1]` に正規化してから重み付き和を取る。スコアの「強さ」が結果に反映される（トップヒットが圧倒的なクエリではその強度がそのまま順位に影響）。`weights: [vector, keyword]` で個別重み付けが可能。テーブル中心や専門用語の多いコーパスで `weights: [0.3, 0.7]` のように keyword 寄りにバイアスする用途に有効。

`weights` は検索時のみに使われるパラメータです。変更してもインデックスは無効化されません（reindex 不要）。

**ストラテジーの選び方:**

- **`hybrid`** — ほとんどの場面でベストな選択。製品コードや日本語キーワードのような完全一致クエリと、意味的なクエリの両方に対して安定した結果を返します。
- **`keyword`** — クエリが文書中の正確な表現を含む場合に適しています（型番、エラーコードなど）。Ollama / Qdrant が利用できない環境でも動作します。
- **`vector`** — クエリが文書の表現と異なる言い回しの場合に有効（概念に関する質問など）。クエリ時に Ollama が起動している必要があります。
- **`parent_child`** — `chunking.strategy: parent_child` プロファイルと組み合わせて使用。小さなチャイルドチャンクで精度の高い検索を行い、重複除去されたペアレントチャンクをコンテキストとして返す。重複除去後に十分なペアレント候補を確保するため `dense_top_k` / `keyword_top_k` は `top_k × 3` 以上（例：`top_k: 8` → `dense_top_k: 60`）に設定してください。

> **注意:** ストラテジーはグローバル設定ではなく**プロファイル単位**で設定します。異なるストラテジーを持つ複数のプロファイルを用意し、`--profile <name>` で切り替えることができます。

### リランキング

`rerank.enabled: true` を設定すると、検索後に CrossEncoder による再スコアリングが行われ、結果の順序が改善されます。`top_n` 件の候補を取得してから再スコアし、最終的な返却件数は呼び出し側で指定します（CLI なら `--top-k`、API なら リクエストの `top_k`）。

```yaml
rerank:
  enabled: true
  provider: sentence-transformers
  model: hotchpotch/japanese-reranker-cross-encoder-small-v1
  max_length: 512  # トークン切り捨て上限。BERT 系モデルでは 512 のまま使用すること
  top_n: 30        # リランキング前に取得する候補数。最終件数は呼び出し側（--top-k / API top_k）で決定
```

リランキングはクエリ時にのみ適用されます。`rerank` の設定を変更しても再インデックスは不要です。使用には `uv pip install -e ".[reranker]"` が必要です。

`mrag search`・`mrag eval`・`mrag serve` の `--no-rerank` オプションで実行時に無効化できます。

> **注意: `parent_child` プロファイルとリランキングの組み合わせ。** `retrieval.strategy: parent_child` を使用している場合、リランキングはペアレント解決後のペアレントチャンク（約3000文字 / 約1361トークン）に対して適用されます。BERT 系リランカー（`hotchpotch/japanese-reranker-cross-encoder-*` を含む全バリアント）は 512 トークンで切り捨てるため、ペアレントチャンクの大部分が失われます。この組み合わせを検出すると mrag は実行時に `WARN` を表示します。`parent_child` プロファイルでは `rerank.enabled: false` のままにすることを推奨します。チャイルドチャンクのマッチングで検索精度は確保されており、広範囲なペアレント文脈が主な価値だからです。

### コンテキスト拡張（Contextual Augmentation）

`augmentation.strategy: contextual` を設定すると、`mrag index` 実行時にチャンクごとに Ollama LLM を呼び出してコンテキスト説明文を生成します。この説明文はチャンク内容の前に付加されてから embedding されるため、個々のチャンクがドキュメント全体のどの部分に関するものかをモデルが理解しやすくなり、セマンティック検索の精度が向上します。

```yaml
augmentation:
  strategy: contextual        # none（デフォルト）| contextual
  provider: ollama
  model: gemma4:e4b           # 生成モデル — embedding.model とは別
  endpoint: http://localhost:11434
```

[Anthropic contextual retrieval](https://www.anthropic.com/news/contextual-retrieval) パターンに基づいた実装です。生成モデル（`gemma4:e4b`）は Embedding モデル（`bge-m3`）とは独立しています。

有効化時、mrag は **Contextual Embeddings（ベクター）と Contextual BM25（FTS5 キーワード）の両方** を構築します。同じ「コンテキスト + チャンク」テキストが両方のインデックスに格納される Anthropic 推奨の完全構成です。

**重要な仕様:**

- `strategy: none`（デフォルト）— LLM 呼び出しなし。インデックス速度に影響なし
- `augmentation.strategy` を変更するとプロファイルハッシュが変わるため、次回の `mrag index` で全チャンクが再インデックスされます
- `strategy: contextual` でのインデックスは遅くなります（1チャンクにつき1回の LLM 呼び出し）
- **ドキュメント truncation 制約（ローカル LLM の制限）:** プロンプトの `{document}` プレースホルダーはローカル生成モデルに渡される前に 8000 文字に切り詰められます。8000 文字を超えるドキュメントの場合、末尾近くのチャンクはドキュメント前半部分のみを文脈としてコンテキストが生成されるため、文脈の関連度が下がる可能性があります。これは `gemma4:e4b` などコンテキストウィンドウが限られたローカルモデルとローカルファースト運用のトレードオフです。回避策：長いコンテキスト対応の生成モデルを使う、または非常に長いドキュメントを `mrag add` 前に分割しておく
- 一時的な Ollama タイムアウトや HTTP 5xx エラーはエクスポネンシャルバックオフで自動リトライされます。ログの `↻ retry` 行で状況を確認できます
- リトライを使い果たしても失敗するチャンク（OCR/表ノイズによる空レスポンス連発など）は、ドキュメント全体を失敗させる代わりに raw バリアントとして保存されます。成功行に `(N raw fallback)` と表示され、`⤵ fallback` ログで対象チャンクを確認できます
- チャンク数が 300 以上のドキュメントはインデックス時に `⚠ large document` 警告を表示します（情報提供のみ、エラーではありません）

**リトライ・失敗ポリシー設定（任意）:**

デフォルトのリトライポリシー（3回試行、初回遅延 2 秒、×2 バックオフ、上限 30 秒）とデフォルトの失敗ポリシー（`raw_fallback`）はほとんどの環境で適切です。必要に応じてプロファイルごとに上書きできます：

```yaml
augmentation:
  strategy: contextual
  provider: ollama
  model: gemma4:e4b
  endpoint: http://localhost:11434
  retry:
    max_attempts: 5
    initial_delay_seconds: 3.0
    backoff_multiplier: 2.0
    max_delay_seconds: 60.0
  failure_policy:
    mode: raw_fallback   # raw_fallback（デフォルト）| fail_document
```

`failure_policy.mode: fail_document` を指定すると、1チャンクでも失敗した場合にドキュメント全体を失敗とする 0.7 以前の挙動に戻ります。`retry` および `failure_policy` の変更はプロファイルハッシュに影響しないため、インデックスの再構築は不要です。

同じ `retry` ブロックは `embedding` にも設定できます。

**プロジェクトごとのプロンプトカスタマイズ:**

`mrag init` 実行時に `profiles/context_prompt.txt` がデフォルトのプロンプトテンプレートで生成されます。このファイルを編集することで、ドメインに特化した指示を LLM に与えられます：

```bash
# 現在のプロンプトを確認
cat profiles/context_prompt.txt

# 編集（{document} と {chunk} プレースホルダーは必ず残すこと）
nano profiles/context_prompt.txt

# 新しいプロンプトを反映してインデックスを再構築
mrag reindex
```

プロンプトファイルはインデックス時に自動的に読み込まれます。編集内容は `mrag reindex` を実行するまで既存チャンクには反映されません。

---

## アーキテクチャ

```
mrag CLI
  ├── mrag add      → テキスト抽出 → SQLite（documents テーブル）
  ├── mrag index    → チャンキング → [コンテキスト拡張（LLM、任意）] → Embedding（Ollama）
  │                              → SQLite（chunks + chunk_variants）+ Qdrant + FTS5
  └── mrag search   → キーワード（FTS5 BM25）+ ベクター（Qdrant）→ RRF 融合 → [リランカー] → 結果

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
├── mrag.yaml                    # プロジェクト設定（名前、KB ID、トークナイザー、Qdrant 接続先）
├── mrag.db                      # SQLite データベース
├── profiles/
│   ├── default.yaml             # 検索プロファイル
│   └── context_prompt.txt       # コンテキスト拡張用 LLM プロンプト（編集可能）
├── data/
│   └── documents/
│       └── <doc-id>/
│           ├── original.pdf     # 元ファイルのコピー
│           ├── extracted.txt    # 抽出されたプレーンテキスト
│           ├── extracted.md     # 抽出された Markdown
│           └── extraction_meta.json
├── qdrant/                      # Qdrant ベクターストレージ（mode: local 時にここへ保存）
└── cache/
    └── embeddings/              # Embedding キャッシュ（任意）
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
| `profiles/` | 検索プロファイル YAML + `context_prompt.txt` |
| `data/documents/` | 元ファイル + 抽出テキスト |
| `qdrant/` | Qdrant ベクターデータ（local モード時） |

> **前提条件:** 移行先ホストで同じ Embedding モデルを持つ Ollama が起動していること（新規インデックスやベクター検索時に必要）。既存ドキュメントは `qdrant/` に事前構築済みのベクターが含まれるため、再 Embedding なしで即座に検索できます。

`mode: server` を使用している場合は `qdrant/` を除いてコピーし、移行先で Qdrant サーバーを起動してから `mrag reindex` を実行してください。

---

## ライセンス

GNU Affero General Public License v3.0のもとで配布されます。

Copyright (c) 2026 BathTimeFish KK.

---

## 謝辞

mrag は PDF テキスト抽出とテーブル検出に [PyMuPDF](https://github.com/pymupdf/pymupdf) を利用しています。PyMuPDF は [Artifex Software](https://artifex.com) が開発・メンテナンスしており、AGPL-3.0 ライセンスのもとで配布されています。

mrag は SQLite FTS5 による日本語形態素解析に [@hotchpotch](https://github.com/hotchpotch) 氏の [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) を利用しています。

- **sqlite-vaporetto** — `MIT OR Apache-2.0` ライセンス
- **同梱モデル**（`bccwj-suw+unidic_pos+kana.model.zst`、`-with-model` リリースに含まれる）— [BSD-3-Clause](https://opensource.org/license/BSD-3-Clause) ライセンス。[daac-tools/vaporetto-models](https://github.com/daac-tools/vaporetto-models/releases) を出典とします

mrag を sqlite-vaporetto ライブラリやそのモデルと一緒に再配布する場合は、モデルの BSD-3-Clause 著作権表示を配布物に含める必要があります。
