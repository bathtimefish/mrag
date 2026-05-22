# Aggregating multiple knowledge bases — `mrag registry`

This document covers the `mrag registry` command group, used to roll up several mrag projects into a single registry.

The **Knowledge Registry** is a mechanism for aggregating multiple mrag projects that sit directly under a common parent directory, and describing them in a single metadata file called `knowledge_registry.yaml`. It is designed to be used by AI agents (Agentic RAG workflows) as an index for deciding "which knowledge bases are available right now, and which one should I search?".

The mrag CLI itself does **not** read `knowledge_registry.yaml` during search or indexing. The registry is a file consumed by external agents. The roles of the three YAML files are:

| File | Consumer | Role |
|---|---|---|
| `mrag.yaml` | mrag CLI | Per-project runtime configuration |
| `kb_information.yaml` | External agents / users | Metadata for a single knowledge base (→ [kb-information.md](./kb-information.md)) |
| `knowledge_registry.yaml` | External agents / users | **Discovery / selection index across multiple knowledge bases** |


## The two subcommands

| Subcommand | When to use |
|---|---|
| `mrag registry generate <root_dir>` | Build a `knowledge_registry.yaml` from the projects directly under the root directory |
| `mrag registry validate <registry_path>` | Verify that an existing registry is consistent with its schema and the actual filesystem layout |


## Directory layout

`mrag registry` assumes a layout where mrag projects sit **directly under** a single parent directory (the "root"):

```text
my-kb/                               ← root (where the registry lives)
├── knowledge_registry.yaml          ← generated file
├── kb-device/                       ← individual mrag project
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


## `mrag registry generate` — generate the registry

```bash
# Standard usage (writes knowledge_registry.yaml under the root)
mrag registry generate ./knowledges

# Specify an output path
mrag registry generate ./knowledges --output ./meta/knowledge_registry.yaml

# Preview the YAML on stdout without writing to disk
mrag registry generate ./knowledges --dry-run
```

What it does:

1. Scans the immediate subdirectories of the root
2. Checks each subdirectory for both `kb_information.yaml` and `mrag.yaml`
3. Reads the required fields out of `kb_information.yaml` and builds a registry entry
4. Checks all entries for duplicate `id`
5. Writes to the `--output` path if given, otherwise to `<root>/knowledge_registry.yaml`

### Skip vs. error policy

| Situation | Behavior |
|---|---|
| Subdirectory has no `kb_information.yaml` | warn + skip |
| No `mrag.yaml` (not an mrag project) | warn + skip |
| `kb_information.yaml` is structurally invalid | warn + skip |
| Zero matches | **exit 1** (catches typos and nested-layout mistakes early) |
| Duplicate `id` | **exit 1** (no file is written) |

Warnings are emitted on stderr. stdout is reserved for the pipe-friendly payload (the YAML produced by `--dry-run`).


## How `path` is resolved

The `knowledge_bases[].path` field in the registry is a POSIX-style relative path, **anchored at the directory that contains the registry file itself**.

```yaml
knowledge_bases:
  - id: kb_device
    path: ./kb-device        # standard case — kb-device/ sits directly under the root
  - id: kb_legacy
    path: ../legacy/kb-old   # upward traversal can appear when --output writes elsewhere
```

The design intent is **portability**. You can `scp` or `git push` the entire root directory to another machine and the registry will still resolve correctly, because every path is described relative to the registry itself.

An agent can reach the correct project directory by running `cd $(dirname knowledge_registry.yaml) && cd <kb.path>`.

> Note: when `--output` writes the registry to a sibling or parent directory, `path` values may contain `../`. This is expected, not an error.


## `mrag registry validate` — validate the registry

```bash
# Human-readable validation
mrag registry validate ./knowledges/knowledge_registry.yaml

# JSON output (for agents)
mrag registry validate ./knowledges/knowledge_registry.yaml --json
```

Validation steps:

1. **Read the file** — if the registry is missing or fails to parse as YAML, exit 1 immediately
2. **Schema validation** — if the data does not match the Pydantic schema, exit 1 immediately
3. **Consistency checks** — collect **all** issues below, then exit 1 if the count is greater than zero
   - Duplicate `id`
   - `knowledge_bases[].path` does not exist, or is not a directory
   - `<path>/mrag.yaml` does not exist
   - `<path>/kb_information.yaml` does not exist
   - A profile listed in `preferred_profiles` is not present at `<path>/profiles/<name>.yaml`

> Note: fatal errors (missing file, YAML parse failure, schema mismatch) exit immediately, while all other consistency issues are collected and reported together before exit. A single run surfaces every problem, so an agent can plan all fixes in one turn.


### Stable issue keys

Each issue in the `--json` output carries a stable string in its `issue` field. Agents can branch on this value:

| Key | Meaning |
|---|---|
| `path_not_found` | `knowledge_bases[].path` does not exist, or is not a directory |
| `mrag_yaml_not_found` | `<path>/mrag.yaml` is missing |
| `kb_information_yaml_not_found` | `<path>/kb_information.yaml` is missing |
| `preferred_profile_not_found` | `<path>/profiles/<name>.yaml` is missing |
| `duplicate_id` | `knowledge_bases[].id` is duplicated against another entry |

### JSON output schema

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


## `agent_instructions` — guidance for agents

The registry contains a section that describes the selection policy and the search command template that agents should follow. Defaults are filled in at generation time. Edit it to match how you want agents to behave (run `mrag registry validate` afterward to re-check consistency).

```yaml
agent_instructions:
  selection_policy: >
    Select the most relevant knowledge base based on the user's question.
    If the question spans multiple domains, search multiple knowledge bases.

  search_command_template: |
    cd {path}
    mrag search "{query}" --profile {profile} --json
```

The agent side is expected to substitute `{path}` / `{query}` / `{profile}`. The mrag CLI itself does not interpret these strings.


## Recommended pattern — generate, then validate

When you drive the registry from an agent, or update it from CI, **run generation and validation as a pair** for safety:

```bash
# Step 1: generate the registry
mrag registry generate ./knowledges

# Step 2: check schema + filesystem consistency
mrag registry validate ./knowledges/knowledge_registry.yaml --json | jq '.issue_count'
```

- Generation warnings go to stderr, so you can capture them as logs
- If `issue_count` from validation is 0, agents can safely `cd` into each project directory
- Re-run `generate` whenever knowledge bases are added or removed, profiles are added, or names change (it always regenerates the whole file — there is no diff/merge mode)


## Tips

- `--dry-run` writes the YAML to **stdout**, so you can pipe surveys like `mrag registry generate ./knowledges --dry-run | yq`. Warnings remain isolated on stderr.
- The exit 1 for "zero matches" is intentional — it catches a wrong root directory, or a layout where projects are nested one level deeper than expected. Follow the tip in the error message and run `mrag init <root>/<kb-name>`.
- Manual edits to the registry are expected. After tweaking `agent_instructions`, run `mrag registry validate` before relying on the new version.
- To create an empty registry deliberately, use `touch <root>/knowledge_registry.yaml` (there is no `--allow-empty` flag as of v0.18.0).
- For per-knowledge-base metadata, see [kb-information.md](./kb-information.md).
