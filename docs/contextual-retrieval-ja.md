# コンテキスチュアル・リトリーバル

このドキュメントでは、mrag のコンテキスチュアル拡張（contextual augmentation）について解説します。

**コンテキスチュアル・リトリーバル**は、Anthropic が提案した「チャンク単位でドキュメント全体における位置づけ（context）を LLM に生成させ、そのテキストを元のチャンク本文に前置してから埋め込む」レシピです。これによりベクター検索の精度が改善することが期待できます。

mrag では、プロファイルの `augmentation.strategy: contextual` を有効にするとこの augmentation が `mrag index` 実行時に適用されます。


## 処理の概要

通常のインデックスでは、チャンクの本文がそのまま Embedding Model に渡されます。contextual augmentation を有効にすると、`mrag index` の途中で次のようなフローになります：

1. 各チャンクごとに、ドキュメント全体（の冒頭）とそのチャンクを LLM に渡す
2. LLM は「このチャンクがドキュメント中でどんな位置づけにあるか」を短いテキストで返す
3. 生成されたコンテキスト文をチャンク本文の頭に付けて Embedding Model に渡す
4. 上記の処理によりベクター検索のヒット精度が、コンテキスト情報分だけ向上する

> 重要：**FTS5 キーワード検索のインデックスは常に元のチャンク本文（コンテキスト前置なし）を使います**。コンテキスチュアル拡張で変わるのはベクター側だけで、キーワード検索の結果は影響を受けません。


## 設定方法

`profiles/<profile-name>.yaml` で `augmentation` セクションを次のように設定します。

```yaml
augmentation:
  strategy: contextual           # none（デフォルト） | contextual
  provider: ollama
  model: gemma4:e2b              # 生成用 LLM。Embedding Model とは別に指定
  endpoint: http://localhost:11434
  retry:                         # 任意。以下は既定値
    max_attempts: 3
    initial_delay_seconds: 2.0
    backoff_multiplier: 2.0
    max_delay_seconds: 30.0
  failure_policy:                # 任意。リトライ後も失敗した場合の挙動
    mode: raw_fallback           # raw_fallback（デフォルト） | fail_document
```

各フィールドの意味：

- **`strategy`** — `none` ならコンテキスチュアル拡張なし（高速）。`contextual` で有効化
- **`model`** — コンテキスト生成用の LLM。検索用の `embedding.model` とは別物
- **`endpoint`** — コンテキスト生成用LLM APIエンドポイントの URL（デフォルト：`http://localhost:11434`）
- **`retry`** — コンテキスト生成失敗時（タイムアウトなど）に対するリトライ設定
- **`failure_policy.mode`** — リトライしきっても失敗したチャンクの扱い（後述）

> `augmentation.strategy` を変更するとプロファイルハッシュが変わり、次回 `mrag index` 実行時に**そのプロファイルのインデックスが全件再構築**されます。`retry` と `failure_policy` の変更はハッシュ対象外で、再インデックスは不要です。


## コンテキストプロンプト（`context_prompt.txt`）

LLM に渡すプロンプトは `profiles/context_prompt.txt` というテキストファイルとして外出しされており、必要に応じてチューニングできます。`mrag init` で次のようなデフォルトテンプレートが書き出されます：

```
<document>
{document}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Answer only with the succinct context and nothing else. Always respond in the same language as the document and chunk. Prefer specific technical terms, parameter names, identifiers, and concrete values over general category descriptions. Do not begin your response with self-referential phrases like "This chunk" or "This section".
```

- `{document}` と `{chunk}` の 2 つの placeholder は必須です（削除すると LLM 呼び出し時にエラーになります）
- ドキュメント本文は冒頭 8000 文字までが `{document}` に展開されます
- 編集後は次回 `mrag index` 実行時から新プロンプトが使われます

> `context_prompt.txt` は `profile_hash` の対象外です。プロンプトを書き換えても自動再インデックスは走りません。**既存チャンクを新プロンプトで再生成したい場合は `mrag reindex` を明示的に実行してください**。

### ドメイン特化のチューニング例

たとえば日本語の技術ドキュメントを扱う場合、デフォルトプロンプトのままでもおおむね日本語で返ってきますが、混在を避けたいなら明示的に指示するのが確実です：

```
... Always respond in Japanese. Prefer specific module names, part numbers, communication protocol names, and concrete parameter values over general category descriptions. ...
```

ナレッジベースのドメインに応じて「型番」「プロトコル名」「コマンド名」など、ナレッジベースで重要な語彙にバイアスをかけると、コンテキスト文の有用性が上がる可能性があります。


## 失敗時の挙動 — `augmentation.failure_policy`

LLM 呼び出しは処理負荷が高く長時間になることがあります。そのため処理の不安定さに起因するタイムアウトなどの一時的な失敗が起きえます。mrag はチャンク単位でリトライ（指数バックオフ）をサポートしています。さらにリトライ数を超えて失敗した場合の挙動を `augmentation.failure_policy.mode` で切り替えられます。

| モード | 挙動 |
|--------|------|
| `raw_fallback`（デフォルト） | そのチャンクだけ **raw variant**（コンテキスト前置なし）で保存する |
| `fail_document` | そのチャンクの失敗をドキュメント全体のエラーとして扱う |


> 注意：`raw_fallback` モードは「壊れたチャンクで全体を止めない」ためのセーフガードです。フォールバックが多発する場合は元のドキュメント品質（OCR ノイズ・繰り返しテキストなど）か、retry 設定の調整を検討してください。


## 失敗時の挙動 — `embedding.failure_policy`（v0.21.0+）

Embedding（埋め込み）処理でも同種の **チャンク粒度フォールバック** が用意されています。コンテキスト拡張後のテキストを Ollama などの Embedding プロバイダに渡すと、まれに特定の入力（例：`bge-m3` の NaN 返却問題）でバッチ全体が HTTP 500 を返すケースがあります。v0.21.0 ではこのケースを **バイセクションで失敗チャンクを特定し、そのチャンクだけ vector を持たない状態で保存**する挙動になりました。

```yaml
embedding:
  model: bge-m3
  failure_policy:
    mode: fallback_no_vector   # デフォルト
    # mode: fail_document      # v0.20.0 までと同じ挙動（ドキュメント全体失敗）
```

| モード | 挙動 |
|--------|------|
| `fallback_no_vector`（デフォルト） | 失敗チャンクは `qdrant_point_id=NULL` で保存。**vector 検索ではヒットしないが、FTS5 keyword 検索ではヒットする** |
| `fail_document` | そのチャンクの失敗をドキュメント全体のエラーとして扱う（v0.20.0 互換） |

> 重要：fallback されたチャンクは `chunk_variants.metadata_json` に `{"embedding_status": "fallback_no_vector", "embedding_error": "..."}` が記録されます。`mrag inspect document` の **Embedding Status** セクションや `mrag inspect chunks --json` の `variant.embedding_status` フィールドで件数・該当チャンクを確認できます。

> 注意：`mrag reindex` を実行すると fallback チャンクも再度 embedding が試行されます。Ollama 側のバグが修正された、あるいはモデルを変更したタイミングで再インデックスすれば、自然に `qdrant_point_id` が埋まり vector 検索でもヒットするようになります。


## インデックスログの読み方

`mrag index` 実行中、ログには以下のような行が混じります：

- `↻ retry` — LLM 呼び出しに失敗してリトライしている（情報。回復すれば成功扱い）
- `⤵ fallback` — リトライしきっても失敗したチャンクで raw に切り替えた（要監視）
- `⚠ large document` — 300 チャンク以上のドキュメントで拡張処理を開始するときに出る（情報。長時間処理の予告）
- `Embedding fallback for chunk (input prefix: ...) — error: ...` — Embedding がチャンク単位で失敗（v0.21.0+。Ollama / モデル側のバグ報告に引用可能な先頭 200 文字を含む）
- `(N augmentation fallback)` / `(M embedding fallback)` — ドキュメント単位の集計行に出るフォールバック件数（両方発生時は併記）

ログ末尾は通常の `✓ Indexed: ...` で締めくくられます。


## 実運用での所感

参考までに、Apple Silicon Mac 環境でローカルの Ollama に `gemma4:e2b` を使った場合の体感値：

- 1 チャンクあたりおよそ **数十秒**（モデル・本文量に依存）
- 500 チャンクを超えるようなドキュメントでは VRAM 逼迫起因の一時失敗が起きやすく、`initial_delay_seconds` を上げる（例：2 → 5）と回復しやすくなる
- ベクター検索の品質向上は得られるものの、インデックス時間が**通常の数倍〜数十倍**になりうるので、ドキュメント規模と用途を見て採否を決める

軽い動作確認や検索品質の評価が目的なら、まずは `gemma3:2b` などの**小型モデル**で速度と品質のトレードオフを見るのも有効です。


## Tips

- 検索戦略との関係：コンテキスチュアル拡張は `vector` および `hybrid` のベクター段の品質に効きます。`keyword` 単独や `parent_child` のキーワード段は変わりません（→ [retrieval-strategies-ja.md](./retrieval-strategies-ja.md)）
- チャンキング戦略との関係：拡張処理はチャンキング後に各チャンクへ適用されるため、どのチャンキング戦略とも組み合わせられます（→ [chunking-strategies-ja.md](./chunking-strategies-ja.md)）
- `parent_child` プロファイルでは `augmentation.strategy: none` を推奨します。親チャンクで広い文脈を返すため、コンテキスト前置の効果が薄いケースが多いためです。

