[English](CHANGELOG.md) / 日本語

# 変更履歴

mragの主要な変更点を記録します。0.24.0より前のエントリは記録していません。
それらのリリースについてはリポジトリの履歴が記録となります。

---

## 未リリース

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
  残します。document ID は変わらず、再 index もしません。MRAG Plus にも同じコマンドが
  あります。
- **`identities/`** を予約しました。`mrag init` がその旨の `identities/README.md` を
  作ります。`mrag add` はその中のファイルを拒否し(`source_identity_reserved_path`、
  symlink 経由でも同じ)、再帰的な追加ではこのディレクトリを読み飛ばし、root に
  指定すると拒否します。ディレクトリが無い場合は書き込みを伴う次の `add` が作り直します。

### 互換性

**1.1.0 で作った project は一覧・検索・index を続けられますが、
`mrag catalog migrate-identities` を実行するまで `mrag add` を拒否します**
(exit 2、エラーにコマンド名を表示)。一覧は保存済みの `external/...` や `legacy/...` を
移行後と同じように解釈し、値は保存されたまま返します。1.1.0 より前の project は
コマンド不要で、catalog に最初に書き込むときに各行へ `identities/legacy/v1/<id>` が
付き、文書の無い catalog は開いた時点で scheme 2 になります。`identities/` 配下の
project ファイルがあると移行は止まるので、その文書を削除し、ファイルを移して
追加し直してください。共有 identity fixture は MRAG Plus のものとバイト単位で
一致させました。

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
