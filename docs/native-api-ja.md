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
- **`top_k`** — 最終的に返す件数（`1` 以上 `100` 以下）。省略時は解決されたプロファイルの `retrieval.top_k` に従います

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
      "filename": "manual.md",
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

activeなdocument exclusionは、結果を返す前にすべてのstrategyへ適用されます。exclusionは
source documentを保持するため、検索結果から全chunkが除外されても同じdocumentは
`GET /api/v1/documents`に引き続き現れます。詳細は
[ドキュメントの検索除外](./document-exclusions-ja.md)を参照してください。


## `GET /api/v1/documents` — ドキュメント一覧 / 詳細

### 一覧

```http
GET /api/v1/documents?profile=default&all=false&status=stale&status=ready&limit=100&offset=0 HTTP/1.1
Authorization: Bearer <MRAG_API_KEY>
```

パラメータはすべて省略可能で、**受け付けるのはこの 5 つだけです**。それ以外は無視せず `400` で拒否します。

| パラメータ | 意味 |
|---|---|
| `profile` | 行の index 状態と除外状態を答える profile。既定はサーバーの `--profile`。存在しない profile は `404 profile_not_found`。 |
| `all` | `true` で抽出が完了していない文書（`pending`、`error`）も含める。既定の `false` では抽出済みの文書だけ。 |
| `status` | 繰り返し可。`aggregate_status` がいずれかに一致する行だけを残す。 |
| `limit` | 1 ページの件数（1〜500）。既定 100。 |
| `offset` | 読み飛ばす行数。末尾を超える offset はエラーではなく空のページ。 |

レスポンス：

```json
{
  "schema_version": 1,
  "status": "ok",
  "profile": "default",
  "filter": {"all": false, "statuses": []},
  "total": 1,
  "returned": 1,
  "page": {"limit": 100, "offset": 0, "count": 1, "next_offset": null},
  "documents": [
    {
      "document_id": "abcdef0123456789",
      "display_name": "docs/manual.md",
      "source_identity": "docs/manual.md",
      "source_binding_status": "project_relative",
      "content_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      "status": "extracted",
      "aggregate_status": "indexed",
      "source_status": "ready",
      "index_status": "indexed",
      "retrieval_status": "eligible",
      "profile": "default",
      "exclusion_id": null,
      "created_at": "2026-05-22T10:00:00",
      "updated_at": "2026-05-22T10:00:00",
      "ingest_ms": null,
      "id": "abcdef0123456789",
      "filename": "manual.md",
      "file_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      "source_type": "md"
    }
  ]
}
```

envelope：

- **`total`** — 表示範囲（`all`）が認める文書数。status による絞り込みの前。
- **`returned`** — status で絞り込んだ後の文書数（全ページ合計）。`total` と `returned` で「文書が無い」と「絞り込みに一致しない」を区別できます。
- **`page.count`** — このレスポンスの行数。**`page.next_offset`** — 次に指定する offset。最後のページでは `null`。

行のフィールド（行・状態・並び順は MCP の `list_documents` および MRAG Plus と共通）：

- **`status`** — 保存されている抽出状態 `pending`、`extracted`、`error`。以前のリリースや詳細レスポンスと同じ値です。
- **`aggregate_status`** — 選択した profile についての状態を 1 つにまとめたもので、`status=` が絞り込む対象です。`excluded`、`error`、`pending`、`indexing`、`stale`、`fallback`、`indexed`、`ready` のうち、この順で最初に当てはまるもの。`ready` は抽出済みでこの profile ではまだ index されていない状態です。
- **`source_status`** — `building`、`ready`、`error`。
- **`index_status`** — 選択した profile について `not_indexed`、`pending`、`indexing`、`indexed`、`fallback`（一部の chunk が素のテキストへ fallback したか vector を持たない）、`stale`（index 後に文書か profile が変わった）、`error`。
- **`retrieval_status`** / **`exclusion_id`** — この profile に効く除外があれば `excluded`。profile を限定した規則が全 profile の規則より優先して報告されます。
- **`content_hash`** — 抽出済み原本の SHA-256（接頭辞なし 64 桁 hex）。抽出が完了していない文書では `null`。**`file_hash`** には常に保存値が入ります。
- **`source_identity`** — 元ファイルのパスに基づく安定した識別子で、保存値そのものです。プロジェクト内のパスはそのまま、プロジェクト外のソースは `identities/external/<root-key>/<path>`（`external_root`）、移行した行は `identities/legacy/v1/<document_id>`（`legacy_unbound`）になります。`display_name` に root key は表示しません。identity scheme 1 のままの catalog（1.1.0 で作成）は保存済みの `external/...` や `legacy/...` をそのまま返し、`mrag catalog migrate-identities` が変換するとおりに解釈します。
- **`ingest_ms`** — 常に `null`（mrag は取り込み時間を記録しません）。MRAG Plus と行の形を揃えるためのフィールドです。
- 並び順は `(source_identity, document_id)` です。

**バッチ差分取得の例。** 変わったものだけを再 index するジョブは、この profile が追いついていない文書をページ単位で取得できます。

```bash
offset=0
while :; do
  page=$(curl -s -H "Authorization: Bearer $MRAG_API_KEY" \
    "http://127.0.0.1:8000/api/v1/documents?status=stale&status=ready&limit=500&offset=$offset")
  echo "$page" | jq -r '.documents[] | [.document_id, .aggregate_status, .display_name] | @tsv'
  offset=$(echo "$page" | jq '.page.next_offset')
  [ "$offset" = "null" ] && break
done
```

以前のリリースは全文書を配列で返していました。1.2.0 はこの envelope を返し、`all=true` を指定しない限り抽出済みの文書だけを返します。

### 詳細

```http
GET /api/v1/documents/{document_id} HTTP/1.1
```

詳細レスポンスは従来の抽出中心の項目（`id`、`filename`、`file_hash`、
`status`、`created_at`）に `extracted_text_path` と `chunk_count` を加えた形です。
ここでの `status` は保存済み抽出状態（`pending` / `extracted` / `error`）で、一覧と同じ値です。
導出した状態と source identity は一覧エンドポイントで確認してください：

```json
{
  "id": "abcdef0123456789",
  "filename": "manual.md",
  "file_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
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
