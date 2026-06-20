# 単一バイナリへのパッケージ化（PyInstaller）

このドキュメントでは、mrag を **PyInstaller で単一の実行ファイルにパッケージ化**して配布する方法を解説します。

これは**任意の配布手段**です。mrag の標準的な使い方は従来どおり `uv pip install -e .`（あるいは `pip install`）であり、本手順はそれを置き換えるものではありません。パッケージ化は「Python 環境を用意せずに mrag を配りたい / 動かしたい」場合の選択肢として用意されています。本手順は mrag の実行時ソースを一切改変せず、**インストール済みのパッケージをそのまま1つのバイナリに固める**だけです。

> 補足：mrag は PDF 抽出に PyMuPDF（AGPL）を使うため、配布物も AGPL-3.0 の条件下に置かれます。バイナリを再配布する場合はライセンス表記に注意してください。


## 前提

- 対象 OS 上で mrag が `uv pip install -e .` 等でインストール済みであること
- PyInstaller がインストールされていること

```bash
uv pip install pyinstaller
```

> **重要：PyInstaller はクロスコンパイルできません。** macOS arm64 向けバイナリは macOS arm64 上で、Linux 向けは Linux 上で、というように**配布したいプラットフォームごとに実機（または同等の CI）でビルド**する必要があります。


## ビルド手順

リポジトリ同梱のビルドスクリプト `packaging/build.sh` を使います。

```bash
packaging/build.sh                 # LEAN onefile（reranker 抜き・既定）
packaging/build.sh --with-reranker # フル（sentence-transformers + torch を同梱）
packaging/build.sh --onedir        # フォルダ形式（起動が速い。後述）
packaging/build.sh --help
```

ビルドが終わると、構成に応じて次の場所に成果物が出力されます。

- onefile（既定）：`dist/mrag` … 単一の実行ファイル
- onedir（`--onedir`）：`dist/mrag/` … フォルダ。中の `dist/mrag/mrag` を起動する

スクリプトは内部で、PyInstaller が静的解析で取りこぼしやすい依存（PyMuPDF / qdrant-client / uvicorn / FastAPI / pydantic / apsw）を自動収集し、mrag の動作に必須な次の2点を必ず付与します。

- `--copy-metadata mrag` … バージョン情報（`importlib.metadata`）の同梱。これが無いと起動時に即クラッシュします
- `--collect-data mrag` … `schema.sql` などのパッケージ同梱データの収集。これが無いと `mrag init` が失敗します

これらは mrag 側のコードがパッケージ化に耐えるようハードニング済み（v0.21.2）であることと合わせて、追加の手作業なしで動作します。


## モードの選び方

### reranker の有無

| モード | 同梱内容 | 目安サイズ | 用途 |
|---|---|---|---|
| LEAN（既定） | torch / sentence-transformers を**除外** | 約 60〜200MB | リランキングを使わない通常運用 |
| フル（`--with-reranker`） | CrossEncoder リランカー一式を同梱 | プラットフォーム依存（後述） | リランキングまで単一バイナリで完結させたい場合 |

リランキング（`rerank.enabled: true`）はオプショナル機能です。使わないなら LEAN を選んでください。フルビルドは PyTorch を丸ごと含むためサイズが大きくなり、ビルド時間も起動時間も伸びます。`--with-reranker` を使う前に、対象環境に `uv pip install -e ".[reranker]"` で reranker extras が入っている必要があります。

フルビルドのサイズは **同梱される torch のビルドに大きく依存**します。実測では macOS arm64（CUDA を含まない CPU/MPS 版 torch）の onefile で **約 231MB** でした。一方、**Linux で CUDA 対応 torch** を含む場合は CUDA ランタイムを丸ごと抱えるため **1GB を超える**ことがあります。リランカーのモデル重み自体はバイナリに含まれず、初回検索時に HuggingFace からダウンロードされます。

### onefile と onedir

これは「単一ファイルか」と「起動の速さ」のトレードオフです。onefile は文字どおり1ファイルですが、**起動のたびに自分自身を一時ディレクトリへ展開する**ため、起動が遅くなります。onedir は展開が不要なぶん起動がほぼ瞬時です。

本リポジトリの LEAN ビルドを macOS arm64 で計測した実測値：

| 構成 | サイズ | 初回起動 | 2回目以降 |
|---|---|---|---|
| onefile | 58MB（単一ファイル） | 約 7 秒 | **毎回 約 7 秒** |
| onedir | 136MB（フォルダ） | 約 7 秒 | **約 0.6 秒** |
| （参考）venv 実行 | — | — | 約 0.55 秒 |

onefile は毎回展開コストがかかるため、何度も呼び出す CLI 用途では起動の遅さが体感に効きます。onedir の2回目以降は venv 実行とほぼ同等です。

> **指針：mrag を繰り返し実行する用途なら `--onedir` を推奨**します。「とにかく1ファイルで配りたい」「実行頻度が低い」なら onefile が手軽です。


## 動作確認

ビルド後は必ずスモークテストしてください。

```bash
# onefile の場合
./dist/mrag --version          # → 0.21.2
./dist/mrag --help

# onedir の場合
./dist/mrag/mrag --version
```

Python 環境のない一時ディレクトリで初期化まで通すと、より確実です。

```bash
mkdir /tmp/kb-test && cd /tmp/kb-test
/path/to/dist/mrag init        # mrag.db とプロジェクト一式が生成されれば成功
```


## 注意点

- **クロスコンパイル不可**：配布対象の OS / アーキテクチャごとにビルドが必要です。
- **vaporetto トークナイザはバイナリに含まれません**：日本語形態素トークナイザの共有ライブラリは実行時に `~/.mrag/extensions` からロードされる外部プラグインであり、パッケージ化とは独立しています。これは意図的な設計で、バイナリに固めても差し替え可能なまま保たれます。vaporetto を使う場合は、配布先にも従来どおり拡張を配置してください（`apsw` も同梱されている必要があり、スクリプトは存在すれば自動で含めます）。
- **外部サービス依存は残ります**：mrag は埋め込み生成に Ollama、ベクトル検索に Qdrant を使います。これらはバイナリには含まれず、実行時に別途必要です（パッケージ化は mrag 本体を固めるだけで、依存サービスを内包するものではありません）。
- **バージョン同期**：`pyproject.toml` の `version` と `mrag/__init__.py` の `_FALLBACK_VERSION` は一致させてください。バイナリのバージョン表示はこの値に基づきます。
- **AGPL-3.0**：PyMuPDF に起因して mrag は AGPL-3.0 です。バイナリ再配布時はライセンス順守に注意してください。


## Windows でのビルド

`build.sh` は Unix シェル前提です。Windows では PowerShell から同等のコマンドを実行します（`--add-data` の区切り文字が `;` になる点に注意）。

```powershell
$collect = @('fitz','pymupdf','qdrant_client','uvicorn','fastapi','pydantic','apsw') |
    ForEach-Object { '--collect-all', $_ }
pyinstaller --onefile --name mrag --clean --noconfirm `
  --copy-metadata mrag --collect-submodules mrag --collect-data mrag `
  @collect `
  --exclude-module torch --exclude-module sentence_transformers `
  packaging\mrag_entry.py
```

フルビルドにする場合は `--exclude-module` を外し、`$collect` に reranker 系パッケージを追加してください。


## トラブルシュート

- **起動時に `PackageNotFoundError: mrag`** → `--copy-metadata mrag` が抜けています。スクリプト経由なら自動付与されます。
- **`mrag init` で schema.sql が見つからない** → `--collect-data mrag` が抜けています。同上。
- **`ModuleNotFoundError`（fitz / uvicorn のプロトコル等）** → 対象パッケージが収集されていません。スクリプトの `add_collect_if_present` に当該パッケージを追加してビルドし直してください。
- **バイナリが巨大／起動が遅い** → reranker を含めていないか確認し（LEAN を使う）、繰り返し利用なら `--onedir` を選んでください。
