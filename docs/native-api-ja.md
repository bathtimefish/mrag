# mrag ネイティブ REST API — `native-api`

このドキュメントでは、`mrag serve` が公開する **ネイティブ REST API**（`/api/v1/*`）について解説します。

mrag は `mrag serve` を起動するとデータ連携用 API サーバが起動します。API は Native API と Dify 用の外部ナレッジ API がサポートされています。このドキュメントは Native API について解説します。Dify API については [Dify API のドキュメント](./dify-api-ja.md) を参照ください。

Native API は mrag を直接プログラムから呼び出す用途を想定しており、検索だけでなく **ドキュメント / プロファイル一覧の参照**もカバーします。レスポンスは Dify API よりも詳細で、`chunk_id` / `document_id` / `reranked` フラグなどの内部情報がそのまま返ります。

> 補足：Native API は mrag 固有の仕様です。FastAPI が自動生成する OpenAPI 仕様（`/openapi.json`）から型付きクライアントを生成する用途を想定しています。

> 前提：プロジェクトディレクトリの中（`mrag.yaml` がある場所）で `mrag serve` を起動します。ドキュメントを `mrag add` → `mrag index` 済みであることが前提です。


## エンドポイント一覧

| Method | Path | 役割 |
|---|---|---|
| `POST` | `/api/v1/retrieve` | クエリを投げて関連チャンクを取得（`/api/v1/search` エイリアスあり） |
| `GET` | `/api/v1/documents` | 登録済みドキュメントの一覧 |
| `GET` | `/api/v1/documents/{document_id}` | ドキュメント単体の詳細（チャンク数を含む） |
| `GET` | `/api/v1/profiles` | プロファイル一覧 |
| `GET` | `/api/v1/profiles/{profile_name}` | プロファイル単体の詳細 |

> 補足：自動生成された OpenAPI ドキュメント（Swagger UI）は `http://<host>:<port>/docs`、Redoc は `/redoc`、生 JSON は `/openapi.json` で参照できます。本ドキュメントと並行して活用してください。


## mrag serve のセットアップ

```bash
cd /path/to/my-kb

# （任意）API キーを設定
export MRAG_API_KEY="任意の長い秘密文字列"

mrag serve --host 0.0.0.0 --port 8000
```

`mrag serve` のオプション（`--profile` / `--no-rerank` など）と認証の挙動は Dify API と共通です。詳しい説明は [dify-api-ja.md](./dify-api-ja.md) の「mrag 側のセットアップ」節を参照してください。


## `POST /api/v1/retrieve` — 検索

mrag の検索ロジックを直接叩くエンドポイントです。`/api/v1/search` は同じハンドラのエイリアスです。

### リクエスト

```http
POST /api/v1/retrieve HTTP/1.1
Host: your-mrag-host:8000
Authorization: Bearer <MRAG_API_KEY>
Content-Type: application/json
```

```json
{
  "query": "クエリ",
  "profile": "default",
  "strategy": "hybrid",
  "top_k": 5
}
```

各フィールドの意味：

- **`query`** — 検索クエリ文字列（必須）。フィールド欠落で 422
- **`profile`** — プロファイル名。省略時は `mrag.yaml` の `default_profile`。存在しないプロファイルを指定すると 404
- **`strategy`** — 検索戦略の上書き（`hybrid` / `vector` / `keyword` / `parent_child`）。省略時はプロファイルの `retrieval.strategy` に従います。`parent_child` を指定する場合はインデックス側もそのプロファイルで作られている必要があります（子チャンクが存在しないと正しく動作しません）
- **`top_k`** — 最終的に返す件数（`1` 以上 `100` 以下、デフォルト `5`）

### レスポンス

```json
{
  "query": "クエリ",
  "profile": "default",
  "strategy": "hybrid",
  "reranked": true,
  "results": [
    {
      "chunk_id": "...",
      "document_id": "...",
      "filename": "manual.pdf",
      "score": 0.823412,
      "content": "ヒットしたチャンクの本文",
      "metadata": {
        "chunk_index": 12,
        "retrieval_score": 0.42
      }
    }
  ]
}
```

各フィールドの意味：

- **`query`** / **`profile`** / **`strategy`** — 実際にサーバー側で適用された値（リクエストでの省略やプロファイル既定の解決結果が反映されます）
- **`reranked`** — CrossEncoder によるリランキングが適用されたかどうか
- **`results[].chunk_id`** — チャンクの DB プライマリキー。後続で [mrag inspect chunk](./inspect-ja.md) に渡せます
- **`results[].score`** — 検索戦略本来のスコア（リランキング有効時は CrossEncoder のスコアに置き換わります。**Dify API のような `[0, 1]` 正規化は適用されません**）
- **`results[].metadata.retrieval_score`** — リランキング有効時のみ。リランキング前のスコア（→ [reranking-ja.md](./reranking-ja.md)）


## `GET /api/v1/documents` — ドキュメント一覧 / 詳細

### 一覧

```http
GET /api/v1/documents HTTP/1.1
Authorization: Bearer <MRAG_API_KEY>
```

レスポンス：

```json
[
  {
    "id": "abcdef0123456789",
    "filename": "manual.pdf",
    "file_hash": "sha256:...",
    "status": "indexed",
    "created_at": "2026-05-22T10:00:00"
  }
]
```

各フィールド：

- **`id`** — `mrag add` 時に払い出されるドキュメント ID
- **`status`** — ドキュメントの**抽出処理**ステータス。値は `pending` / `extracted` / `error` のいずれか（インデックス処理のステータスではない点に注意。プロファイル別のインデックス状態は SQLite の `document_indexes` テーブルに保持されています）
- **`created_at`** — `mrag add` した日時

### 詳細

```http
GET /api/v1/documents/{document_id} HTTP/1.1
```

レスポンスは一覧の各エントリに **`extracted_text_path`** と **`chunk_count`** が追加されたものです：

```json
{
  "id": "abcdef0123456789",
  "filename": "manual.pdf",
  "file_hash": "sha256:...",
  "status": "indexed",
  "created_at": "2026-05-22T10:00:00",
  "extracted_text_path": "data/extracted/abcdef0123456789.md",
  "chunk_count": 42
}
```

> 補足：`chunk_count` はそのドキュメント配下の全プロファイル横断の合計件数です。プロファイル別に確認したい場合は [`mrag inspect document`](./inspect-ja.md) を使ってください。

存在しない `document_id` を指定すると 404 が返ります。


## `GET /api/v1/profiles` — プロファイル一覧 / 詳細

### 一覧

```http
GET /api/v1/profiles HTTP/1.1
```

レスポンス：

```json
[
  {
    "name": "default",
    "strategy": "hybrid",
    "embedding_model": "nomic-embed-text",
    "chunking_strategy": "recursive"
  }
]
```

`profiles/*.yaml` を読み込んだ結果が返ります。`mrag.yaml` 側に登録済みでも YAML ファイルが見つからないプロファイルは除外されます。

### 詳細

```http
GET /api/v1/profiles/{profile_name} HTTP/1.1
```

レスポンスは一覧の各エントリに **chunking / retrieval 関連の主要パラメータ**が追加されたものです：

```json
{
  "name": "default",
  "strategy": "hybrid",
  "embedding_model": "nomic-embed-text",
  "chunking_strategy": "recursive",
  "chunk_size": 800,
  "overlap": 120,
  "dense_top_k": 30,
  "keyword_top_k": 30,
  "fusion": "rrf"
}
```

> 補足：プロファイルの完全な YAML 設定が必要な場合は `mrag profiles show <name>` を使ってください。本エンドポイントはエージェント / ダッシュボード向けの抜粋情報です。

存在しない `profile_name` を指定すると 404 が返ります。


## 認証

`MRAG_API_KEY` 環境変数の設定は [Dify API](./dify-api-ja.md) と同じで現時点で簡易な仕様です。Native API の各エンドポイントでも `Authorization: Bearer <MRAG_API_KEY>` ヘッダが必須となります。

> 重要：認証失敗時のエラー形式は **Dify API とは異なります**。
> - Dify API（`/retrieval`）: `{"error_code": 1001, "error_msg": "..."}`
> - Native API（`/api/v1/*`）: `{"detail": "Unauthorized"}`
>
> どちらも HTTP ステータスは `401` ですが、レスポンスボディの構造が違うのでクライアント側で分岐してください。


## エラーコード一覧

| HTTP | 発生条件 |
|---|---|
| 401 | 認証必須時の `Authorization` ヘッダ欠落 / 不一致（`{"detail": "Unauthorized"}` 形式） |
| 404 | プロファイル名 / ドキュメント ID が存在しない |
| 422 | `query` フィールド欠落、`top_k` 範囲外、JSON 構造不正 |
| 503 | Qdrant / 内部リソース不到達。`{"detail": "<原因>"}` を返します（リトライ可能） |

Native API のエラーレスポンスはすべて FastAPI のデフォルト形式（`{"detail": ...}`）に従います。


## OpenAPI ドキュメント

`mrag serve` 起動中は、ブラウザから以下にアクセスできます：

| URL | 内容 |
|---|---|
| `http://<host>:<port>/docs` | Swagger UI |
| `http://<host>:<port>/redoc` | Redoc |
| `http://<host>:<port>/openapi.json` | 生の OpenAPI 仕様 JSON |

これらは `MRAG_API_KEY` を設定していても**認証の対象外**です（ローカルでの API 確認・ヘルスチェック用途を想定）。本番運用ではリバースプロキシ側でアクセス制限を掛けることを検討してください。


## Tips

- **`reranked` フラグで挙動を切り分け**：CI で「リランキング有効プロファイルが期待通りに動いているか」を確認するなら、検索レスポンスの `reranked: true` を assert すると単純に検出できます
- **`score` の絶対値は比較対象にしない**：戦略間で目盛が違うので、`hybrid` の `0.8` と `keyword` の `0.8` は意味が違います。クエリ間で順位を比較するならランクを使い、絶対値を使うならクライアント側で正規化してください
- **検索戦略のリクエスト時上書き**：プロファイルを差し替えずに `strategy` だけ切り替えたい場合（同じインデックスで `hybrid` と `vector` を比較したい等）、リクエストの `strategy` フィールドが便利です
- **Dify ライクな `[0, 1]` 正規化が必要な場合**は Dify エンドポイントを使うか、クライアント側で実装してください（→ [dify-api-ja.md](./dify-api-ja.md) の「スコアの正規化」節）
