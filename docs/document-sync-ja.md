[English](document-sync.md) / 日本語

# document とファイルの同期

`mrag add` は document の出所を記録しますが、その後ファイルが移動・変更・消失しても
catalog は気づきません。改名したファイルは別の document として二重に追加され、削除した
ファイルは検索に答え続け、編集したファイルは古い本文のままです。`mrag documents sync` は
catalog がそのディレクトリ配下にあると記録している document と、ディレクトリの現在の中身
を比較し、差分を計画として示し、`--apply` でそれを実行します。`mrag documents rebind` は
sync が単独で判断できない場合に、1 つの document を 1 つのファイルへ手で結び付けます。

どちらも「先に決めて、後に書く」コマンドです。計画は `--apply` の有無で変わらず、
`--apply` はそれを item ごとに 1 transaction ずつ実行するだけです。

## 安全な実行フロー

```bash
# sync が何をするかを表示する。何も書かない。
mrag documents sync ./corpus

# 適用する。
mrag documents sync ./corpus --apply

# 適用後、この project で index 済みの全 profile を再構築する。
mrag documents sync ./corpus --apply --index

# 名指しした profile だけを再構築する。
mrag documents sync ./corpus --apply --index --profile default --profile ja

# 自動処理向けに machine-readable な 1 object を出力する。
mrag documents sync ./corpus --apply --json
```

ディレクトリは project 内（project 相対パスで表される）でも、それ以外の場所でも構いません。
project 自身の `data/` と予約済みの `identities/` は拒否されます。`--include`、`--exclude`、
`--hidden`、`--follow-symlinks`、`.mragignore` によるファイルの選択は
[`mrag add --recursive`](recursive-add-ja.md) とまったく同じで、同じ条件なら sync と
再帰追加は同じファイルを見ます。

## 計画が示すもの

catalog がそのディレクトリ配下に置く全 document と、scan が見つけた全ファイルが、
それぞれ 1 つの item になり、action を持ちます。

| action | 条件 | `--apply` がすること |
|---|---|---|
| `add` | document のないファイルで、その内容をどの document も持っていない | 新しい document として登録する |
| `update` | document のファイルが別の内容を持っている | 新しい内容を document の現在の版として保存する。ID は変わらない |
| `move` | document のファイルが消え、その内容を持つ新しいファイルがちょうど 1 つある | document を新しいパスへ向ける。ID は変わらない |
| `exclude` | document のファイルが消え、その内容を持つものがない | document を全 profile から除外する（`reason: file_missing`） |
| `restore` | sync が除外したファイルが同じ内容で戻ってきた | その除外を解除する |
| `noop` | document のファイルが catalog の言う場所に同じ内容である | 何もしない |
| `duplicate` | 新しいファイルの内容を別の document が既に持っている、または複数のファイルが同じ移動の候補 | 何もしない。内容を持つ document を report が名指しする |
| `adopt_candidate` | 旧 catalog から移行されパスを記録していない document の内容を、新しいファイルが持っている | 何もしない。`documents rebind` で明示的に採用する |

移動は 1 対 1 でのみ判定します。同じ内容の消えた document が 2 つ、あるいは同じ内容の
新しいファイルが 2 つある場合、推測で対応付けることはなく、それぞれ `reason: ambiguous_move`
の `duplicate` として報告し、何も変更しません。大文字小文字だけが変わる改名は、大文字小文字
を区別しないファイルシステム上でも move です。

mrag が取り込まない形式のファイル（PDF、Office、HTML）は `unsupported` として数え、
何も残しません。コーパスのディレクトリには document でないファイルも置かれるものなので、
sync はそれを失敗扱いしません。（再帰追加は同じファイルを `failed` と報告しますが、
sync はそれらの追加を頼まれていないので、失敗するものがありません。）

ファイルは存在するが選択から外れた document — filter、隠しディレクトリ、`.mragignore`
によるもの — は `out_of_scope` です。存在しているが見ていないだけなので、見ていない sync
がそれを除外することはありません。

### sync が書く除外と、人が書く除外

`exclude` の item は `mrag exclusions add` と同じ document 除外を全 profile に書き、
`origin: sync`、理由 `source file missing at documents sync` を付けます。sync が解除する
のは自分が書いた除外だけで、ファイルが戻るか、document が move または rebind されたとき
だけです。人が書いた除外（`origin: user`）を sync が解除することはなく、人が除外した
document のファイルが消えても `reason: excluded_by_user` の `noop` です — 何をしたいかは
既に示されています。そのような document の移動では除外も一緒に移ります。

`mrag exclusions list --json` で各ルールの origin を確認できます。

### project 外のディレクトリ

project 外のファイルは、追加時のディレクトリ（*root*）配下として
`identities/external/<root key>/<root からのパス>` で識別されます。root key はディレクトリ
の解決済みパスから導くので、同じディレクトリは常に同じ identity を生み、そのディレクトリ
の sync はその document を直接配置できます。

登録済み root の *親* ディレクトリの sync でも配置できます。root は登録時に祖先ディレクトリ
の key を記録するからです。それ以外のディレクトリの sync では root がその配下にあるかを
判定できないため、そうした root はそのままにし、`summary.unresolvable_roots` に数えます。
祖先の記録が始まる前の旧 mrag で登録された root は、sync が走査するディレクトリの下で
見つかり次第配置され、そのとき祖先が記録されます。

## JSON report と exit code

```json
{
  "schema_version": 1,
  "command": "documents sync",
  "status": "success",
  "applied": true,
  "root": "corpus",
  "directory_binding": "project_relative",
  "summary": {"add": 1, "update": 1, "move": 1, "exclude": 0, "restore": 0, "noop": 4,
              "duplicate": 0, "adopt_candidate": 0, "blocked": 0, "out_of_scope": 0,
              "unsupported": 2, "failed": 0, "unresolvable_roots": 0},
  "items": [
    {"action": "move", "status": "applied", "document_id": "...",
     "source_identity": "corpus/archive/old-notes.txt", "previous_identity": "corpus/notes.txt",
     "content_hash": "...", "reason": null}
  ],
  "scan_issues": [],
  "audit_log": "logs/2026-10-02T09-15-42.113204Z-documents-sync.json",
  "index": {"status": "indexed", "reason": null,
            "profiles": [{"profile": "default", "status": "indexed", "error": null}],
            "next_action": null}
}
```

item の `status` は、計画では `planned` か `reported`、`--apply` 後は `applied`、`failed`、
`cancelled` のいずれかです。sync 自身が定義する唯一の失敗は `ingestion_source_changed`
（`reason: changed_during_sync`）で、計画と書き込みの間にファイルの内容が変わった場合です。
次の実行が拾います。`directory_binding` は `project_relative` か `external_root` です。
`blocked` は常に 0 です。mrag は Markdown とテキストしか取り込まないので、変換待ちの
ものがありません。

`index` object は `--index` 指定時に現れます。計画では再構築する profile を添えて `planned`、
`--apply` 後は `indexed`、`failed`（`next_action` に実行すべき `mrag index --profile`）、
または `reason` が `no_changes`、`no_indexed_profile`（index 済み profile がまだない。
`--profile` で名指しする）、`cancelled` の `skipped` です。再構築ごとに `logs/` へ独自の
ログを書きます。再構築の失敗は report を `partial` にします。

適用した実行は report を `logs/<timestamp>-documents-sync.json` に書きます。ログを
書けなかった場合、変更は有効なまま report は `degraded` になります。

| exit code | 意味 |
|---:|---|
| `0` | 計画を表示した、または failed item なしで適用した。 |
| `3` | 1 件以上の item が失敗、scan issue が報告された、再構築が失敗した、または監査ログを書けなかった。他の item は有効。 |
| `2` | 使用方法が不正: ディレクトリが存在しない／ファイルである／`data/` か `identities/`、`--index` なしの `--profile`、未知の profile、`mrag catalog migrate-identities` が先に必要な catalog。 |
| `1` | 初期化済み project の中ではない。 |
| `130` | 中断された。下記参照。 |

## 実行を止める

適用中の sync は `SIGINT`（Ctrl-C）または `SIGTERM` — `timeout`、cron、systemd、
service wrapper が送るもの — を受け取ると、次の item の境界で止まります。適用済みの
item は保たれ、残りは `cancelled` として報告され、report は `status: "cancelled"`、
監査ログは書かれ、exit code は 130 です。もう一度実行すれば残りが完了します。2 回目の
シグナルは report なしで即座にプロセスを終了します。

`mrag add --recursive` もファイル間で同じ契約に従い、到達しなかったファイルを `cancelled`
として報告します。

## 1 つの document を手で結び付ける

```bash
# 結び付けが何をするかを表示する。
mrag documents rebind <DOCUMENT_ID> ./notes/renamed.md

# 実行する。
mrag documents rebind <DOCUMENT_ID> ./notes/renamed.md --apply
```

rebind は sync が手を付けない場合のためのものです。移動 *かつ* 変更されたファイル、
`adopt_candidate`、自分で解決できる `ambiguous_move`。名指ししたファイルへ document を
向け、ID を保ちます。異なる内容のファイルは document の新しい内容になります — 両端を
自分で名指ししたので `--force` は不要です。その document に対する sync 自身の除外は
解除され、人の除外は残ります。

別の document が既にそのパスにある（`rebind_target_taken`）か、既にその内容を持っている
（`rebind_content_held_by_other_document`）場合は exit 1 で拒否し、report がその document
を名指しします（`held_by`）。document を自分自身のファイルへ結び付けるのは `noop` です。
使用方法の誤り — 存在しないファイル、ディレクトリ、mrag が取り込まない形式、`data/` や
`identities/` 配下のファイル — は exit 2 です。

適用した rebind は `logs/<timestamp>-documents-rebind.json` を書きます。パスは更新できたが
その後に新しい内容を読めなかった場合、document は新しいパスに以前の内容のまま残り、
コマンドは `status: "partial"` で exit 3 を返します。もう一度実行すれば完了します。

どちらのコマンドも単独では index を再構築しません。sync の `--index` か `mrag index` が
行います。
