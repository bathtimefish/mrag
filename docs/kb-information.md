# Self-describing knowledge bases — `kb_information.yaml`

This document covers **`kb_information.yaml`**, the metadata file that ships with every mrag project, and the `mrag kb-info` commands that generate and validate it.

`kb_information.yaml` is a metadata file that describes "what kind of knowledge the knowledge base contains" and "what kinds of questions it handles well". The mrag CLI itself does **not** read this file during search or indexing. Its purpose is to give AI agents a summary of the knowledge base.


## File structure (version: 1)

```yaml
version: 1                              # schema version (currently only 1)

knowledge_base:                         # required section
  id: kb_device                         # slug identifier (lowercase + digits + _)
  name: Device Knowledge Base           # human-readable display name
  description: ""                       # free-form description (may be empty)

agent_usage:                            # optional section (defaults apply if omitted)
  tags: []                              # classification tags for this KB
  best_for: []                          # examples of questions this KB handles well
  avoid_for: []                         # examples of questions this KB should not answer
  preferred_profiles: [default]         # list of recommended profile names
  example_queries: []                   # representative queries
```

What each section is for:

- **`knowledge_base`** — identity information (id / name / description). The same values are copied into the registry
- **`agent_usage`** — the input an agent uses when deciding whether to pick this KB. mrag still works with everything empty, but filling it in improves selection accuracy for LLM agents


## Generation via `mrag init`

`mrag init` generates `kb_information.yaml` in one of three modes, depending on how you invoke it.

| Invocation | Mode | What it produces |
|---|---|---|
| `mrag init <dir>` | Interactive | Prompts for name / kb_id / description; `agent_usage` is left as an empty template |
| `mrag init <dir> --non-interactive` | Non-interactive | Derives name / kb_id from arguments or the cwd name; `agent_usage` is left as an empty template |
| `mrag init <dir> --kb-info-json PATH` | JSON input | Generates a fully populated file, including `agent_usage`, from a JSON file |

> Note: in Interactive and Non-interactive modes, the `agent_usage` fields are not prompted — an empty template is written out, and you can edit it by hand later. To populate `agent_usage` from the start, use `--kb-info-json`.


## `--kb-info-json` — bulk generation by an agent

Use this mode to produce a fully populated `kb_information.yaml`, including `agent_usage`. It is designed for the case where an LLM agent bootstraps a new mrag project.

```bash
# Fetch the JSON Schema (helps an agent construct the input JSON)
mrag init --print-kb-info-schema > schema.json

# The same schema is also available via the kb-info subcommand
mrag kb-info schema > schema.json

# Initialize from a JSON file
mrag init my-kb --kb-info-json ./kb-info.json
```

The JSON file matches the `kb_information.yaml` schema, with **`project.name` added** at the top level:

```json
{
  "project": {
    "name": "my-kb"
  },
  "knowledge_base": {
    "id": "kb_device",
    "name": "Device Knowledge Base",
    "description": "IoT device development notes centered on Arduino and SIM7080G"
  },
  "agent_usage": {
    "tags": ["arduino", "sim7080g", "mqtt"],
    "best_for": ["Troubleshooting SIM7080G"],
    "avoid_for": ["Spec document review"],
    "preferred_profiles": ["default"],
    "example_queries": ["SIM7080G MQTT publish stops after several hours"]
  }
}
```

`project.name` is consumed by `mrag.yaml` and is not written into `kb_information.yaml`. If JSON validation fails, mrag prints per-field errors and exits 1 (no partial file is left behind).


## The `mrag kb-info` subcommands

Run these from inside a project directory (where `kb_information.yaml` lives).

```bash
# Print the YAML as-is (handy for pipes and visual inspection)
mrag kb-info show

# Validate the file against the schema
mrag kb-info validate

# Print the JSON Schema for --kb-info-json input
mrag kb-info schema
```

What each subcommand does:

- **`show`** — sends the contents of `kb_information.yaml` to stdout. No reformatting is done, so it's the right command when you want to see the file exactly as written.
- **`validate`** — validates against the Pydantic schema. On success it prints a summary (`knowledge_base.id` / `name` / `preferred_profiles`, plus counts for the `agent_usage` lists). On schema violations it exits 1 with per-field errors.
- **`schema`** — prints the JSON Schema for the `--kb-info-json` input. The output is exactly the same as `mrag init --print-kb-info-schema`.


## Field rules

### Slug rule for `knowledge_base.id`

The value must match the regular expression `^[a-z0-9_]+$`. A violation causes exit 1 with a suggestion:

| Input | Result |
|---|---|
| `kb_device` | OK |
| `kb_device_v2` | OK |
| `KbDevice` | NG (uppercase) |
| `kb-device` | NG (hyphen) |
| `KB-Device!` | NG → suggestion: `'kb_device'` |
| `""` | NG (empty) |

> Important: `id` is the value used to uniquely identify a KB inside a registry. If two KBs under the same root share an `id`, `mrag registry generate` exits 1 (→ [registry.md](./registry.md)). Keep the slug short and descriptive of the knowledge base's content for easier operation.

### `preferred_profiles` normalization

If `preferred_profiles` is an empty list or omitted, it is normalized **in-memory at load time** to `["default"]` (this is done by a Pydantic model validator). `mrag kb-info validate` does not write back to the file, so the normalized `["default"]` reaches disk only when a process that writes the file — such as `mrag init` — runs. To list multiple profiles explicitly, just write them out:

```yaml
agent_usage:
  preferred_profiles:
    - default
    - hybrid-rerank
```

The expected behavior is that an agent uses the first profile by default and falls back to the rest only in special cases. `registry validate` checks that each listed profile actually exists at `profiles/<name>.yaml`.


## How to fill in `agent_usage`

`agent_usage` is the material an agent uses to decide "out of the KBs I have, which one should I use right now?". You can leave it empty and mrag still works, but it is worth filling in when several KBs are aggregated into a registry.

```yaml
agent_usage:
  tags: [iot, mqtt, m5stack]
  best_for:
    - SIM7080G AT commands and power management
    - Firmware flashing procedures for Arduino
  avoid_for:
    - Spec document review
    - General C++ tutorials
  preferred_profiles: [default]
  example_queries:
    - SIM7080G MQTT publish stops after several hours
    - I2C device is not recognized on Arduino Nano
```

How to think about each field:

- **`tags`** — keywords for the agent to filter KBs. Short, machine-friendly tokens work best
- **`best_for`** / **`avoid_for`** — one or a few lines, in natural language, describing what the KB handles well or poorly. Agents read these when applying their selection policy
- **`example_queries`** — two to five representative queries. They give the agent a basis for "is my query close to one of these?"


## Relationship to the registry

When several mrag projects are aggregated, **`mrag registry generate`** rolls up each project's `kb_information.yaml` into `knowledge_registry.yaml`:

- `knowledge_base.id` / `name` / `description` → copied verbatim into the same fields on the registry
- `agent_usage.tags` / `best_for` / `avoid_for` / `preferred_profiles` / `example_queries` → copied into the matching fields under `knowledge_bases[]`
- The `path` field is the only one computed on the registry side (→ [registry.md](./registry.md))

After updating `kb_information.yaml`, re-run `generate` (there is no diff/merge mode — the registry is always regenerated in full).


## Adding the file to an existing project

Projects created with `mrag init` before v0.17 do not have a `kb_information.yaml`. To make them eligible for the registry, add it with one of these approaches:

```bash
# Approach 1: write the template by hand
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

# Approach 2: regenerate via mrag init --force (overwrites existing mrag.yaml and profiles)
mrag init . --force --non-interactive
```

> Caution: `mrag init --force` overwrites every project file, including `kb_information.yaml`. If you've edited `mrag.yaml` or `profiles/*.yaml`, take a backup first.


## Tips

- `mrag kb-info show` outputs YAML directly, so you can feed it to pipelines like `mrag kb-info show | yq '.agent_usage.tags'` for partial extraction.
- The JSON for `--kb-info-json` is meant to be built by an agent. Embedding the JSON Schema from `mrag kb-info schema` in a system prompt pairs nicely with structured generation (JSON mode).
- `agent_usage` may be empty and the mrag CLI itself will still run, but `mrag registry validate` inspects this file's contents (`path`, `id`, `preferred_profiles` consistency), so running `mrag kb-info validate` before joining a registry is a safe habit.
- Updating the file is most straightforward via direct edits in a text editor. A `mrag kb-info edit` command is deliberately not implemented (to keep the spec simple).
- For details on registry aggregation, see [registry.md](./registry.md).
