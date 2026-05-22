# 複数ナレッジベースを束ねる — `mrag registry`

このドキュメントでは、複数の mrag プロジェクトをひとつのレジストリにまとめるための `mrag registry` コマンド群について解説します。

**Knowledge Registry** は、ひとつの親ディレクトリの直下に並んだ複数の mrag プロジェクトを集約し、`knowledge_registry.yaml` という単一のメタデータファイルに記述するための仕組みです。主にAI エージェントが「いま手元にどんなナレッジベースが揃っていて、どれを検索すべきか」を判断するためのインデックスとして使うことを想定しています。

mrag CLI 自身は、検索やインデックス処理の中で `knowledge_registry.yaml` を**読みません**。レジストリは外部のエージェントが参照するためのファイルです。`mrag.yaml` と `kb_information.yaml` との役割分担は次のとおりです：

| ファイル | 利用者 | 役割 |
|---|---|---|
| `mrag.yaml` | mrag CLI | プロジェクトのランタイム設定 |
| `kb_information.yaml` | 外部エージェント/ユーザー | ナレッジベース単体のメタデータ（→ [kb-information-ja.md](./kb-information-ja.md)） |
| `knowledge_registry.yaml` | 外部エージェント/ユーザー | **複数ナレッジベースの発見・選択用インデックス** |


## 2 つのサブコマンド

| サブコマンド | 使うとき |
|---|---|
| `mrag registry generate <root_dir>` | ルートディレクトリ配下の各プロジェクトから `knowledge_registry.yaml` を生成する |
| `mrag registry validate <registry_path>` | 生成済みのレジストリがスキーマと実ファイル構成と整合しているかを検証する |


## ディレクトリ配置

`mrag registry` が前提とするのは、ひとつの親ディレクトリ（以下ルート）の**直下**に mrag プロジェクトが並んでいる構成です。

```text
my-kb/                          ← ルート（レジストリの置き場所）
├── knowledge_registry.yaml          ← 生成物
├── kb-device/                       ← 個別の mrag プロジェクト
│   ├── mrag.yaml
│   ├── kb_information.yaml
│   └── ...
├── kb-contract/
│   ├── mrag.yaml
│   ├── kb_information.yaml
│   └── ...
└── kb-design/
    └── ...
```


## `mrag registry generate` — レジストリを生成する

```bash
# 標準的な使い方（root 直下に knowledge_registry.yaml を書き出す）
mrag registry generate ./knowledges

# 出力先を指定する
mrag registry generate ./knowledges --output ./meta/knowledge_registry.yaml

# ファイルに書かず、stdout に YAML をプレビュー
mrag registry generate ./knowledges --dry-run
```

挙動：

1. ルートディレクトリ直下のサブディレクトリを 調査する
2. 各サブディレクトリで `kb_information.yaml` と `mrag.yaml` の両方を確認する
3. 必要な情報を `kb_information.yaml` から読み取り、レジストリエントリを構築する
4. エントリ間の `id` 重複をすべて検査する
5. `--output` の指定があればそこに、無ければルート直下の `knowledge_registry.yaml` に書き出す

### スキップとエラーの方針

| 状況 | 挙動 |
|---|---|
| サブディレクトリに `kb_information.yaml` が無い | warn を出して skip |
| `mrag.yaml` が無い（mrag プロジェクトでない） | warn を出して skip |
| `kb_information.yaml` の構造が不正 | warn を出して skip |
| 1 件もマッチしない | **exit 1**（タイポやネスト誤配置を早期検知するため） |
| `id` の重複 | **exit 1**（書き込みなし） |

警告メッセージは stderr に出力されます。stdout はパイプ可能なペイロード（`--dry-run` 時の YAML）専用です。


## `path` の解決基準

レジストリ内の `knowledge_bases[].path` は、**レジストリファイル自身が置かれているディレクトリ**を基準とした POSIX 形式の相対パスです。

```yaml
knowledge_bases:
  - id: kb_device
    path: ./kb-device        # ルート直下に kb-device/ がある標準ケース
  - id: kb_legacy
    path: ../legacy/kb-old   # --output で離れた場所に書き出した場合は上方向の参照も入る
```

この設計の意図は**可搬性**です。ルートディレクトリごと `scp` や `git push` で別マシンに移動しても、`path` がレジストリ基準で記述されているためエージェント側のワークフローが壊れません。

エージェント側は `cd $(dirname knowledge_registry.yaml) && cd <kb.path>` のように解釈すれば、常に正しいプロジェクトディレクトリに到達できます。

> 注意：`--output` で sibling や parent dir に書き出した場合、`path` には `../` が含まれます。これはエラーではなく仕様です。


## `mrag registry validate` — レジストリを検証する

```bash
# 人間向けの表示で検証
mrag registry validate ./knowledges/knowledge_registry.yaml

# JSON 出力（エージェント向け）
mrag registry validate ./knowledges/knowledge_registry.yaml --json
```

検証ステップ：

1. **ファイル読み込み** — レジストリが存在しないか、YAML として解釈できない場合は即時 exit 1
2. **スキーマ検証** — Pydantic スキーマに適合しない場合は即時 exit 1
3. **整合性チェック** — 以下を**全件集計**し、最後に issue 数が 0 でなければ exit 1
   - `id` の重複
   - `knowledge_bases[].path` が存在しない、またはディレクトリでない
   - `<path>/mrag.yaml` が存在しない
   - `<path>/kb_information.yaml` が存在しない
   - `preferred_profiles` の各プロファイルが `<path>/profiles/<name>.yaml` に存在しない

> 補足：致命的エラー（ファイル不在・YAML パース失敗・スキーマ不適合）は即時 exit、それ以外の整合性 issue は全件集計してから exit します。1 回の実行ですべての問題を取得できるので、エージェントが 1 ターンで修正計画を立てられます。


### 安定した issue キー

`--json` 出力の各 issue は、`issue` フィールドに以下の安定文字列を持ちます。エージェントはこの値で分岐できます：

| キー | 意味 |
|---|---|
| `path_not_found` | `knowledge_bases[].path` が存在しない、またはディレクトリでない |
| `mrag_yaml_not_found` | `<path>/mrag.yaml` が見つからない |
| `kb_information_yaml_not_found` | `<path>/kb_information.yaml` が見つからない |
| `preferred_profile_not_found` | `<path>/profiles/<name>.yaml` が見つからない |
| `duplicate_id` | `knowledge_bases[].id` が他のエントリと重複している |

### JSON 出力スキーマ

```json
{
  "registry_path": "/abs/path/to/knowledge_registry.yaml",
  "schema_valid": true,
  "ids_unique": true,
  "issues": [
    {
      "knowledge_base_index": 1,
      "knowledge_base_id": "kb_design",
      "issue": "preferred_profile_not_found",
      "detail": "preferred_profile 'hybrid-rerank' not found at ./kb-design/profiles/hybrid-rerank.yaml"
    }
  ],
  "issue_count": 1
}
```


## `agent_instructions` — エージェントへの指示

レジストリには、エージェントに対する選択ポリシーと検索コマンドのテンプレートを記述するセクションが含まれます。生成時にはデフォルト値が入ります。必要に応じて編集して運用に合わせてください（編集後は `mrag registry validate` で整合性を再確認してください）。

```yaml
agent_instructions:
  selection_policy: >
    Select the most relevant knowledge base based on the user's question.
    If the question spans multiple domains, search multiple knowledge bases.

  search_command_template: |
    cd {path}
    mrag search "{query}" --profile {profile} --json
```

`{path}` / `{query}` / `{profile}` の置換はエージェント側で行う前提です。mrag CLI 自身はこの文字列を解釈しません。


## 推奨パターン — 生成 → 検証の 2 ステップ

エージェントから利用する場合や、レジストリを CI で更新する場合は、**生成と検証を必ずペアで実行**するのが安全です。

```bash
# Step 1: レジストリを生成
mrag registry generate ./knowledges

# Step 2: スキーマと実ファイル構成の整合をチェック
mrag registry validate ./knowledges/knowledge_registry.yaml --json | jq '.issue_count'
```

- 生成時の警告は stderr に出るのでログとして残せます
- 検証時の `issue_count` が 0 であれば、エージェントが安心して各プロジェクトに `cd` できる状態です
- ナレッジベースの増減・プロファイル追加・名称変更があったら、`generate` を再実行してください（差分マージではなく全件再生成です）


## Tips

- `--dry-run` は **stdout に YAML を出す**ので、`mrag registry generate ./knowledges --dry-run | yq` のような俯瞰用パイプラインに使えます。警告メッセージは stderr に分離されています
- 1 件もマッチしない場合の exit 1 は、ルートディレクトリを間違えていたり、サブディレクトリが 1 階層深く配置されているケースをすぐ気付けるように設計されています。エラーメッセージ末尾の Tip に従って `mrag init <root>/<kb-name>` を実行してください
- レジストリの手動編集は想定内です。`agent_instructions` のカスタマイズ後は `mrag registry validate` を回してから運用に乗せてください
- 空のレジストリを意図的に作りたい場合は `touch <root>/knowledge_registry.yaml` で代替してください（v0.18.0 時点では `--allow-empty` 相当のオプションはありません）
- ナレッジベース単体のメタデータについては [kb-information-ja.md](./kb-information-ja.md) を参照してください
