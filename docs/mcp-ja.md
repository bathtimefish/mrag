# MCP server — `mrag mcp`

`mrag mcp` は、1 つの mrag プロジェクトを read-only の Model Context Protocol server として公開します。

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
| `MRAG_MCP_API_KEY` | HTTP bearer token |
| `MRAG_MCP_API_KEY_FILE` | HTTP bearer token を格納したファイル |

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
| `list_documents` | 登録済みドキュメント一覧 |
| `list_profiles` | retrieval profile 一覧 |
| `inspect_document` | ドキュメント単位の index 状態 |
| `inspect_chunks` | chunk metadata 一覧 |
| `inspect_chunk` | 1 chunk の本文/context を確認 |
| `inspect_sections` | heading / parent-child 構造を確認 |

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
mrag://chunks/{chunk_id}
```

大きな本文は `limits.content_max_chars` に従って切り詰められます。

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
