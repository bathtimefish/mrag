[English](README.md) / 日本語

# mrag — Micro RAG

**ローカルファーストの軽量 RAG 検索ランタイム**

mragは小規模なRAGナレッジベースを作成、運用するためのCLIです。ドキュメントのインデックス化から検索までの機能を提供し、ニーズに応じたカスタムRAGを作成するための様々なストラテジーを提供します。AIエージェント向けのskillを使って様々なAIエージェントにナレッジベースを提供できます。

---

## 必要な環境

| コンポーネント | 備考 |
|---|---|
| Python 3.11+ | |
| [Ollama](https://ollama.com) | `ollama serve` が起動済みであること。デフォルトでは Embedding に `bge-m3`、コンテキスト拡張に `gemma4:e2b` を使用 |
| [Qdrant](https://qdrant.tech) | デフォルトの `local` モードではプロセス内 Qdrantを利用。`qdrant.mode: server` を指定したときのみ Docker版 Qdrantが必要 |


## インストール


```bash
git clone https://github.com/bathtimefish/mrag.git
cd mrag
```

Claude Codeなどのエージェントを同ディレクトリ内で起動し、[SETUP.md](./SETUP.md)をエージェントに読ませることでセットアップを実行することが出来ます。

以下は、手動セットアップの手順です。

pythonモジュールのインストールには[uv](https://docs.astral.sh/uv/) の使用を推奨します。

```bash
uv venv
uv pip install -e ".[vaporetto,reranker]"
```

日本語形態素解析（`vaporetto`）と CrossEncoder リランキング（`reranker`）を含む標準構成がセットアップされます。

### vaporetto ネイティブライブラリ

`vaporetto` extra は `apsw`（SQLite 拡張ローディング用）を入れますが、ネイティブ共有ライブラリは別途配置が必要です。

1. [sqlite-vaporetto releases](https://github.com/hotchpotch/sqlite-vaporetto/releases) から、ご利用の OS / アーキテクチャに対応した最新の **`-with-model.tar.gz`** をダウンロード（モデル同梱のバリアントを使ってください）
2. アーカイブを展開して `~/.mrag/extensions/` に共有ライブラリを配置：

   ```bash
   mkdir -p ~/.mrag/extensions
   cp libsqlite_vaporetto.dylib ~/.mrag/extensions/   # macOS
   # cp libsqlite_vaporetto.so ~/.mrag/extensions/    # Linux
   ```

カスタムパスを使う場合は環境変数で指定できます：

```bash
export MRAG_VAPORETTO_LIB=/path/to/libsqlite_vaporetto.dylib
```

`mrag init` 実行時に vaporetto が検出されない場合は trigram トークナイザーに自動フォールバックします。`mrag doctor` で検出状況を確認できます。

### Embedding モデルの取得

```bash
ollama pull bge-m3
```

`bge-m3` は日本語・英語を含む多言語対応のモデルです（1024 次元）。`profiles/default.yaml` を編集すれば Ollama が扱える任意のモデルに差し替え可能です。


## Quick Start

4つのmragコマンドでディレクトリからKBを作成・検索できます：

```bash
mrag init my-kb --non-interactive
cd my-kb
mrag add /path/to/documents --recursive --include '**/*.pdf'
mrag index
mrag search "クエリ"
```

一括投入が不要な場合は、ディレクトリの代わりに単一ファイルを指定します。
ドキュメント追加とインデックス構築は意図的に別の操作になっています。

各ステップの詳細とエージェント連携の手順は [docs/tutorial-ja.md](./docs/tutorial-ja.md) を参照してください。


## CLI コマンド一覧

| コマンド | 役割 |
|---|---|
| `mrag init [PROJECT_DIR]` | プロジェクトを初期化する |
| `mrag add <path>` | 単一ドキュメント、または `--recursive` でディレクトリを追加する |
| `mrag index` | インデックスを構築する |
| `mrag reindex` | インデックスを再構築する |
| `mrag search <query>` | 検索する |
| `mrag eval <query>` | 検索品質を評価する |
| `mrag serve` | HTTP API サーバーを起動する |
| `mrag mcp` | プロジェクトを read-only MCP server として公開する |
| `mrag remove <doc-id>` | ドキュメントを削除する |
| `mrag exclusions add \| list \| restore` | 正本を保持したまま検索対象から除外する |
| `mrag profiles list \| show <name>` | プロファイルの一覧 / 詳細を表示する |
| `mrag kb-info show \| validate \| schema` | ナレッジベース自己記述メタデータを扱う |
| `mrag inspect document \| chunks \| chunk \| sections` | インデックスの内部構造を調査する |
| `mrag registry generate \| validate` | 複数 KB を束ねるレジストリを操作する |
| `mrag extract <file>` | テキスト抽出のみを実行する |
| `mrag show-extracted <doc-id>` | 抽出結果を表示する |
| `mrag export-extracted <doc-id>` | 抽出結果をファイルに書き出す |
| `mrag doctor` | 環境をチェックする |

各コマンドの詳細なオプションは `mrag <command> --help` で確認できます。

ディレクトリ投入では、最初に `mrag add <dir> --recursive --dry-run --json`
で対象を確認し、同じ選択条件から `--dry-run` を外して適用してください。filter、symlink、
duplicate、並列変換、部分成功の詳細は[ディレクトリの再帰追加](./docs/recursive-add-ja.md)
を参照してください。

正本を保持したままナレッジへの寄与を止める場合は、`mrag remove`ではなくdry-runから始める
`mrag exclusions`を使用します。cleanup、復帰、障害時挙動の詳細は
[ドキュメントの検索除外](./docs/document-exclusions-ja.md)を参照してください。


## ドキュメント

機能別の詳細のドキュメントは `./docs/` 下にあります。

### Getting Started

- [tutorial-ja.md](./docs/tutorial-ja.md) — はじめての mrag（init → add → index → search の最短フロー）
- [recursive-add-ja.md](./docs/recursive-add-ja.md) — filterと決定的reportを備えた安全な一括投入

### 検索 (Retrieval)

- [chunking-strategies-ja.md](./docs/chunking-strategies-ja.md) — 4 種類のチャンキングストラテジー
- [retrieval-strategies-ja.md](./docs/retrieval-strategies-ja.md) — 4 種類の検索ストラテジーと融合手法
- [contextual-retrieval-ja.md](./docs/contextual-retrieval-ja.md) — Anthropic 流コンテキスチュアル検索
- [reranking-ja.md](./docs/reranking-ja.md) — CrossEncoder によるリランキング

### 運用 (Operations)

- [document-exclusions-ja.md](./docs/document-exclusions-ja.md) — 正本を保持したドキュメントを検索から可逆的に除外
- [inspect-ja.md](./docs/inspect-ja.md) — インデックスの調査
- [kb-information-ja.md](./docs/kb-information-ja.md) — ナレッジベースの自己記述（`kb_information.yaml`）
- [registry-ja.md](./docs/registry-ja.md) — 複数 KB の集約（`knowledge_registry.yaml`）

### API

- [mcp-ja.md](./docs/mcp-ja.md) — Model Context Protocol server（`mrag mcp`）
- [dify-api-ja.md](./docs/dify-api-ja.md) — Dify External Knowledge API 互換エンドポイント
- [native-api-ja.md](./docs/native-api-ja.md) — mrag ネイティブ REST API

### デプロイ (Deployment)

- [packaging-ja.md](./docs/packaging-ja.md) — PyInstaller による単一バイナリ化（任意の配布手段）


## ライセンス

Copyright (c) 2026 BathTimeFish KK.

Licensed under [GNU Affero General Public License v3.0](./LICENSE).


## 謝辞

mrag は PDF テキスト抽出とテーブル検出に [PyMuPDF](https://github.com/pymupdf/pymupdf) を利用しています。PyMuPDF は [Artifex Software](https://artifex.com) が開発・メンテナンスしており、AGPL-3.0 ライセンスのもとで配布されています。

mrag は SQLite FTS5 による日本語形態素解析に [@hotchpotch](https://github.com/hotchpotch) 氏の [sqlite-vaporetto](https://github.com/hotchpotch/sqlite-vaporetto) を利用しています。

- **sqlite-vaporetto** — `MIT OR Apache-2.0` ライセンス
- **同梱モデル**（`bccwj-suw+unidic_pos+kana.model.zst`、`-with-model` リリースに含まれる）— [BSD-3-Clause](https://opensource.org/license/BSD-3-Clause) ライセンス。[daac-tools/vaporetto-models](https://github.com/daac-tools/vaporetto-models/releases) を出典とします

mrag を sqlite-vaporetto ライブラリやそのモデルと一緒に再配布する場合は、モデルの BSD-3-Clause 著作権表示を配布物に含める必要があります。
