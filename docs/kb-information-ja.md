# ナレッジベースの自己記述 — `kb_information.yaml`

このドキュメントでは、各 mrag プロジェクトに同梱される **`kb_information.yaml`** の役割と、その生成・検証コマンド `mrag kb-info` について解説します。

`kb_information.yaml` は、ナレッジベースが「どのような知識を収録しているか」「どのような質問に有効か」などを記述するメタデータファイルです。mrag CLI 自身は検索やインデックス処理の中でこのファイルを参照しません。このファイルは AI エージェントに対してナレッジベースについての概要を提供します。


## ファイル構造（version: 1）

```yaml
version: 1                              # スキーマバージョン（現在 1 のみ）

knowledge_base:                         # 必須セクション
  id: kb_device                         # slug 形式の識別子（lowercase + 数字 + _）
  name: Device Knowledge Base           # 人間向けの表示名
  description: ""                       # 自然文の説明（空でも可）

agent_usage:                            # 任意セクション（省略時はデフォルト値）
  tags: []                              # ナレッジベースの分類タグ
  best_for: []                          # このKBが得意とする質問の例
  avoid_for: []                         # このKBで答えるべきでない質問の例
  preferred_profiles: [default]         # 推奨プロファイル名のリスト
  example_queries: []                   # 代表的なクエリの例
```

セクションごとの役割：

- **`knowledge_base`** — KB の同定情報（id / name / description）。registry 上でも同じ値が使われます
- **`agent_usage`** — エージェントがこの KB を選ぶときの判断材料。すべて空のままでも mrag は動きますが、記述しておくとLLMエージェントがナレッジベースを選択する際の精度が上がります


## `mrag init` での生成

`mrag init` は呼び出し方によって 3 通りのモードで `kb_information.yaml` を生成します。

| 呼び出し方 | モード | 生成内容 |
|---|---|---|
| `mrag init <dir>` | Interactive | name / kb_id / description をプロンプトで取得、`agent_usage` は空のテンプレート |
| `mrag init <dir> --non-interactive` | Non-interactive | name / kb_id を引数または cwd 名から導出、`agent_usage` は空のテンプレート |
| `mrag init <dir> --kb-info-json PATH` | JSON 入力 | JSON ファイルから `agent_usage` まで含めて完全に生成 |

> 補足：`agent_usage` の各フィールドは Interactive / Non-interactive モードではプロンプトされません。空のテンプレートが書き出されるので、必要に応じてあとから手動編集してください。最初から `agent_usage` を埋めたい場合は `--kb-info-json` を使います。


## `--kb-info-json` — エージェントから一括生成

`agent_usage` まで含めて完全な `kb_information.yaml` を生成するためのモードです。LLMエージェントから mrag プロジェクトを立ち上げる用途を想定しています。

```bash
# JSON Schema を取得（エージェントが入力 JSON を組み立てる手がかりに）
mrag init --print-kb-info-schema > schema.json

# JSON Schema は kb-info サブコマンドからも取得可能
mrag kb-info schema > schema.json

# JSON を渡して初期化
mrag init my-kb --kb-info-json ./kb-info.json
```

JSON ファイルの構造は `kb_information.yaml` のスキーマに **`project.name` を加えた**ものです：

```json
{
  "project": {
    "name": "my-kb"
  },
  "knowledge_base": {
    "id": "kb_device",
    "name": "Device Knowledge Base",
    "description": "Arduino と SIM7080G を中心とした IoT デバイス開発の知見"
  },
  "agent_usage": {
    "tags": ["arduino", "sim7080g", "mqtt"],
    "best_for": ["SIM7080G のトラブルシュート"],
    "avoid_for": ["仕様書レビュー"],
    "preferred_profiles": ["default"],
    "example_queries": ["SIM7080G の MQTT publish が数時間後に止まる"]
  }
}
```

`project.name` は `mrag.yaml` 側で参照される項目で、`kb_information.yaml` には書き出されません。JSON のバリデーションが失敗した場合はフィールド単位のエラーメッセージが出て exit 1 します（部分的に書き込まれたファイルは残りません）。


## `mrag kb-info` サブコマンド

プロジェクトディレクトリの中（`kb_information.yaml` がある場所）で実行します。

```bash
# YAML 本体をそのまま表示する（パイプや目視確認向け）
mrag kb-info show

# スキーマに対する整合性を検証する
mrag kb-info validate

# --kb-info-json 入力用の JSON Schema を出力する
mrag kb-info schema
```

各サブコマンドの挙動：

- **`show`** — `kb_information.yaml` の内容を stdout に流します。整形は一切しないので、ファイルそのものを確認したいときに便利です
- **`validate`** — Pydantic スキーマで検証し、`knowledge_base.id` / `name` / `preferred_profiles` のサマリと、`agent_usage` 各リストの件数を表示します。スキーマ違反があればフィールド単位のエラーで exit 1
- **`schema`** — `--kb-info-json` に渡す JSON ファイルの JSON Schema を出力します。`mrag init --print-kb-info-schema` と完全に同じ内容です


## フィールド規約

### `knowledge_base.id` の slug ルール

正規表現 `^[a-z0-9_]+$` を満たす必要があります。この規則は id のすべての入力経路（`--kb-id`、対話プロンプト、`--kb-info-json`）に対して、プロジェクトファイルを1つも書き出す前に適用されます。したがって `mrag.yaml` と `kb_information.yaml` の間で id の有効性が食い違うことはありません。違反するとサジェスト付きエラーで exit 1 します（Interactive モードでは exit せず、サジェストを新しいデフォルトとしてプロンプトを再表示します）：

| 入力 | 結果 |
|---|---|
| `kb_device` | OK |
| `kb_device_v2` | OK |
| `KbDevice` | NG（大文字） |
| `kb-device` | NG（ハイフン） |
| `KB-Device!` | NG → サジェスト `'kb_device'` を提示 |
| `""` | NG（空文字） |

> 重要：`id` は registry 上で KB を一意に識別する値です。同じ root の下に並ぶ KB の間で重複すると `mrag registry generate` が exit 1 します（→ [registry-ja.md](./registry-ja.md)）。slug は短く、ナレッジベースの内容を表す名前にしておくと運用が楽です。

### `preferred_profiles` の補正

`preferred_profiles` が空配列または未指定の場合、**読み込み時に in-memory で**自動的に `["default"]` に補正されます（Pydantic のモデルバリデータによる挙動です）。`mrag kb-info validate` はファイルを書き戻さないので、YAML 本体に `["default"]` が反映されるのは `mrag init` などファイルを書き出す処理を経たタイミングです。明示的に複数プロファイルを指定したい場合は単に書き並べてください：

```yaml
agent_usage:
  preferred_profiles:
    - default
    - hybrid-rerank
```

エージェントは原則として最初のプロファイルを使い、特殊なケースで後続を試す、といった運用が想定されています。`registry validate` を回すと、ここに書かれた各プロファイルが実在するか（`profiles/<name>.yaml` があるか）が検査されます。


## `agent_usage` の書き方

`agent_usage` はエージェントが「いま手元の KB の中で、どれを使うべきか」を判断するための材料です。空のまま運用しても mrag は動きますが、複数 KB を registry で束ねる構成では埋めておく価値があります。

```yaml
agent_usage:
  tags: [iot, mqtt, m5stack]
  best_for:
    - SIM7080G の AT コマンド・電源管理
    - Arduino 周りのファームウェア書き込み手順
  avoid_for:
    - 仕様書レビュー
    - 一般的な C++ のチュートリアル
  preferred_profiles: [default]
  example_queries:
    - SIM7080G の MQTT publish が数時間後に停止する
    - Arduino Nano で I2C デバイスが認識しない
```

各フィールドの使い分け：

- **`tags`** — エージェント側で KB をフィルタするためのキーワード。短く、機械的に扱える単語が向きます
- **`best_for`** / **`avoid_for`** — 自然文で「得意なこと／不得意なこと」を 1〜数行で。エージェントが選択ポリシーで読みます
- **`example_queries`** — 代表的なクエリを 2〜5 件。エージェントが「自分のクエリがこれに似ているか」を判断する手がかりになります


## registry との関係

複数の mrag プロジェクトを束ねて使う場合、各プロジェクトの `kb_information.yaml` は **`mrag registry generate`** によって `knowledge_registry.yaml` に集約されます。

- `knowledge_base.id` / `name` / `description` → registry の同名フィールドにそのまま転記
- `agent_usage.tags` / `best_for` / `avoid_for` / `preferred_profiles` / `example_queries` → registry の `knowledge_bases[]` 配下に転記
- `path` フィールドだけは registry 側で計算される（→ [registry-ja.md](./registry-ja.md)）

`kb_information.yaml` の値を更新したら、registry を `generate` し直してください（差分マージはなく全件再生成です）。


## 既存プロジェクトへの後付け

v0.17 以前に `mrag init` で作られたプロジェクトには `kb_information.yaml` がありません。registry に参加させる場合は次のいずれかの方法で追加します：

```bash
# 方法 1: テンプレートから手動で作成
cat > kb_information.yaml <<'EOF'
version: 1
knowledge_base:
  id: kb_legacy
  name: Legacy Knowledge Base
  description: ""
agent_usage:
  tags: []
  best_for: []
  avoid_for: []
  preferred_profiles: [default]
  example_queries: []
EOF
mrag kb-info validate

# 方法 2: mrag init --force で再生成（既存の mrag.yaml / profiles は上書きされるので注意）
mrag init . --force --non-interactive
```

> 注意：`mrag init --force` は `kb_information.yaml` を含むすべてのプロジェクトファイルを上書きします。`mrag.yaml` や `profiles/*.yaml` を編集していた場合は事前にバックアップを取ってください。


## Tips

- `mrag kb-info show` は YAML をそのまま出すので、`mrag kb-info show | yq '.agent_usage.tags'` のように部分抽出のパイプラインに使えます
- `--kb-info-json` 用の JSON はエージェントから組み立てる前提です。`mrag kb-info schema` で取得した JSON Schema をシステムプロンプトに埋め込んでおくと、構造化生成（JSON モード）と相性が良いです
- `agent_usage` は空でも mrag CLI 自体は動きますが、`mrag registry validate` でこのファイルの内容が検査される（path・id・preferred_profiles の整合）ので、registry に参加させる前に `mrag kb-info validate` を回しておくと安全です
- 内容の更新はテキストエディタでの直接編集が一番素直です。`mrag kb-info edit` のようなコマンドは意図的に未実装です（→ 仕様の単純化のため）
- registry 側の集約挙動については [registry-ja.md](./registry-ja.md) を参照してください
