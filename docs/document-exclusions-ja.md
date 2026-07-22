# ドキュメントの検索除外

正本と抽出成果物を監査・復帰用に保持したまま、特定ドキュメントのナレッジ検索への寄与を
止める場合は`mrag exclusions`を使用します。対象は安定したdocument IDであり、生成された
chunk IDやfilenameではありません。

## 除外と削除を選ぶ

| 要件 | 操作 | 正本／抽出成果物 | 検索状態 |
|---|---|---|---|
| documentを保持したままナレッジとして使用しない | `exclusions add` | 保持 | 即時block |
| 除外documentを再び利用可能にする | `exclusions restore`後に`index` | 保持 | 明示index後だけ検索可能 |
| mrag document自体を削除する | `remove --force` | 削除 | documentが存在しない |

exclusionは永続的な論理policyと検索用派生dataの物理cleanupを組み合わせた可逆操作です。
`remove --force`はdocument catalogと保持sourceに対するapplication levelの破壊的操作です。

## document IDを取得する

```bash
# 検索結果から取得
mrag search "<unique query>" --json \
  | jq '.results[] | {document_id, filename}'

# SQLiteから取得
sqlite3 mrag.db "SELECT id, filename, status FROM documents ORDER BY filename;"

# mrag serve実行中にNative APIから取得
curl -s http://127.0.0.1:8000/api/v1/documents
```

ここで得たdocument IDを指定します。chunk IDは生成された1つの検索単位だけを識別するため、
exclusionの対象には使用できません。

## exclusionをpreviewして適用する

`add`は`--force`を付けない限りdry-runです。

```bash
# 対象chunk、variant、FTS row、Qdrant pointをpreview
mrag exclusions add --document-id <DOCUMENT_ID>

# machine-readableなpreview
mrag exclusions add --document-id <DOCUMENT_ID> --json

# 現在および将来の全profileへ適用（推奨default）
mrag exclusions add --document-id <DOCUMENT_ID> \
  --reason "obsolete knowledge" --force

# 1つのprofileだけに限定
mrag exclusions add --document-id <DOCUMENT_ID> \
  --profile contextual --reason "not valid for this profile" --force
```

任意の監査理由は1000文字までです。profileごとに意図的に異なるknowledgeを公開する場合以外は、
全profile scopeを使用してください。scopeを重複させず、profile scopeのpolicyを全profileへ
置き換える場合は、先にlistして既存policyをrestoreします。

### `--force`が行う処理

policyとcleanupは意図的に次の順序で処理されます。

1. exclusion policyをSQLiteへcommitし、即時に検索の最終barrierとする。
2. 対象documentのFTS rowを削除する。
3. Qdrant pointを削除する。
4. vector cleanup成功後に、chunk、variant、profile別document index stateをSQLiteから削除する。

document row、original file、抽出text／Markdown、exclusion audit recordは保持します。CLI、
HTTP API、MCPは同じpolicyを適用します。keyword検索はSQLite anti-filter、vector検索はQdrantの
document filterと最終SQLite hydration filterの両方で除外します。

`mrag index`と`mrag reindex`はprovider構築、chunking、augmentation、embeddingより先に
exclusionを解決するため、再indexで対象documentが意図せず復活することはありません。

### degraded cleanupから復旧する

Qdrant cleanupを完了できない場合、commandはexit `3`、JSONでは`status: "degraded"`を返します。
policyは有効なままで、全検索pathはfail-closedを維持します。FTS rowはすでに削除済みですが、
Qdrant point削除を安全にretryするためchunk／variant metadataを保持します。

Qdrantを復旧した後、同じforced add commandを繰り返してください。

```bash
mrag exclusions add --document-id <DOCUMENT_ID> \
  --reason "obsolete knowledge" --force
```

有効なexclusionへ`--force`を再適用するとpending cleanupをreconcileします。cleanupがexit `3`を
返したことを理由にexclusionをrestoreしないでください。

## exclusionを監査する

```bash
# active policyだけ
mrag exclusions list

# restore／revoke済みのaudit recordも含む
mrag exclusions list --all
mrag exclusions list --all --json
```

`add`または`list`が返すexclusion IDを保持してください。これはpolicyのIDであり、document IDとは
異なります。

## documentを復帰する

`restore`もdry-run-firstです。policyをrevokeする前に残存する派生成果物をpurgeします。

```bash
# preview
mrag exclusions restore <EXCLUSION_ID>

# residual cleanup成功後にpolicyをrevoke
mrag exclusions restore <EXCLUSION_ID> --force

# 保持documentを明示的に再構築
mrag index --document-id <DOCUMENT_ID>

# profile scopeをrestoreした場合は対応profileを再構築
mrag index --document-id <DOCUMENT_ID> --profile contextual
```

restoreはembedding providerを暗黙に呼びません。明示indexが成功するまで、保持documentは
index対象ではありますが検索できません。残存Qdrant cleanupが失敗するとpolicyは有効なままです。
Qdrantを修復して`restore --force`をretryしてください。

## 物理削除とsecure erase

`mrag remove <DOCUMENT_ID>`で削除をpreviewし、`mrag remove --force <DOCUMENT_ID>`で
document record、保持成果物、検索用派生record、exclusion historyを削除します。

どちらの操作もstorage media上のsecure eraseを保証しません。複製log、operator管理cache、
filesystem snapshot、external backupも破棄する必要がある場合は、deploymentのdata retention
policyに従って各storageを別途処理してください。
