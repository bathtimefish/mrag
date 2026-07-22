# チュートリアル

このドキュメントはmragを操作するためのシンプルなチュートリアルです。
チュートリアルの前にREADMEに記載されているmragのインストールを完了してください。


## コマンドラインで動かす

### Step 1. プロジェクトを作成する

任意の作業ディレクトリで次のコマンドを実行します。

```bash
mrag init my-first-kb --non-interactive
cd my-first-kb
```

カレントディレクトリに `my-first-kb/` というサブディレクトリが作成され、その中に mrag のプロジェクト一式が展開されます。代表的なファイル：

- `mrag.yaml` — プロジェクトのランタイム設定
- `kb_information.yaml` — このナレッジベースが「何を含み、何に使えるか」を記述するメタデータ
- `profiles/default.yaml` — 検索プロファイル（チャンク戦略・検索戦略をまとめた設定）
- `mrag.db` — SQLite データベース（ドキュメントとチャンクの実体）
- `data/` / `qdrant/` / `cache/` — 補助ディレクトリ

初期化時には、利用可能なトークナイザーが自動検出されます。vaporetto が見つかった場合は自動的に有効化され、次のような表示が出ます：

```
✓ vaporetto tokenizer detected (libsqlite_vaporetto.dylib)
✓ Created directory structure
✓ Generated mrag.yaml
✓ Generated profiles/default.yaml
✓ Generated kb_information.yaml
✓ Initialized mrag.db
```

これで「空のナレッジベース」ができました。

### Step 2. ドキュメントを投入する

`mrag add`でPDF、text、Markdownの単一fileを登録します。

```bash
mrag add ./sample.pdf
```

directoryを投入する場合は`--recursive`を明示します。書き込む前に決定的な選択結果を
previewしてください。

```bash
mrag add ./documents --recursive --dry-run --json \
  --include '**/*.pdf' --include '**/*.md'

mrag add ./documents --recursive --json \
  --include '**/*.pdf' --include '**/*.md'
```

再帰追加はrepeatableな`--include`／`--exclude` glob、root `.mragignore`、hidden pathと
symlinkの制御、converter並列数の上限、部分失敗の明示的reportをサポートします。完全な仕様は
[ディレクトリの再帰追加](./recursive-add-ja.md)を参照してください。

このコマンドは次の処理をまとめて行います：

1. PDF からテキストを抽出（PyMuPDF を使用）
2. 抽出結果を `data/documents/` に保存
3. 中身のハッシュをとってドキュメント ID を割り当て、`mrag.db` に登録

この段階では各ドキュメントが登録されただけで、**まだ検索はできません**。検索可能にするには
次のStep 3でインデックスを構築する必要があります。再帰modeを含め、`mrag add`が暗黙に
indexを開始することはありません。

### Step 3. インデックスを構築する

```bash
mrag index
```

このコマンドが、ナレッジベースを検索可能にする「実際の仕事」をします。具体的には：

1. ドキュメントをチャンク（小さな単位）に分割
2. 各チャンクの Embedding ベクトルを Ollama 経由で計算
3. ベクトルを Qdrant に書き込み
4. チャンク本文を SQLite FTS5（キーワード検索インデックス）に書き込み

完了するとサマリが表示されます：

```
✓ Indexed: 12  Up-to-date: 0  List-skipped: 0
Log: logs/20260519101000-index.json
```

「Indexed: 12」は、今回新規にインデックスされたチャンク数（PDF のページ構成によって増減）を意味します。

`logs/` に毎回 JSON ログが保存されます。`--output-log`でログの出力先を変更できます。
一度`index`を実行したプロジェクトに新しいドキュメントを`add`して再び`index`を実行した場合は、すでにインデックス済みのドキュメントはスキップされ、新規追加分だけが処理されます。 

`--profile`で指定したプロファイル設定で`index`を実行できます。


### Step 4. 検索する

検索ができる状態になりました。さっそく試します。

```bash
mrag search "キーワード"
```

デフォルトのプロファイル設定では **ハイブリッド検索**（キーワード検索とベクター検索を組み合わせ、結果を融合する方式）が実行されます。

出力はこのような形になります：

```
[1] score=6.39  doc=sample.pdf  chunk=eb0495d2...
    …本文の冒頭抜粋がここに表示されます…

[2] score=5.81  doc=sample.pdf  chunk=3fa12c11...
    …次のチャンクの抜粋…

Score stats:  min=5.81  max=6.39  mean=6.10  σ=0.0412

Document distribution:
  sample.pdf  ████████████████████ 3
```

各行の意味：

- `score` — 関連度スコア（大きいほどクエリに近い）
- `doc` — ヒットしたドキュメント名
- `chunk` — チャンクの ID（後で `mrag inspect chunk <id>` で深掘りできます）
- `Score stats` — 上位結果のスコア分布。スコアが団子状か離散しているかを目視で確認できます
- `Document distribution` — どのドキュメントから何件ヒットしたかの棒グラフ

この検索結果をAIエージェントに渡すことでAIエージェントにナレッジを提供できます。

#### 検索ストラテジーを切り替えてみる

mrag は複数の検索ストラテジーを持っており、`--strategy` で切り替えられます。

```bash
# キーワードのみ（FTS5 BM25 — 表記揺れに弱いが速い）
mrag search "知りたいキーワード" --strategy keyword

# ベクターのみ（意味的に近い文を引く — 表記が違っても拾える）
mrag search "知りたいキーワード" --strategy vector

# ハイブリッド（デフォルト — 上記 2 つを組み合わせて融合）
mrag search "知りたいキーワード" --strategy hybrid
```

作成したナレッジベースの多くでは`hybrid`検索が有益だと思いますが、必要に応じて検索ストラテジーを切り替えることができます。

#### リランキング

プロファイルの`rerank.enabled`を`true`に設定するとリランカーが有効になります。

```bash
# 上位 3 件だけ表示
mrag search "知りたいキーワード" --top-k 3

# リランキング（CrossEncoder による並び替え）を無効化
mrag search "知りたいキーワード" --no-rerank
```

### Step 5. 正本を削除せずに古いナレッジを除外する

保持documentからCLI、HTTP API、MCP検索への寄与を止める場合はdocument levelのexclusionを
使用します。`mrag search --json`または`GET /api/v1/documents`から`document_id`を取得し、
cleanupをpreviewしてから適用します。

```bash
mrag exclusions add --document-id <DOCUMENT_ID>
mrag exclusions add --document-id <DOCUMENT_ID> \
  --reason "superseded specification" --force
```

最初にexclusion policyが有効になり、その後`--force`がFTS、chunk／variant、document index、
Qdrantの派生dataを物理cleanupします。originalと抽出成果物は保持し、index／reindexも対象を
skipし続けます。

復帰するときは`mrag exclusions list`が返すexclusion IDを使用し、保持documentを明示的に
indexします。

```bash
mrag exclusions restore <EXCLUSION_ID> --force
mrag index --document-id <DOCUMENT_ID>
```

profile scope、Qdrant cleanup degraded時のretry、破壊的な`mrag remove --force`との違いは
[ドキュメントの検索除外](./document-exclusions-ja.md)を参照してください。

---

## AI エージェントで mrag を操作する

### SKILL.md

Claude CodeなどのAIエージェントに[AGENTS.md](../AGENTS.md),[SKILL.md](../SKILL.md)を読ませることで、AIエージェントに直接mragを操作させることもできます。

### プロンプト例

#### ナレッジベースを作成する

```
> あたらしく ./my-kb というmragプロジェクトを作成して ./documents/ に収録されているすべてのPDFをもとにデフォルトのRAGナレッジベースを作成してください。

...

⏺ Bash(cd /home/user/tools/mrag &&           
      /home/user/tools/mrag/.venv/bin/mrag init ./my-kb
      --non-interactive --kb-info-json /tmp/my-kb-info.json)

...

⏺ Bash(cd /home/user/tools/mrag/kbs/my-kb &&
      /home/user/tools/mrag/.venv/bin/mrag add ../data --recursive
      --include '**/*.pdf' --dry-run --json)

...

⏺ Bash(cd /home/user/tools/mrag/kbs/my-kb &&
      /home/user/tools/mrag/.venv/bin/mrag add ../data --recursive
      --include '**/*.pdf' --strict --json)

...

⏺ Bash(/home/user/tools/mrag/.venv/bin/mrag index 2>&1 | tee          
      mrag-index-$(date +%Y%m%d-%H%M%S).log) 

...

⏺ Bash(/home/user/tools/mrag/.venv/bin/mrag search "Some keyword      
      in document" --top-k 3 --json 2>/dev/null |
      /home/user/tools/mrag/.venv/bin/python -c "                     
      import jso…)                                         
  ⎿  strategy: hybrid reranked: False count: 3                          
     score stats: {'min': 0.015873015873015872, 'max': 0.0163934426
     2295082, 'mean': 0.016131830251343737, 'stdev': 0.000260224656

...

⏺ インデックス完了・検索動作確認まで完了しました。 

```

#### 検索する

```
> リランカーをONにして温度センサーの許容温度の仕様について調査してください 

...

  Read 1 file (ctrl+o to expand)                                        
                                                   
⏺ Update(profiles/default.yaml)                                         
  ⎿  Added 1 line, removed 1 line 

...

⏺ Bash(/home/user/tools/mrag/.venv/bin/mrag search "温度センサー
      許容温度 動作温度範囲 使用温度 仕様" --top-k 6 --json 2>/dev/null
      > /tmp/temp-search.json; /home/user/tools/mrag/.venv/b…)
  ⎿  strategy: hybrid reranked: True count: 6                           
     score stats: {'min': 0.5065747499465942, 'max': 0.581636965274
     8108, 'mean': 0.5461344619592031, 'stdev': 0.02947577986893289

...

⏺ リランカーを有効化（rerank.enabled: true）し、my-kb    
  内の温度センサー関連仕様書を調査しました。
  センサー測定値として設定可能な温度値の範囲は上記のとおりですが、モジュール本体を含む製品の動作保証範囲は -10°C ～ 50°C です...

```

SKILL.mdにはmragの基本的な操作や使う上でのノウハウが収録されているので、mragのコマンド群に習熟しなくてもAIエージェントに目的のRAGナレッジベースを作らせたり、精度分析をさせることが簡単にできます。
