# Dify External Knowledge API 互換エンドポイント — `dify-api`

このドキュメントでは、mrag が提供する **Dify External Knowledge API 互換エンドポイント** について解説します。

mrag は `mrag serve` を起動するとデータ連携用APIサーバが起動します。APIはNative APIとDify用の外部ナレッジAPIがサポートされています。このドキュメントはDify APIについて解説します。Native APIについては[Native APIのドキュメント](./native-api-ja.md)を参照ください。

Dify 側のナレッジ設定画面に **エンドポイント URL と API キー** を登録すれば、Dify のチャットフローや RAG ノードからそのまま mrag のナレッジベースを検索できます。

> 前提：プロジェクトディレクトリの中（`mrag.yaml` がある場所）で `mrag serve` を起動します。ドキュメントを `mrag add` → `mrag index` 済みであることが前提です。


## エンドポイント

Dify 互換のエンドポイントは以下です。

| Method | Path | 役割 |
|---|---|---|
| `POST` | `/retrieval` | Dify から検索リクエストを受け取り、ヒットしたチャンクを返す |

> 注意：`/retrieval` は**ルートパス**に置かれています。mrag のネイティブ REST API は `/api/v1/*` 配下にありますが、Dify 仕様準拠のためこのエンドポイントだけはルート直下にあります。


## mrag 側のセットアップ

```bash
# 1. プロジェクトディレクトリに移動
cd /path/to/my-kb

# 2. （任意）API キーを環境変数で設定
export MRAG_API_KEY="任意の長い秘密文字列"

# 3. サーバー起動
mrag serve --host 0.0.0.0 --port 8000
```

- **`--host`** — 外部から Dify が到達できるアドレスにバインドします。ローカル Dify からの接続なら `127.0.0.1` でも可
- **`--port`** — デフォルトは `8000`
- **`--profile`** — 検索に使うプロファイル。省略時は `mrag.yaml` の `default_profile`
- **`--no-rerank`** — プロファイルで `rerank.enabled: true` でも、このサーバーセッションでは無効化（レイテンシ優先のときに便利）

起動時のコンソールには `Knowledge ID: <id>` が表示されます。この値が Dify からのリクエストの `knowledge_id` と一致しないと 404 になります（後述）。


## リクエスト形式

```http
POST /retrieval HTTP/1.1
Host: your-mrag-host:8000
Authorization: Bearer <MRAG_API_KEY>
Content-Type: application/json
```

```json
{
  "knowledge_id": "kb_device",
  "query": "クエリ",
  "retrieval_setting": {
    "top_k": 5,
    "score_threshold": 0.3
  },
  "metadata_condition": null
}
```

各フィールドの意味：

- **`knowledge_id`** — `mrag.yaml` の `knowledge_base.id`（= プロジェクトの knowledge ID）と一致する必要があります。一致しない場合は 404 + `error_code: 2001`
- **`query`** — 検索クエリ文字列。フィールドそのものが欠落すると 422。空文字は受け付けますが、実用上は検索結果が空になりやすいので呼び出し側で弾くことを推奨します
- **`retrieval_setting.top_k`** — 返す最大件数（`1` 以上 `100` 以下）
- **`retrieval_setting.score_threshold`** — 正規化後スコアの下限（`0.0`〜`1.0`）。**未満のレコードは返却から除外**されます
- **`metadata_condition`** — Dify 仕様上は受け取りますが、**現バージョンでは無視**されます。送っても 200 で返ります


## レスポンス形式

```json
{
  "records": [
    {
      "content": "ヒットしたチャンクの本文",
      "score": 0.823412,
      "title": "manual.pdf",
      "metadata": {
        "chunk_id": "...",
        "document_id": "..."
      }
    }
  ]
}
```

各フィールドの意味：

- **`content`** — チャンクの本文（`parent_child` プロファイルでは**親チャンク**の本文が入ります）
- **`score`** — `[0.0, 1.0]` の範囲に正規化されたスコア（後述）
- **`title`** — ヒットしたチャンクが属するドキュメントのファイル名。ファイル名が解決できない場合は `document_id` の先頭 8 文字
- **`metadata`** — チャンクに紐づく内部メタデータ（`chunk_id` / `document_id` など）


## スコアの正規化

検索戦略によってスコアの素値の性質が違うため、Dify エンドポイントでは `[0.0, 1.0]` に揃えて返します：

| 検索戦略 | 素のスコア | 正規化方法 |
|---|---|---|
| `keyword` | BM25 | `score / (1 + score)` で `(0, 1)` に圧縮 |
| `vector` | コサイン類似度（`[0, 1]`） | そのまま、`[0, 1]` にクランプ |
| `hybrid` | RRF 融合スコア（`[0, 1]`） | そのまま、`[0, 1]` にクランプ |
| `parent_child` | hybrid + 親解決後のスコア | そのまま、`[0, 1]` にクランプ |

> 重要：`retrieval_setting.score_threshold` はこの**正規化後の値**に対する閾値です。`keyword` 戦略では BM25 の `5.0` がおおむね `0.83` に圧縮されるなど、素値とは尺度が異なります。閾値はサーバーを動かしながら実測で調整してください。

> 注意：プロファイルで `rerank.enabled: true` の場合、`score` は CrossEncoder の出力に置き換わります。CrossEncoder のスコアレンジはモデル依存で、必ずしも `[0, 1]` には収まらないため、`vector` / `hybrid` / `parent_child` ではクランプ、`keyword` では `score/(1+score)` 圧縮が追加で掛かります。**リランキングを使う場合は閾値を一段下げる**ことを想定してください（→ [reranking-ja.md](./reranking-ja.md)）。


## 認証

`MRAG_API_KEY` 環境変数で設定します。現時点でAPI認証は簡易な仕組みです。

| 設定 | 動作 |
|---|---|
| 未設定 | 認証なしで全リクエストを受け付ける（ローカル開発向け） |
| 設定済み | `Authorization: Bearer <MRAG_API_KEY>` ヘッダ必須 |

認証失敗時は Dify 仕様準拠のエラーコードを返します：

| 状況 | HTTP | error_code | error_msg |
|---|---|---|---|
| `Authorization` ヘッダ欠落 / 形式不正 | 401 | `1001` | `Invalid Authorization header format.` |
| Bearer トークンが不一致 | 401 | `1002` | `Authorization failed. Please check your API key.` |

> 補足：`/`、`/docs`、`/openapi.json`、`/redoc` の 4 エンドポイントは認証の対象外です（OpenAPI ドキュメントの閲覧と健全性確認のため）。


## エラーコード一覧

| HTTP | error_code | 発生条件 |
|---|---|---|
| 401 | 1001 | `Authorization` ヘッダ欠落・形式不正 |
| 401 | 1002 | Bearer トークン不一致 |
| 404 | 2001 | `knowledge_id` が `mrag.yaml` の値と不一致 |
| 422 | — | `knowledge_id` または `query` の欠落、`top_k` が範囲外、JSON 構造不正 |
| 500 | — | Qdrant / 内部リソース不到達などのサーバー内部例外 |

`error_code: 1001 / 1002 / 2001` のエラーは `{"error_code": ..., "error_msg": ...}` 形式で返します。422 / 500 は FastAPI のデフォルト形式（`{"detail": ...}`）です。


## Dify 側の設定

Dify で **「外部ナレッジ（External Knowledge）」** として mrag を登録する流れの要点だけ示します（UI の詳細は Dify 公式ドキュメントを参照）。

1. Dify Studio の「ナレッジ」セクションから「外部ナレッジを追加」を選ぶ
2. **エンドポイント URL** に `http(s)://<mrag-host>:<port>/retrieval` を入力
3. **API キー** に `MRAG_API_KEY` と同じ値を入力（mrag 側で未設定なら空欄で可）
4. **Knowledge ID** に `mrag.yaml` の `knowledge_base.id` を入力（`mrag init` 直後にコンソール表示される値）

接続テストが通ったら、Dify のチャットフロー側で「ナレッジ取得（Knowledge Retrieval）」ノードからこのナレッジを参照できます。


## Tips

- **`score_threshold` の決め方**：まず `0.0` で動かし、Dify 側のナレッジ取得結果を見ながら 0.05 刻みで上げていくのが安全です。`keyword` 戦略は素の BM25 が大きく変動するので、閾値の妥当な範囲もデータセット依存です
- **デバッグ時は curl で叩く**：`curl -X POST http://localhost:8000/retrieval -H 'Authorization: Bearer ...' -H 'Content-Type: application/json' -d '{...}'` で動作を確認すると、Dify Studio 側の設定ミスと mrag 側の問題を切り分けやすいです
- **本番運用時のレイテンシ**：リランキング有効時は CrossEncoder の推論コストが乗ります。Dify のタイムアウト設定とあわせて、`rerank.top_n` を絞るか `--no-rerank` でサーバーを起動する選択肢を検討してください
