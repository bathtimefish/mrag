# MCP server — `mrag mcp`

`mrag mcp` は、1 つの mrag プロジェクトを read-only の Model Context Protocol server として公開します。

`list_documents` は Native API の `GET /api/v1/documents` と同じもの —
同じ envelope（`total`、`returned`、`page`）・行・状態・並び順 — を返し、
同じ引数 `profile`、`all`、`status`（集約状態のリスト）、`limit`（1〜500、既定 100）、
`offset` を受け付けます。`all` を指定しなければ抽出済みの文書だけを返します。旧データは
元パスを推測せず `legacy_unbound` と示します。各フィールドは
[Native API](./native-api-ja.md#一覧) を参照してください。

MCP 対応クライアントから `mrag search --json` を shell 実行する代わりに、MCP tools/resources として mrag を呼び出せます。

## インストール

MCP server は公式 Python MCP SDK を optional dependency として利用します。

```bash
uv pip install -e ".[mcp]"
```

他の extra と組み合わせる場合:

```bash
uv pip install -e ".[vaporetto,reranker,mcp]"
```

## 最短起動

index 済みの mrag プロジェクト内で実行します。

```bash
mrag mcp
```

オプションなしでは `stdio` MCP server として起動し、次の既定値を使います。

- `project_dir: .`
- `profile: <mrag.yaml の default_profile>`
- `retrieval.strategy: <profile の retrieval.strategy>`
- `retrieval.top_k_default: <profile の retrieval.top_k>`

`stdio` では stdout は MCP JSON-RPC メッセージ専用です。ログや警告は stderr に出ます。

## 設定ファイル

daemon 化やコンテナ運用では YAML に設定をまとめます。

```yaml
version: 1

project_dir: /data/kb
profile: default
transport: streamable-http

retrieval:
  strategy: hybrid
  top_k_default: 5
  top_k_max: 50
  no_rerank: true

http:
  host: 127.0.0.1
  port: 8001
  path: /mcp
  allowed_origins: []

auth:
  bearer_token_env: MRAG_MCP_API_KEY
  bearer_token_file_env: MRAG_MCP_API_KEY_FILE
```

起動:

```bash
mrag mcp --config ./mrag-mcp.yaml
```

設定ファイル内の相対 `project_dir` は、その設定ファイルがあるディレクトリからの相対パスとして解決されます。

このほかに省略可能な節が 3 つあります。`mrag mcp init-config` は既定値付きでこれらも出力します。

| キー | 既定値 | 意味 |
|---|---|---|
| `features.tools` | `true` | tools を公開する |
| `features.resources` | `true` | resources を公開する |
| `features.management_tools` | `false` | `false` のままにします。`true` は設定エラーとして拒否されます |
| `limits.content_max_chars` | `20000` | tool の結果に含まれる chunk 本文と、YAML・抽出テキスト・text の原本の各 resource を、この文字数で切り詰めます。`0` で切り詰めなし。切り詰めた tool の結果には `truncated: true` と `content_chars`・`returned_chars` が付きます |
| `limits.artifact_max_bytes` | `1048576` | `.../original` が返す原本の最大サイズ |
| `limits.request_timeout_seconds` | `60` | 値の検証（1 以上）だけ行います。server はこの値を適用しません |
| `logging.level` | `INFO` | `DEBUG`・`INFO`・`WARNING`・`ERROR` のいずれか（大文字） |

## 環境変数による上書き

環境変数は YAML より優先されます。

```bash
MRAG_MCP_CONFIG=/etc/mrag/mcp.yaml \
MRAG_PROJECT_DIR=/data/kb \
MRAG_MCP_TRANSPORT=streamable-http \
MRAG_MCP_PORT=8001 \
MRAG_MCP_API_KEY=secret \
mrag mcp
```

主な環境変数:

| 変数 | 意味 |
|---|---|
| `MRAG_MCP_CONFIG` | 設定ファイルパス |
| `MRAG_PROJECT_DIR` | プロジェクトディレクトリ |
| `MRAG_MCP_TRANSPORT` | `stdio` または `streamable-http` |
| `MRAG_MCP_PROFILE` | 既定プロファイル |
| `MRAG_MCP_STRATEGY` | 既定検索ストラテジー |
| `MRAG_MCP_TOP_K_DEFAULT` | 既定件数 |
| `MRAG_MCP_TOP_K_MAX` | `top_k` 上限 |
| `MRAG_MCP_NO_RERANK` | rerank 無効化 |
| `MRAG_MCP_HOST` / `MRAG_MCP_PORT` / `MRAG_MCP_PATH` | HTTP bind 設定 |
| `MRAG_MCP_ALLOWED_ORIGINS` | `http.allowed_origins` に入れる origin のカンマ区切りリスト |
| `MRAG_MCP_LOG_LEVEL` | `logging.level` |
| `MRAG_MCP_API_KEY` | HTTP bearer token |
| `MRAG_MCP_API_KEY_FILE` | HTTP bearer token を格納したファイル |

token ファイルは `MRAG_MCP_API_KEY` より先に読まれます。空のファイルは token を
供給しないので、判断は環境変数に委ねられます。空白だけのファイルの場合、HTTP
server と `mrag mcp validate` は認証なしで動かずにエラーで止まります。

## 補助コマンド

```bash
mrag mcp init-config > mrag-mcp.yaml
mrag mcp schema > mrag-mcp.schema.json
mrag mcp validate --config ./mrag-mcp.yaml
mrag mcp --config ./mrag-mcp.yaml --print-effective-config
```

`--print-effective-config` は解決済み secret 値をマスクします。

## Tools

MVP では read-only tools のみ公開します。

| Tool | 用途 |
|---|---|
| `search` | KB を検索する |
| `list_documents` | profile ごとの抽出・索引・検索対象の状態付きドキュメント一覧 |
| `list_profiles` | retrieval profile 一覧 |
| `inspect_document` | ドキュメント単位の index 状態 |
| `inspect_chunks` | chunk metadata 一覧 |
| `inspect_chunk` | 1 chunk の本文/context を確認 |
| `inspect_sections` | heading / parent-child 構造を確認 |

引数（必須なのは各 tool の先頭に挙げたものだけです）:

| Tool | 引数 |
|---|---|
| `search` | `query`、`profile`（既定: server の profile）、`strategy`（既定: MCP 設定の `retrieval.strategy`。未設定なら選択した profile の値）、`top_k`（既定: `retrieval.top_k_default`。未設定なら選択した profile の `retrieval.top_k`）、`no_rerank`（既定: `retrieval.no_rerank`） |
| `list_documents` | `profile`、`all`、`status`、`limit`、`offset`。すべて省略可能で、内容は冒頭の説明のとおり |
| `list_profiles` | なし |
| `inspect_document` | `document_id`、`profile`（1 つの profile に絞る） |
| `inspect_chunks` | `document_id`、`profile`、`limit`（既定: 全 chunk）、`offset`（既定 `0`）、`show_content` と `show_context`（既定 `false`） |
| `inspect_chunk` | `chunk_id` |
| `inspect_sections` | `document_id`、`profile` |

`inspect_chunks` と `inspect_sections` の `profile` を省略できるのは、その document が
ちょうど 1 つの profile で index されている場合だけです。

`search` は `top_k` を上限に丸めません。呼び出しで指定した値でも既定値から解決した値でも、
`retrieval.top_k_max` を超えると tool error（`isError: true`）になります。メッセージは
`top_k must be <= retrieval.top_k_max (50)` で、括弧内は有効な上限値です。

`search` の各結果は `reference` を持ちます — document の名前、保存された原本と
抽出物（project root からの相対パス）、`mrag://documents/{document_id}/original`
リソース。agent は読んだものを引用できます。`mrag search --json` と
`POST /api/v1/retrieve` が返すのと同じオブジェクトで、各フィールドは
[ネイティブ API](./native-api-ja.md#post-apiv1retrieve--検索) を参照してください。

`search` toolはactiveなdocument exclusionを適用します。sourceは保持されるため、除外documentも
`list_documents`には引き続き現れます。exclusion管理は意図的にCLI限定です。詳細は
[ドキュメントの検索除外](./document-exclusions-ja.md)を参照してください。

`add`、`index`、`reindex`、`remove`、`exclusions`、profile編集などのwrite/management toolsは
公開しません。

## Resources

次の read-only resources を公開します。

```text
mrag://kb/info
mrag://profiles
mrag://profiles/{profile}
mrag://documents
mrag://documents/{document_id}
mrag://documents/{document_id}/extracted.txt
mrag://documents/{document_id}/extracted.md
mrag://documents/{document_id}/original
mrag://chunks/{chunk_id}
```

大きな本文は `limits.content_max_chars` に従って切り詰められます。

`mrag://documents` が返すのは 1 ページ目だけです。内容は `list_documents` を引数なしで
呼んだ結果（server の profile、抽出済みの文書、先頭 100 行）と同じです。続きがあるかどうかは
返り値の `page.next_offset` で分かります。2 ページ目以降は `list_documents` tool で取得して
ください。

`.../original` は保存された原本を、追加時に記録された種類で返します。Markdown や
text のファイルは text として、1.0.0 より前に追加された PDF は blob として返します。
text か blob かはこの種類で決まり、バイト列がたまたま decode できるかどうかでは
決まりません。`limits.artifact_max_bytes`（既定 1 MiB）を超える原本は切り詰めずに
拒否します。その場合は project から直接読んでください。

## Streamable HTTP

HTTP endpoint として公開する場合は `transport: streamable-http` を使います。

```yaml
transport: streamable-http
http:
  host: 127.0.0.1
  port: 8001
  path: /mcp
auth:
  bearer_token_env: MRAG_MCP_API_KEY
```

既定 host は `127.0.0.1` です。`0.0.0.0` に bind し、bearer token が未設定の場合は警告を出します。

token を設定している場合、`Authorization` ヘッダが `Bearer <token>` でないリクエストには
`401` と plain text の本文 `Unauthorized` を返します。`http.allowed_origins` が空でない場合、
`Origin` ヘッダがリストにないリクエストには `403` と plain text の本文 `Forbidden origin` を
返します（`Origin` ヘッダのないリクエストは通ります。origin の判定は token の判定より先です）。
どちらの応答も `text/plain` で、JSON-RPC の error ではありません。
