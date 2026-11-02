[English](CHANGELOG.md) / 日本語

# 変更履歴

mragの主要な変更点を記録します。0.24.0より前のエントリは記録していません。
それらのリリースについてはリポジトリの履歴が記録となります。

---

## 1.6.2 — 2026-10-27

### 変更

- **sqlite-vaporetto は、Python 標準の `sqlite3` が拡張を読める環境ではその上で、
  読めない環境でだけ apsw の上で動くようになりました。** Vaporetto の project では、
  1 つの process が使う SQLite は検索も index の書き込みも 1 つになります。1 つの
  process で 2 つの SQLite が同じ database を開いてはいけません。macOS と Linux の
  lock は process 単位なので、片方の SQLite の接続が閉じると、その SQLite は自分が
  最後の接続だと判断して checkpoint し、もう片方の SQLite がまだ開いている接続の
  下で `-wal` と `-shm` を消します。その接続は以後 commit を見なくなります。uv の
  Python は拡張を読めます。`mrag doctor` は拡張がどちらの SQLite で動くかを表示
  します。`mrag init` が Vaporetto を選ぶのは今までどおり apsw がある場合だけです
  （`vaporetto` extra）。`sqlite3` が拡張を読める環境なら、Vaporetto の project は
  apsw なしでも開けるようになりました。index の作り直しは要りません。

### 修正

- **`sqlite3` が拡張を動かす環境では、`mrag serve` の Vaporetto の keyword 検索が、
  要求ごとに分かち書きの model を作り直さなくなりました。** 拡張は model（測った
  機械で 2〜2.6 秒）を、交わらない 2 つの cache に分けて読み込みます。FTS5 の
  tokenizer は接続ごとに、`vaporetto_split` は thread ごとに 1 つ持ちます。
  `keyword_search` は呼び出しごとに、呼んだ thread で接続を開いていたので、server は
  keyword の要求ごとに少なくとも前者を払っていました（ある機械で 1 回 2.1 秒、要求の
  thread が替わる別の機械で 5.2 秒）。Vaporetto の検索は process の寿命のあいだ
  保持する 1 本の接続・1 本の thread を通るようになりました。1 文書の project で
  測ると、最初の keyword の要求が 4.2 秒、その後はすべて 4 ms（hybrid は 40〜50 ms）
  で、server の稼働中に別の `mrag index` が足した文書も次の検索で見つかりました。
  apsw でしか拡張を読めない環境では、検索は今までどおり接続を毎回開きます（保持すると
  上の 2 つ目の SQLite になるため）。ただし同じ 1 本の thread で動くので、要求の
  thread が替わっても thread 側の model は作り直しません。trigram の project と、
  コマンドラインの `mrag search` は変わりません（process ごとに 1 回払うのは同じで、
  これを下げられるのは拡張だけです）。
- **`mrag doctor` が apsw の install の command を欠けずに表示するようになりました。**
  Rich が `[vaporetto]` を markup と解釈し、案内が `uv pip install -e '.'`（apsw が
  入らない）になっていました。各 check の文面は書いたとおりに表示します。

---

## 1.6.1 — 2026-10-25

### 修正

- **Qdrant server での vector 検索が、取りこぼしていた近い点に届くように
  なりました。** 検索は Qdrant の既定の HNSW の探索幅(graph の
  `ef_construct`、100)で送られていました。1 枚の graph にまとまった 3 万点の
  corpus で測ると、この探索幅は厳密検索の top 10 の 8% を違えて
  返し、探索幅 1600 からは厳密検索の top 10 と完全に一致しました。10 万点でも
  増える時間は数 ms です。すべての vector 検索が探索幅 1600 を指定します。既定の
  local mode は全 vector を総当たりするので探索幅を使わず、結果は変わりません。
  総当たりされる小さい server の segment も同じです。index の作り直しは要りません。

---

## 1.6.0 — 2026-10-24

### 追加

- **埋め込みモデルの task prefix。** 省略可能な 2 つの profile 設定
  `embedding.query_prefix` と `embedding.document_prefix` で、モデルが埋め込む
  テキストの前に決まった文字列を置けるようになりました。query prefix は検索
  クエリの前に置きます(`vector`、`hybrid` と `parent_child` の vector 側、
  `mrag eval`)。keyword 側は書かれたままのクエリで検索し、保存済みの vector は
  この設定に依存しないので、変えても何も作り直しません。document prefix は
  index が埋め込むすべてのチャンクの前に(contextual の context よりさらに前に)
  置き、中の `{title}` は拡張子を除いたファイル名(無ければ `none`)になります。
  設定すると index identity に含まれます。どちらも 1〜256 文字で、`{title}` は
  document prefix の中にだけ 1 回まで書けます。保存される variant、FTS5 の
  index、検索結果にはどちらも入りません。未設定なら(既存の profile はすべて
  そうです)テキストは書かれたまま埋め込まれ、index identity も変わりません。
  [task prefix](docs/retrieval-strategies-ja.md#task-prefix) を参照してください。
- **`mrag doctor` が Ollama の版を表示し**、EmbeddingGemma 2 を動かせる最初の版
  である 0.40.4 より古いと警告します。

### 変更

- **新しいプロジェクトは EmbeddingGemma 2 で埋め込みます。** `mrag init` は
  `embedding.model: embeddinggemma-2:740m-q8_0` と、モデルが学習時に使った
  `query_prefix: "task: search result | query: "` を書き込みます。prefix を
  付けずに測った 2 つのコーパスでは、どちらも `bge-m3` を下回りました。この tag は
  どの OS でも GGUF の `Q8_0` 版です(`latest` は Apple Silicon では vector の
  異なる MLX 版に解決されます)。prefix 付きで日本語の文書を測ったところ、1 つの
  コーパスで `bge-m3` と同等、別のコーパスで上回り、人が判定した質問では
  下回りました。埋め込みにかかる時間は Apple Silicon の Mac で 4 分の 1 から
  3 分の 1 短く、NVIDIA RTX 4090 では 7% 長くなりました。Ollama 0.40.4 以降と
  `ollama pull embeddinggemma-2:740m-q8_0` が必要です。model を書いていない
  profile は引き続き `bge-m3` を意味します。

### 修正

- **名前が変わった document を次の index が作り直すようになりました。**
  `mrag documents sync` が document を新しいパスへ移した後(または
  `mrag documents rebind` が別のファイルを指させた後)、document の一覧は
  それを `stale` と報告するのに、`mrag index` と `documents sync --index` は
  最新と判定して飛ばしていました。そのため `mrag reindex` するまで `stale` の
  ままでした。index の計画が一覧と同じ規則を使い、別の名前で index された
  document を作り直します。名前を記録する前に index された行は、一覧と同じく
  判定しません。
- **Ollama に無い埋め込みモデルを traceback ではなくエラーとして報告します。**
  profile の model に Ollama が HTTP 404 を返すと、`mrag index` と
  `mrag reindex` は traceback で終わり、`mrag search` は 404 をそのまま表示して
  いました。エラーは endpoint と model を示し、`Pull it with: ollama pull <model>`
  と案内し(EmbeddingGemma 2 なら Ollama 0.40.4 以降が要ることも添えます)、
  コマンドは exit 1 で終わります。
- **API サーバーと `mrag eval` が `mrag search` と同じ形でクエリを埋め込みます。**
  どちらも埋め込みの client を自前で作り、`embedding.max_input_tokens` を渡して
  いませんでした。そのためサーバーの既定 profile と `mrag eval` の要求には
  `num_ctx`/`num_batch` が無く、`mrag search`・MCP・index の要求にはありました。
  Ollama の既定の batch より長いクエリは経路によって扱いが変わることがあり、
  2 種類の要求が交互に来るたびに Ollama はモデルを読み直していました。どの経路も
  profile の設定を送るようになりました。

### 互換性

- 既存のプロジェクトには影響しません。profile が model を書いており、prefix を
  設定しておらず、index identity も変わりません。既存のプロジェクトを
  EmbeddingGemma 2 に移すには上の 2 行を設定します(index は作り直しになります)。
  新しいプロジェクトで `bge-m3` を使うには `model: bge-m3` にして
  `query_prefix` を消します(`bge-m3` は prefix を取りません)。
- 1.3.0〜1.5.6 で名前を変えた document があるプロジェクトでは、次の
  `mrag index` でその document が作り直されます。

---

## 1.5.6 — 2026-10-21

### 修正

- **mrag が実装していない provider を、profile を読む時点で拒否するように
  なりました。** `embedding.provider`・`augmentation.provider`・
  `keyword.provider` は、どこからも参照されない自由な文字列でした。
  `provider: lm_studio` と書いた profile も読み込まれ、mrag はその endpoint に
  別の名前のまま Ollama として要求を送っていました — index の identity だけは
  その文字列で変わっていました。それぞれ mrag が持つ実装(`ollama`・`ollama`・
  `sqlite_fts5`)だけを受け付け、ほかの値ではコマンドが、欄の名前と受け付ける
  値を示す `Error:`(`embedding.provider` / `Input should be 'ollama'`)で止まります。
  実装済みの provider を書いた profile や、書いていない profile は従来どおり
  読み込まれ、index の identity も変わりません。`rerank.provider` は以前から
  使用時に拒否されていました。
- **`mrag search` と `mrag profiles show` が、検証に通らない profile を
  エラーとして報告するようになりました。** どちらも profile が無い場合しか
  捕まえていなかったため、無効な profile — `retrieval.fusion: harmonic_mean`
  のように欄が受け付けない値 — では traceback で終わっていました。
  `mrag index` や `mrag serve` と同じく、理由を `Error:` として表示し exit 1 で
  終わります。

---

## 1.5.5 — 2026-10-21

### 修正

- **Ollama 0.40.6 で、窓より長いとして拒否された prompt を認識するように
  なりました。** `augmentation.context_window_tokens` を設定すると、Ollama 0.34.4
  はそのような prompt を `exceed_context_size_error` と token 数を含む HTTP 400 で
  拒否していました。0.40.6 は `request (N tokens) exceeds the available context
  size (W tokens), try increasing it` という文だけを返します。この拒否はただの
  HTTP 400 として扱われていたため、`raw_fallback` のチャンクは
  `N-token prompt exceeds augmentation.context_window_tokens (W)` ではなく
  サーバーの応答本文を理由として fallback し、`fail_document` も同じ一般的な
  メッセージで失敗していました。この文も認識し、token 数の欄が無いときは文から
  読むようにしました。チャンクを文脈なしで残す動作は変わらず、変わるのは理由だけ
  です。

---

## 1.5.4 — 2026-10-20

### 修正

- **Dify エンドポイントで、リランクしたレコードの score が検索戦略によらず
  リランカーの score になりました。** リランキングを有効にすると、結果の score は
  CrossEncoder の出力に置き換わります。出力が 1 つのリランカーでは、
  sentence-transformers がこれを既に `[0, 1]` に変換しています。ところが `keyword`
  戦略は、これを BM25 とみなして `score / (1 + score)` でさらに圧縮していたため、
  リランクしたレコードはすべて `0.5` 未満の score になり — リランカーが `0.74` と
  評価したレコードが `0.42` と表示され — `0.5` 以上の閾値ではすべて除外されて
  いました。リランク後の score は検索戦略によらず `[0, 1]` にクランプしてそのまま
  返すようにしました。もともとクランプだけだった `vector`・`hybrid`・`parent_child`
  の値は変わりません。`docs/dify-api-ja.md` から、リランキング時に閾値を下げるよう
  勧める記述を外しました。

---

## 1.5.3 — 2026-10-20

### 修正

- **Dify エンドポイントが `score_threshold` を、レコードに表示される score と
  比べるようになりました。** これまでは、レコード用に小数第 6 位へ丸める前の
  正規化 score と比べていたため、`score` が閾値と同じに見えるレコードが除外される
  ことがありました — `0.4999996` は `0.5` と表示されながら、閾値 `0.5` を下回って
  いました。丸めた score で比べるようにしたので、閾値と同じに見えるレコードは残り、
  閾値より小さく見えるレコードは除外されます。score の値そのものは変わりません。

### ドキュメント

- **`docs/dify-api-ja.md` に、`keyword` 戦略でありふれた語の score がほぼ 0 になる
  理由を書きました。** SQLite の BM25 は、index された chunk の半数以上に現れる語に
  最小の重みしか与えないため、そうした検索のレコードは `0.000001` 前後の score に
  なり、`0` より大きい閾値ではすべて除外されます。並び順は保たれます。

---

## 1.5.2 — 2026-10-19

### 修正

- **止められた `mrag catalog migrate-identities` が、他のエラー出力と同じく
  `error` を返すようになりました。** 変換できない文書があるとき、レポートは
  `status: "error"` と妨げている文書の一覧を返しますが、`error` オブジェクトを
  持たなかったため、`status` が `"error"` のときに `error.code` を読む呼び出し側は
  何も得られませんでした。dry run と本番のどちらのレポートにも、
  `error: {code: "identity_migration_blocked", message}` が載るようになりました。
  それ以外は変わりません。`action` は `refused` のまま、妨げている文書は従来どおり
  列挙され、何も書き込まれず、終了コードは 1 のままです。
- **`mrag catalog migrate-identities` のエラー出力が、コマンド名を正しく示すように
  なりました。** プロジェクトの外で実行したときなどに `command: "catalog"` と
  示していましたが、同じコマンドのレポートはすべて `"catalog migrate-identities"`
  です。これに揃えました。

---

## 1.5.1 — 2026-10-11

### 修正

- **`augmentation.context_window_tokens` を超える context prompt が、黙って
  切られなくなりました。** Ollama はそうした prompt の末尾を残して HTTP 200 を
  返すため、prompt の冒頭である文書の抜粋が失われ、context はチャンクだけから
  書かれていましたが、実行結果は成功として数えられていました。窓を設定した
  プロファイルは `/api/generate` に `truncate: false` を送るようになり、
  サーバーは prompt を拒否します(Ollama 0.34.4 では HTTP 400
  `exceed_context_size_error`、トークン化のみで返ります)。そのチャンクは
  `augmentation.failure_policy` に従い、既定では context なしで保存されて
  augmentation fallback として数えられ、その理由に prompt のトークン数が
  示されます。この拒否では抜粋を短くしません。新規プロジェクトの既定値
  (`chunk_size` 800、窓 16,384)では、日本語の業務文書 9,688 チャンクで測った
  最長の prompt は 6,957 トークンで、既定のプロジェクトは影響を受けません。
  窓を下げた場合、`chunk_size` を大きくした場合、`chunk_size` より長い表の行
  (表は行の途中では切られません)がある場合は超えることがあります。窓を設定
  していないプロファイルは従来と同じ要求を送り、索引識別子は変わりません。

- **Windows で、文書の抽出テキストが `extracted_hash` の示すとおりのバイト列で
  保存されるようになりました。** `extracted.md` と `extracted.txt` は OS の改行で
  書かれていたため、Windows では各行が CRLF で終わる一方、`extracted_hash` は LF
  のテキストから計算されていました。mrag 自身は同じテキストとして読み戻して正しく
  インデックスしていましたが、保存された成果物が自身の記録と食い違っていたため、
  成果物を hash と照合する読み手には、そうした文書がすべて破損に見えていました。
  今後はどの OS でも LF で書きます。すでに Windows で書かれたプロジェクトは CRLF
  のファイルを持ったままですが、対応は不要です。mrag はこれまでどおり読み、CRLF を
  LF として読めばテキストは記録と一致します。

- **Windows で、`mrag add --recursive` と `mrag documents sync` がディレクトリ
  ツリー全体をたどるようになりました。** Windows はディレクトリ項目の inode を
  返さないため、どのディレクトリも同じものに見えていました。最初のディレクトリ
  より後のディレクトリは「訪問済み」として何も告げずに読み飛ばされ、入れ子の
  ディレクトリはシンボリックリンクの循環(`directory_symlink_cycle`。プロジェクト
  ルートを追加したときの `cache/embeddings` など)として報告されていました。
  兄弟と入れ子のディレクトリに 7 ファイルを置いたツリーで、1.5.0 は 2 件だけを
  追加して exit 0 で終わっていました。今後はどの OS でも完全なメタデータで
  ディレクトリを見分けます。**1.5.0 以前の Windows で再帰追加や sync を実行した
  場合は、もう一度実行してください**。読み飛ばされていたものが追加され、登録済み
  のものはスキップされます。
- **成果物のパスがどの OS でも `/` で記録されるようになりました。** Windows では
  カタログに `data\documents\<id>\…` が記録され、検索結果・`show-extracted`・API
  がそのまま返し、他の OS では開けませんでした。すでにそう書かれたカタログは
  対応不要です。どちらの区切りでも読み、`/` で返します。
- テストスイートが Windows でも通るようになりました。SIGTERM で実行を止める
  2 件のテストは、`os.kill`(Windows ではプロセスを終了させる)ではなく
  シグナルハンドラを通して送るようにし、識別子の fixture のルートキー
  (POSIX のパス)は POSIX でだけ検査します。

## 1.5.0 — 2026-10-09

### 変更

- **keyword 検索が、空白のない日本語の質問を理解するようになりました。** vaporetto
  トークナイザーでは、クエリをチャンクの索引と同じトークナイザーで分かち書きし、
  機能語(助詞・助動詞・疑問詞)を除いて、残った語を AND で結びます。これまでは
  クエリの空白区切りの各語が 1 つの FTS5 フレーズだったため、
  `熱電対モジュールの測定範囲` はその 6 形態素がこの順に並んだチャンクにしか当たらず、
  実際の仕様書 12 本に対する質問はすべて 0 件で、`hybrid` は vector 側だけで答えて
  いました。機能語だけの質問は、その文法を検索する代わりに keyword 0 件を返します。
  クエリ内の空白は従来どおり語を区切り、FTS5 の演算子は無効化されたままです。
  trigram と porter は変わりません。

## 1.4.3 — 2026-10-09

### 修正

- **`mrag catalog migrate-identities` が報告を表示するようになりました。** `--json`
  なしでは、dry run も実行も、変換そのものは済んだ後に
  `TypeError: echo() got an unexpected keyword argument 'markup'` で止まり、要約と
  締めの案内が表示されませんでした(1.2.0 以降)。`--json` 付きの実行には影響ありません。

## 1.4.2 — 2026-10-09

### 修正

- **vaporetto が入った環境で初期化した project を index できるようになりました。**
  1.3.0 以降、`sqlite-vaporetto` のある環境で `mrag init` を実行すると catalog が
  `document_indexes` table なしで作られ、最初の `mrag index` が
  `no such table: document_indexes` で止まっていました。vaporetto の経路は schema を
  すべてのセミコロンで分割して適用しており — コメント内の 2 か所も含めて — その結果の
  エラーを捨てていたため、`embedding_cache` も(最初のリリースから)欠けていました。
  schema は SQLite が解釈するとおりに実行するようにし、エラーが出れば見過ごさずに
  `mrag init` を止めます。
- **そのように作られた project は自分で修復します。** catalog を開いたとき —
  `mrag index` を含め、catalog を読むどのコマンドでも — schema が定義していて
  catalog に無い table を、その index とともに作ります。既にあるものは変えず、
  文書を追加し直す必要もありません。

## 1.4.1 — 2026-10-04

### 修正

- **空の token ファイルで HTTP の認証が外れなくなりました。**
  `MRAG_MCP_API_KEY_FILE` が空のファイルを指していると token が空文字に解決され、
  そこで解決が終わっていたため、並べて設定した `MRAG_MCP_API_KEY` が無視され、
  `mrag mcp` は streamable HTTP を認証なしで提供していました。空のファイルは token を
  供給しないものとして扱い、判断は環境変数に委ねます(空でエクスポートされた変数と
  同じ扱いです)。
- **空白だけの token ファイルを拒否するようにしました。** token を書くつもりの
  ファイルなので、HTTP server と `mrag mcp validate` は、認証なしで動く代わりに、
  変数名を示すエラーで止まります。何も認証しない stdio では無視します。

## 1.4.0 — 2026-10-03

### 追加

- **検索結果が `reference` を持つ。** `mrag search --json`、
  `POST /api/v1/retrieve`（と `/api/v1/search`）、MCP の `search` ツールの各結果が、
  本文の出どころを示す同じオブジェクトを持ちます。document の `display_name` と
  `source_binding_status`（document 一覧と全く同じ読み方）、source が project 内に
  ある場合の `source_path`、保存された `original` と `extracted_markdown`（project
  root からの相対パス）、原本を返す MCP の `resource`、`location.source_format`。
  document は抽出物を 1 つだけ持ち、chunk は文字範囲を記録しないため、
  `revision_id`・`location.source_start`・`location.source_end` は `null` です。
  全フィールドが常に存在します。人向けの `mrag search` 出力は各結果に
  `source: … original: …` の行を加えます。
  [docs/native-api-ja.md](docs/native-api-ja.md) を参照してください。
- **MCP リソース `mrag://documents/{document_id}/original`** は保存された原本を、
  追加時に記録された種類で返します — Markdown と text は text として、1.0.0 より前
  に追加された PDF は blob として。新設の `limits.artifact_max_bytes`（既定 1 MiB）を
  超えるものは切り詰めずに拒否します。

### 変更

- **sync の計画器が理由 `converter_differs` を扱う:** バイト列は保存済み
  revision と同じで、今回の実行が別の converter か別のオプションで読むファイルは、
  `content_changed` / `conversion_not_requested` ではなくこの理由で `update` または
  `blocked` になります。mrag は source を受け取ったまま保存するので、mrag 自身の
  sync がこれを報告することはありません。計画器を検証する判定表が、変換を伴う
  source も記述しているためです。

### 互換性

追加のみです。既存のフィールドは変わらず、Dify の `/retrieval` endpoint にも影響
しません。reference が指す抽出物はその document の現在の抽出物です。source
ファイルを更新してから次の `mrag index` までの間は結果の本文を含まないことがあり、
その間 document 一覧はその document を `stale` と報告します。

---

## 1.3.0 — 2026-10-02

### 追加

- **`mrag documents sync <DIR> [--apply] [--index [--profile NAME]...] [--json]`** は、
  catalog がディレクトリ配下にあると記録している document とディレクトリの現在の中身を
  比較し、差分を計画として示します: `add`、`update`、`move`、`exclude`、`restore`、`noop`、
  `duplicate`、`adopt_candidate`。`--apply` で計画を item ごとに 1 transaction ずつ実行し、
  `logs/` に監査ログを残し、`--index` で index 済み profile をその後に再構築します。改名した
  ファイルは document ID を保ち、消えたファイルは戻るまで全 profile から除外され、同じ移動の
  候補が複数あるときは推測で対応付けません。mrag が取り込まない形式のファイルは失敗ではなく
  `unsupported` として数えます。選択オプションは `mrag add --recursive` と同じです。
  [docs/document-sync-ja.md](docs/document-sync-ja.md) を参照してください。
- **`mrag documents rebind <DOCUMENT_ID> <FILE> [--apply] [--json]`** は、sync が手を付けない
  場合 — 移動かつ変更されたファイル、移行された document の候補、自分で解決できる曖昧な
  移動 — に、1 つの document を ID を保ったまま 1 つのファイルへ手で結び付けます。別の
  document が持つパスや内容は拒否します。
- **除外が出所を記録します。** `document_exclusions.origin` は `mrag exclusions add` で書いた
  ルールでは `user`、sync が書いたものでは `sync` で、`mrag exclusions list --json` に表示
  されます。sync が解除するのは自分のルールだけです。
- **登録された source root が祖先を記録します。** `source_root_ancestors` は `mrag add` が
  登録した root の上位ディレクトリすべての key を保持し、親ディレクトリの sync がその root の
  document を配置できるようにします。本リリース以前に登録された root は、`add`、sync、
  rebind が最初に見つけたときに祖先を得ます。
- **バッチ系コマンドが境界で止まります。** `mrag add --recursive` と適用中の
  `mrag documents sync` は `SIGINT` または `SIGTERM` を受け取ると次のファイルまたは item で
  止まり、到達しなかったものを `cancelled`（`status: "cancelled"`、`summary.cancelled`）と
  して報告し、ログを書いて exit 130 で終了します。2 回目のシグナルは即座にプロセスを終了
  します。従来は `timeout`、cron、service wrapper からの `SIGTERM` で report なしに終了して
  いました。

### 変更

- **文書一覧の `name_changed` を記録済みの名前から判定します。**
  `document_indexes.indexed_display_name` は profile が index した時点の表示名を記録し、
  現在の名前が異なる document は `reason: name_changed` の `stale` になります。従来は
  index 時の名前の記録がなく、この freshness は報告されませんでした。
- 本リリースが最初に開いたときに、catalog は `source_root_ancestors` テーブルと
  `document_exclusions.origin`、`document_indexes.indexed_display_name` の列を得ます。
  document ID、identity、index は変わりません。

## 1.2.0 — 2026-09-27

**互換性のない変更:** Native API の文書一覧と MCP の `list_documents` は新しい
envelope を返し、既定では抽出済みの文書だけを返します。「変更」を参照してください。

### 修正

- **project に `external/` や `legacy/` というディレクトリがあると文書一覧が壊れた。**
  1.1.0 は project 外のソースを `external/<key>/<path>`、移行した行を `legacy/v1/<id>`
  と名付け、project 内のファイルを素のパスで名付けていたため、両者を区別できません
  でした。project 内の `external/notes.md` で Native API と MCP の文書一覧全体が失敗し、
  `external/sub/notes.md` は `notes.md` という外部ファイルとして表示され、
  `legacy/v1/<既存ID>` にある project 内ファイルはその移行文書とみなされて新しい版として
  取り込まれました。project のパスではない identity を予約済みの名前空間へ移しました
  (identity scheme 2): `identities/external/<key>/<path>` と `identities/legacy/v1/<id>`。

### 追加

- **`mrag catalog migrate-identities [--dry-run] [--json]`** は catalog に保存された
  identity を scheme 2 へ変換します。先に計画を示し、変換できない文書をすべて列挙して
  それが残る間は何も変更せず、1 つの transaction で書き換え、`logs/` に監査ログを
  残します。document ID は変わらず、再 index もしません。
- **`identities/`** を予約しました。`mrag init` がその旨の `identities/README.md` を
  作ります。`mrag add` はその中のファイルを拒否し(`source_identity_reserved_path`、
  symlink 経由でも同じ)、再帰的な追加ではこのディレクトリを読み飛ばし、root に
  指定すると拒否します。ディレクトリが無い場合は書き込みを伴う次の `add` が作り直します。

### 変更

- **文書一覧を 1 つの contract にまとめました**（Native API の
  `GET /api/v1/documents` と MCP の `list_documents` は同じものを返します）。
  レスポンスは配列ではなく envelope — `schema_version`、`status`、`profile`、
  `filter`、`total`（status で絞り込む前の表示件数）、`returned`（絞り込み後）、
  `page {limit, offset, count, next_offset}`、`documents` — になりました。
  `all=true` を指定しない限り抽出済みの文書だけを返します。パラメータは
  `profile`（既定はサーバーまたは MCP 設定の profile。存在しなければ
  `404 profile_not_found`）、`all`、繰り返し可能な `status`、`limit`（1〜500、既定 100）、
  `offset` で、**それ以外のパラメータは無視せず `400` で拒否します**。各状態は
  選択した profile について導出します。`index_status` は index 後に文書か profile の
  index identity が変わると `stale` になり（1.1.0 は全 profile をまとめてファイル hash
  だけを比べていました）、`fallback` はテキスト一致ではなく chunk variant の
  fallback 印から判定し、profile を限定した除外はその profile について報告します
  （1.1.0 は複数 profile で index された文書でこれを落としていました）。行には
  `aggregate_status`（`status=` が絞り込む状態。優先順は
  `excluded > error > pending > indexing > stale > fallback > indexed > ready`）と
  `ingest_ms`（常に `null`）を追加しました。`profile` は常に解決した profile、
  `content_hash` は抽出が完了していない文書では `null`（`file_hash` には保存値）です。
  行の `status` は保存されている抽出状態のままです。バッチ処理は
  `?status=stale&status=ready` で、その profile が追いついていない文書だけを取得できます。

### 互換性

**`GET /api/v1/documents` と MCP `list_documents` のクライアントは、envelope の
`documents` を読み、pending や失敗した文書を見るには `all=true` を渡し、
`limit`/`offset` でページングする必要があります。**

**1.1.0 で作った project は一覧・検索・index を続けられますが、
`mrag catalog migrate-identities` を実行するまで `mrag add` を拒否します**
(exit 2、エラーにコマンド名を表示)。一覧は保存済みの `external/...` や `legacy/...` を
移行後と同じように解釈し、値は保存されたまま返します。1.1.0 より前の project は
コマンド不要で、catalog に最初に書き込むときに各行へ `identities/legacy/v1/<id>` が
付き、文書の無い catalog は開いた時点で scheme 2 になります。`identities/` 配下の
project ファイルがあると移行は止まるので、その文書を削除し、ファイルを移して
追加し直してください。identity の規則は、パスとそれが生むべき identity を並べた
fixture で固定しました。さらに、文書の事実と、それが生むべき状態・絞り込み結果・
並び順・ページを並べた判定表を 2 つ目の fixture として文書一覧を固定したので、
コードだけで規則を変えるとテストが失敗します。

---

## 1.1.0 — 2026-09-22

### 追加

- **Ollamaへ依頼する生成長と入力長の上限。** 新規profileは
  `augmentation.max_context_tokens: 512`、`augmentation.context_window_tokens: 16384`、
  `embedding.max_input_tokens: 8192`を持ちます。contextual augmentationは前の2つを
  `/api/generate`の`options.num_predict`と`options.num_ctx`として送り、埋め込みは3つ目を
  `/api/embed`の`options.num_ctx`と`options.num_batch`の両方として、index時にも検索時にも送ります。
  これらが無いと、暴走したcontextはserverの上限まで生成され、長すぎるpromptはOllamaがerrorなしに
  切り詰めて文書抜粋を落とします。Ollama 0.34.0では、長いchunkが先頭2,048 tokenだけでHTTP 200のまま埋め込まれます。
  mragは値をmodel自身の最大と照合しません。modelの対応範囲内で設定してください。
- **Source identity。** 各文書が由来を記録します。project内ならproject相対path、
  project外ならopaqueなroot keyとroot相対pathで、単一fileの`mrag add`と再帰addが同じ規則で導出します。
  登録済みの元fileの内容が変わった場合は、同じdocument IDのまま抽出物を置き換えます。
- **文書一覧の行**をNative APIの`GET /api/v1/documents`とMCPの`list_documents`で共有します。
  従来の項目に加えて`source_identity`、`display_name`、`source_binding_status`、`content_hash`、
  `source_status`、`index_status`、`retrieval_status`、`profile`、`exclusion_id`、`updated_at`を返します。

### 修正

- **Difyの`metadata_condition`が受理されて無視されていた問題**。filterを指定したrequestに、
  filterの効いていない結果が正しい結果として返っていました。`document_id`、`source`、`chunk_id`、
  `heading_path`に対して、`contains`、`not contains`、`start with`、`end with`、`is`、`is not`、
  `empty`、`not empty`を`and` / `or`で適用します。filter前に`top_k`の4倍の候補を取得します。
  この範囲外のfield・演算子・構造は無視せず、HTTP 400と`error_code: 4001`で拒否します。
- **`mrag init --force`が、何を取り残すかを告げずにprojectを上書きしていた問題**。
  設定を書き直す一方で`mrag.db`は残すため、vaporetto libraryの無い環境で再初期化すると、
  vaporettoで構築済みのFTS表に対して`trigram`と書かれていました。文書を持つproject、
  異なるKB ID、`mrag.yaml`またはFTS表と食い違うtokenizerは、何も書く前に拒否します。
  空のprojectでは上書きするfileを表示します。`--kb-id`を省略した場合は既存のIDを維持します。
  また`mrag.yaml`の無いdirectoryに`mrag.db`がある場合も`mrag init`は拒否します。

### 変更

- 文書一覧の順序を新しい順から`(source_identity, document_id)`順に変更しました。
  Native APIとMCPの`list_documents`のpaginationが対象です。`status`は保存値
  (`pending`、`extracted`、`error`)のままです。
- Dify recordの`metadata`は`document_id`、`source`、`chunk_id`、`heading_path`
  (見出しpathを` > `で連結)の4項目だけになりました。`metadata_condition`で指定できる項目です。
  それ以外のchunk metadataは返しません。
- content identityは変わりません。SHA-256が登録済み文書と一致するfileは、どのpathから来ても
  `skipped_duplicate`です。`--force`は一致した文書をそのIDのまま再抽出し、その文書のidentityを保ちます。

### 互換性

**既存の知識ベースはそのまま動作し、再indexは不要です。** catalogへ書き込む最初のcommandが
`source_identity`を追加し、identity方式を記録します。読み取りでは変更しません。
このリリースより前に追加した文書は由来を復元できないため、`legacy/v1/<document_id>`・
`source_binding_status: legacy_unbound`として一覧に出ます。mragはpathを推測しません。
変更の無いそれらのfileを追加し直すと`skipped_duplicate`になります。新しい上限設定を持たないprofileは
1.0.2と同一のrequestを送り、index identityも変わりません。設定すると該当profileのidentityが変わり、
次の`mrag index`でその文書を作り直します。Ollamaは別の`num_ctx`で起動したrunnerを載せ直すため、
同じserverを使うclient間ではmodelごとに値を揃えてください。

---

## 1.0.2 — 2026-09-14

### 修正

- **サーバーがcontextual promptを大きすぎるとして拒否しても抜粋を短くせず、chunkが
  rawになっていた問題**。prompt内の文書抜粋は8,000文字で打ち切りますが、
  サーバーはtoken数で拒否し、受け付ける量はそのbatch sizeで決まります。Ollamaの背後の
  llama.cpp serverは、上限よりずっと短い日本語の業務文書に対して
  `input (2123 tokens) is too large to process. increase the physical batch size
  (current batch size: 2048)` を返しました。llama.cppはこれをserver errorとして報告し、
  `ollama_post` はHTTP 500を一時的な障害とみなすため、同じpromptがbackoff付きで
  3回送られてからfallbackすることがありました。拒否はstatusにかかわらずメッセージで判別して
  リトライしないようにし、contextual augmentationは抜粋を半分にして再送します —
  4,000、2,000、1,000文字の順で、各段階は `↻ retry` 行として表示されます。
  `failure_policy` が適用されるのは、1,000文字のpromptも拒否された場合だけです。
  **最初のpromptを受け付けるサーバーには1.0.1とまったく同じ要求が送られ**、
  index identityは変わらず、再indexは不要です。これまでfallbackしていたchunkは、
  その文書が次にindexされるときに文脈を得ます。拒否されるかどうかはサーバーに
  依存します。Apple Silicon上のOllama 0.34.0は14,621 tokenのpromptを受け付けました。

## 1.0.1 — 2026-09-03

### 修正

- **index構築時間がindexの大きさとともに増えていた問題**。`_cleanup_document` は
  再index前に文書のFTS行を削除しますが、これがindex実行のたびに全文書に対して
  走っていました — 削除するものが何も無い初回buildでも同様です。`fts_chunks` が
  絞り込みに使える列はすべて `UNINDEXED` であるため、この削除は使える索引を持たず
  表全体を走査します。本schemaでの実測で2,000行のとき0.65 ms/文書、20,000行では
  4.61 ms/文書であり、総costはcorpus規模のおよそ二乗で増えていました。文書のchunk行
  が存在しない場合には削除を行わないようにしました — これは索引の効く検索であり、
  初回buildはまさにこの場合にあたります。再indexは従来どおり置き換える行を削除します。
- **0.24.0より前に構築したナレッジベースが `local` モードで検索できなかった問題**。
  0.24.0でQdrant collectionの命名方式を変更した際、影響は `qdrant.mode: server`
  のみと記載していましたが誤りでした。collection名は `local` モードでも同じ方法で
  導出されるため、0.24.0より前にindexしたプロジェクトではvector / hybrid検索が
  すべて `Collection mrag_…_<fingerprint> not found` で失敗し、アップグレード後の
  `mrag index` は新規追加文書だけを新しいcollectionに書き込んで、それ以前の文書を
  到達不能のまま残していました。vectorは初回利用時に自動で移行されるようになりました。
  `mrag search`、`mrag eval`、`mrag serve`、`mrag mcp`、`mrag index`、`mrag reindex`
  のいずれも、`chunk_variants` が旧collectionに紐づけたままのpointを新collectionへ
  移し、該当行を付け替え、参照が無くなった旧collectionを削除します。再embeddingは
  不要で、stderrに1行の通知(`Migrated N vector point(s) from pre-0.24.0 Qdrant
  collection …`)が出るだけです。localモードで30,000 pointあたり約20秒です。移動する
  のはデータベースが名前を持つpointだけなので、共有Qdrant server上で別ナレッジベース
  のpointも含むcollectionはそれらを保持したまま残され、`mrag reindex` で新名称に
  再構築済みの旧collectionは孤児として扱われます(localモードでは削除、serverでは
  放置。統合し直すことはありません)。下記0.24.0の項に訂正を追記しました。
- **`mrag eval` が近似重複の結果を見落としていた問題**。重複判定は外側の空白を
  除いたchunk本文の完全一致だったため、実際に結果を埋め尽くす種類の重複 — 年次
  調査報告の各年版に転載された同一段落で、chunk境界・見出しレベル・空白だけが
  異なるもの — が一度も警告されませんでした。あるcorpusでは1クエリの上位8件中
  6件が同一段落のコピーで、いずれも無印でした。結果は正規化した文字shingle
  (Unicode NFKC、大文字小文字・空白・句読点を除去)の重なり係数0.85以上で比較する
  ようになり、警告は先行する結果を名指しします: `⚠ duplicate of [1]`、
  `⚠ near-duplicate (0.93) of [1]`、初出側には `⚠ duplicated by [3], [4]`。
  正規化後40文字未満の本文は従来どおり完全一致のみ警告します。

### 変更

- **`mrag doctor` がapswを独立した行で報告するようになりました**。`fts_tokenizer`
  が `vaporetto` のプロジェクトは、`vaporetto` extraが無いと検索時に
  `APSW is not installed` で失敗しますが、doctorはそれをライブラリのロード失敗
  として暗示するだけで、しかもsqlite-vaporettoライブラリを複数検出した場合には
  ロード試験自体を省略していました。apswのバージョン、または `not installed` と
  インストールコマンドを表示し、ライブラリはあるがapswが無い場合は
  "failed to load" ではなくその旨を述べるようになりました。

---

## 1.0.0 — 2026-07-31

初のメジャーリリース。**破壊的変更**: PDF取り込みの廃止と、MITライセンスへの変更。

### 廃止

- **プロセス内でのPDF抽出**。`mrag add` が受け付けるのは `.md`、`.markdown`、`.txt`
  のみです。`.pdf`、`.html`、`.htm`、`.docx`、`.pptx`、`.xlsx` は
  `Unsupported file type: <ext> (requires external conversion to Markdown)`
  として拒否されます。ドキュメント変換は専用エンジンの担当になりました。PDFは
  [docling](https://github.com/docling-project/docling)、Office形式は
  [MarkItDown](https://github.com/microsoft/markitdown) で変換し、生成された
  Markdownを追加してください。
- `mrag add` および `mrag extract` の `--extractor` / `-e`。
- `--converter-jobs` とその背後の並列変換プール。PDF抽出の並列化のためだけに
  存在していたものです。
- `mrag.yaml` の `default_extraction` とプロファイルの `extraction`。既存の設定
  ファイルに残っていても無害です(拒否されず、無視されます)。
- PyMuPDF依存、およびPyInstallerビルドスクリプトでの収集指定。

### 変更

- **ライセンス: AGPL-3.0 → MIT**。PyMuPDF(AGPL-3.0 / Artifex商用のデュアル
  ライセンス)が本プロジェクト唯一のコピーレフト依存であり、PyInstallerビルドが
  それを配布バイナリへ同梱していたため、配布物全体がAGPLの条件下に置かれて
  いました。撤去により、残る依存はすべてMIT、BSD、Apache-2.0、ISC、MPL-2.0の
  いずれかになりました。0.27.0以前のリリースはAGPL-3.0のままです。
- 本リリース以降でビルドした単一バイナリはMITで配布できます。
- 新規追加ドキュメントの `extraction_provider` は `plain` として記録されます。

### 互換性

**既存のナレッジベースは影響を受けません**。`mrag index`、`mrag reindex`、および
すべての検索経路が読むのは `data/documents/` に保存済みの抽出済みテキストであり、
元ファイルではありません。したがって旧バージョンがPDFから取り込んだドキュメントは
検索も再インデックスも引き続き可能です。カタログの行は `source_type='pdf'`、
`extraction_provider='pymupdf'` の値を保持し続けます。本変更はプロファイル
ハッシュを変えないため、この変更自体による再インデックスは発生しません(ただし
0.24.0〜0.27.0をまたぐアップグレードでは、以下の理由により必要になる場合が
あります)。

影響を受けるのは**新規**PDFの取り込みだけです。

---

## 0.27.0 — 2026-07-30

### 修正

- **`mrag reindex` が再構築前にプロファイルを空にする方式をやめました**。修正した
  不具合は2件です。**(1)** プロファイルのchunk行を削除する一方でQdrantのpointを
  一度も削除していなかったため、reindexのたびに1世代分のvectorが残り続けて
  いました。ベクトル検索はchunk行が存在しないhitを警告なしに捨てるため、この孤児
  pointが `top_k` の枠を占め、要求件数より少ない結果が返っていました。reindex
  回数に比例して悪化し、しかも何の警告も出ません。**(2)** 削除が再構築より先に
  完了していたため、Ollamaへ接続できない等の失敗が起きるとプロファイルの
  インデックスが失われた状態で残りました。

  reindexは文書単位の強制再構築を行うようになり、各文書の旧chunk・FTS行・vector
  pointは新しいもののembeddingが成功した後にのみ置き換えられます。したがって実行が
  失敗しても従来のインデックスは検索可能なまま残り、exit 1 で終了します。また
  完了後にcollectionとデータベースを突き合わせ、`Reclaimed N orphaned vector
  point(s)` を表示します。**旧版が蓄積した孤児pointは、プロファイルごとに
  `mrag reindex` を一度実行すれば回収されます** — Qdrantの手動クリーンアップは
  不要です。

  なお**異なる埋め込みモデル**で構築されたcollectionに残るpointはプロファイルへ
  帰属できないため対象外です。モデルを変更した経緯がある場合は該当collectionを
  手動で削除してください。

### 変更

- プロファイルを本当に空にしたい呼び出し側のために `cleanup_profile_index()` は
  残していますが、clientが渡されないときにvector削除を黙って飛ばすのではなく
  自前でQdrant clientを構築するようにしました。

---

## 0.26.0 — 2026-07-26

### 追加

- `augmentation.think`(既定値 `false`)。

### 変更

- **コンテキスチュアル拡張が既定で思考トークンを送らなくなりました**。既定モデルの
  `gemma4:e2b` をはじめとする推論モデルは、生成予算の大半をOllamaが応答から除去
  する思考トークンに費やしていました。実測では54文字の注記を返すのに390トークンを
  生成しています。思考を無効にすると1回の呼び出しが 6.5秒 → 3.4秒 になり、しかも
  注記は**より長く具体的**になりました。mragはモデルごとに1回 `/api/show` を確認し、
  `thinking` capabilityを持つモデルにのみこのパラメータを送るため、非対応モデルへの
  影響はありません。

  **`think` はプロファイルハッシュに含まれます**。そのため
  `augmentation.strategy: contextual` を使用しているプロファイルは、次回
  `mrag index` 実行時にインデックスが全件再構築されます。生成されるコンテキストが
  実際に変わる以上、旧インデックスを残すと設定と不整合になるためです。従来の挙動を
  維持したい場合は `augmentation.think: true` を設定してください。

---

## 0.25.0 — 2026-07-26

### 修正

- **`mrag search` とネイティブAPIの `POST /api/v1/retrieve` が、プロファイルの
  `retrieval.top_k` を参照するようになりました**。従来はどちらも呼び出し側が
  `--top-k` / `top_k` を指定しない場合に固定値5で上書きしていたため、プロファイルに
  `top_k: 20` と設定しても無視されていました。

  アップグレード後、件数を明示しない検索はプロファイルの値を返します。`mrag init`
  が生成するプロファイルの場合は8件です。従来の件数を維持したい場合は `--top-k 5`
  (APIの場合はリクエストボディに `"top_k": 5`)を明示するか、プロファイルの
  `retrieval.top_k` を設定してください。

  MCPサーバーは以前からプロファイル値を解決していたため変更はありません。Dify
  エンドポイントもプロトコル上 `top_k` が常に明示されるため変更はありません。

---

## 0.24.0 — 2026-07-24

### 変更

- **破壊的変更(Qdrant Serverバックエンドのみ): Qdrant collectionの命名方式を
  変更しました**。異なるナレッジベース/プロファイルが同一のQdrant collectionへ
  意図せず衝突する問題を防ぐためです(旧方式では例えば `"kb-1"` と `"kb 1"` の
  ような異なるIDが同一のcollection名に正規化されうる)。

  この変更は `qdrant.mode: server` 使用時のみに影響し、デフォルトの `local`
  モードには影響しません。`qdrant.mode: server` を使用している場合、アップグレード
  後は従来作成済みのcollectionが見つからなくなります。各プロファイルで一度
  `mrag reindex`(または `mrag index`)を実行し、新しい名前でcollectionを再構築して
  ください。再indexするまでの間、該当プロファイルでの検索は誤ったデータが混ざる
  のではなく空の結果を返します。データが削除されるわけではなく、旧collectionは
  Qdrant上に取り残されるだけなので、新しいcollectionへのデータ投入を確認したうえで
  手動で削除できます。

  **訂正:** `local` モードには影響しないという記述は誤りでした。命名変更は両モードに
  適用されており、0.24.0より前にindexしたlocalモードのプロジェクトは、1.0.1の項に
  記載した自動移行が入るまで `Collection … not found` で失敗していました。
