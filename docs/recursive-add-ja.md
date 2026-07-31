# ディレクトリの再帰追加

ディレクトリツリー内の各ファイルを独立したmragドキュメントとして登録する場合は、
`mrag add <DIRECTORY> --recursive`を使用します。再帰追加が行うのは抽出とcatalog登録までで、
`mrag index`を暗黙には開始しません。

対応するsource fileはplain text（`.txt`）、Markdown（`.md`）、`.markdown`です。
関係のない形式も含むtreeではinclude ruleを指定してください。

mragはドキュメント変換を行いません。変換engineが必要な形式 — `.pdf`、`.html`、
`.htm`、`.docx`、`.pptx`、`.xlsx` — は`requires external conversion to Markdown`
として`failed` itemにreportされます。先に変換し（PDFはdocling、Office形式は
MarkItDown）、生成されたMarkdownを追加してください。変換できないfileが1件あっても
実行は止まりません。残りのsourceは取り込まれ、exit codeは3になります。

## 安全な実行フロー

最初に対象をpreviewします。globはshellではなくmragにそのまま渡すためquoteしてください。

```bash
# mrag.dbとdata/documents/を変更せずに対象を確認
mrag add /path/to/documents --recursive --dry-run --json \
  --include '**/*.md' --include '**/*.markdown' --include '**/*.txt' \
  --exclude 'drafts/'

# 確認した同じ条件を適用
mrag add /path/to/documents --recursive --json \
  --include '**/*.md' --include '**/*.markdown' --include '**/*.txt' \
  --exclude 'drafts/'

# indexは別の明示的なstage
mrag index
```

directoryを`--recursive`なしで指定すると拒否されます。`--include`、`--exclude`、
`--hidden`、`--follow-symlinks`、`--dry-run`、`--strict`などのdirectory専用optionも、
単一file sourceには指定できません。

## pathの選択

matchには、指定したsource rootからの相対pathをPOSIX形式へ正規化して使用します。
candidateとreport itemはこの相対pathでsortされるため、抽出を並列化しても同じtreeから
決定的なreportが得られます。

選択順序は次のとおりです。

1. 1つ以上の`--include`があれば、いずれかにmatchするpathだけを残す。
2. `--exclude`にmatchするpathを必ず除外する。
3. source root直下の`.mragignore`を記述順に適用する。後続の`!pattern`は、先行する
   `.mragignore` ruleによる除外を解除できるが、`--exclude`は上書きできない。

`--include`と`--exclude`はrepeatableです。`*`は1つのpath component内、`**`はdirectoryを
またいでmatchし、`?`は1文字にmatchします。末尾`/`はdirectory subtreeを表し、`/`を含まない
patternは任意の深さにある同名pathへmatchします。

`.mragignore`の例：

```gitignore
# 生成物とdraftを除外
generated/
drafts/

# review済みの1件だけ戻す
!drafts/approved.md
```

`.mragignore`は1 MiB以下のUTF-8 regular fileである必要があり、symlinkは使用できません。
ignore file自体は取り込まれません。

## traversalの安全策

- dotで始まるpath componentはdefaultでskipします。含める場合だけ`--hidden`を指定します。
- symlinkはdefaultでskipします。信頼できるtreeでのみ`--follow-symlinks`を指定してください。
  mragはdirectory cycleを検出し、同じcanonical file targetを最大1回だけ取り込みます。
- projectの`data/` subtreeはsymlink aliasを含め常にskipします。その配下をsource rootにする
  指定も拒否し、mrag自身の保持成果物を再取り込みすることを防ぎます。
- regular file以外は無視します。

## duplicateと置換

content identityにはSHA-256を使用します。登録済みfileは`skipped_duplicate`としてreportされ、
errorにはなりません。同一contentを再抽出する必要がある場合だけ`--force`を指定してください。
mragは既存document IDを維持して抽出recordを置換します。

準備後のdocumentは直列化されたwrite boundaryを通して永続化し、最終reportは相対pathの
安定順を維持します。

## JSON reportとexit code

自動処理では`--json`を使用してください。summaryとcandidateまたはscan issueごとのitemを
含む1つのobjectを出力します。

```json
{
  "schema_version": 1,
  "command": "add",
  "status": "partial",
  "summary": {"added": 4, "skipped": 2, "failed": 1},
  "items": [
    {"source": "manuals/a.md", "status": "added", "document_id": "...", "error": null},
    {"source": "manuals/b.md", "status": "skipped_duplicate", "document_id": "...", "error": null},
    {"source": "manuals/c.pdf", "status": "failed", "document_id": null,
     "error": {"code": "prepare_failed",
               "message": "Unsupported file type: .pdf (requires external conversion to Markdown)"}}
  ],
  "index_started": false,
  "recursive": true,
  "dry_run": false
}
```

| exit code | 意味 |
|---:|---|
| `0` | failed itemなし。addedとduplicate skipの両方を含む場合がある。 |
| `3` | defaultのbest-effort modeで部分成功。成功したdocumentは登録済みのまま。 |
| `1` | 全itemが失敗、または`--strict`指定中に1件以上失敗。 |
| `2` | CLIの使用方法が不正。 |

`--strict`が変更するのはexit codeであり、投入をatomicにはせず、先に成功したdocumentを
rollbackしません。失敗原因の修正後に再実行すると、`--force`を指定しない限り以前の成功分は
安全に`skipped_duplicate`になります。

ingestion reportを確認してから`mrag index`を実行してください。add → index → searchの全体像は
[チュートリアル](./tutorial-ja.md)を参照してください。
