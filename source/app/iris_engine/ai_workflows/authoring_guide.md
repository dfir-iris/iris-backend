# Writing IRIS AI workflows as JSON

This guide is for people and for LLMs. It explains how to write an IRIS AI workflow, or a saved block, as a JSON document that can be imported with **Settings → AI workflows → Import**. You can also import with `POST /api/v2/ai-workflows/import` (workflows) or `POST /api/v2/ai-workflows/blocks/import` (blocks). The *Live catalogue* section at the end lists what this instance offers: node types and their defaults, events, tools and keystore names. Use only names that appear there.

## 1. Documents

### Workflow document

```json
{
  "format": "iris-ai-workflow",
  "format_version": 1,
  "workflow": {
    "name": "Short name",
    "description": "What it does and why",
    "trigger_type": "event",
    "trigger_config": {"hooks": ["on_postload_alert_create"]},
    "graph": {"nodes": [], "edges": []},
    "write_tool_allowlist": [],
    "max_runs_per_hour": 60,
    "token_budget_per_run": 50000,
    "suggestion_audience": "entity"
  },
  "requirements": {"keystore": [], "tools": []}
}
```

- A bare `workflow` object without the envelope is also accepted.
- `requirements`, `warnings`, `exported_at` and `iris_version` are informative and ignored on import.
- An imported workflow is created **inactive** and owned by the importer, with no customer scope. Review it, create the keystore entries it needs, then activate it.

### Block document

A block is a reusable fragment of graph (for example "look a hash up on VirusTotal"). It has no trigger. It is inserted into a workflow from the editor palette.

```json
{
  "format": "iris-ai-workflow-block",
  "format_version": 1,
  "block": {
    "name": "VirusTotal file lookup",
    "description": "…",
    "category": "Threat intel",
    "nodes": [],
    "edges": []
  }
}
```

- A block holds at most 50 nodes.
- Its write tools are checked against the allowlist of the workflow it is inserted into.
- A block cannot hold a literal credential.

## 2. Graph

```json
{
  "nodes": [{"id": "trigger", "type": "trigger", "label": "Start", "config": {}, "position": {"x": 0, "y": 0}}],
  "edges": [{"id": "e1", "source": "trigger", "target": "next", "source_port": "out"}]
}
```

- **Node ids** are 1–64 characters from letters, digits, `_ . : -`, and are unique. Use short meaningful ids (`vt`, `summary`): later nodes reference outputs as `nodes.<id>.output`.
- **Triggers.** A workflow has exactly **one** `trigger` node. Every node must be reachable from it, and no edge may lead back to it.
- **Edges** leave a node through a **port** (`source_port`, default `out`). The ports of each node type are listed in the catalogue, for example a condition has `true`/`false` and an HTTP request has `out`/`error`/`timeout`. Several edges may leave one port; their targets run in order.
- **Errors.** When a node fails, the run follows its `error` port if one is wired; otherwise the run fails.
- **Loops** are allowed: an edge may lead back to an earlier node (never to the trigger). Exit the loop with a `condition`, for example on a counter kept by a `set_variables` node, and read each pass through `vars` or `nodes.<id>.output`, which holds the output of the latest pass. A run fails once it has executed `IRIS_AI_WORKFLOWS_MAX_STEPS_PER_RUN` nodes (1000 by default), so a loop that never exits stops there; every node of every pass counts.
- **Optional fields.** `label` and `position` are optional; the editor lays out nodes without a position. `handles` (editor only) sets the sides a node's connectors are drawn on: `{"input": "top", "output": "bottom"}`, each side being `left`, `top`, `right` or `bottom` (default: input on the left, outputs on the right). An edge may also carry `source_side` / `target_side` (same values) when it leaves or enters a node on another side than its default; the editor sets them when a link is drawn from or dropped on another side.
- **Limits.** A workflow has at most 200 nodes, 1000 edges and 1 MB.

## 3. Triggers

| `trigger_type` | `trigger_config` |
|---|---|
| `event` | `hooks`: list of `on_postload_*` event names (see the catalogue) or `["*"]`. Optional `condition`: a Jinja expression over `event`, `data`, `hook`, `entity_type`, `entity_id`; e.g. `data.alert_severity_id >= 4`. Optional `dedup_minutes`: ignore the same entity again for that long. Optional `skip_if_active` (default `true`): don't start while a run on the same entity is active. |
| `cron` | `cron`: 5-field expression (`*/15 * * * *`). `target`: `none`, `war_rooms` or `cases` (one run per open target the owner can access), and `max_targets`. |
| `manual` | Optional `entity_types`: restricts and requires the entity it runs on (`alert`, `alert_cluster`, `case`, `war_room`). |
| `webhook` | Inbound HTTP endpoint (the token is created after import). Optional `require_signature`, `entity_type` with `entity_id_path`. |

**What a run is about.**
- Alert, alert cluster, case and war room events run on that entity.
- Events about an object *of a case* (IOC, asset, note, task, evidence…) run on the **case**, with `trigger.sub_entity = {"type": "ioc", "id": 42}`.
- A file uploaded to a datastore: `on_postload_datastore_file_create` runs on the case (`sub_entity` type `datastore_file`, name in `data.file_original_name`, hash in `data.file_sha256`); `on_postload_war_room_datastore_file_create` runs on the war room (`sub_entity` type `war_room_datastore_file`, `data.filename`, `data.sha256`). A case file flagged as evidence also fires `on_postload_evidence_create`.
- The serialised object of an event is in `trigger.payload.data`.

**Runs never trigger themselves forever.**
- Events caused by a run are chained, and the chain is capped (`AI_WORKFLOWS_MAX_CHAIN_DEPTH`).
- Still use `dedup_minutes` when a workflow writes to the object that triggers it, for example an IOC update workflow that updates the IOC.

**Workflow fields.**
- `write_tool_allowlist`: write tools the workflow may run directly in `action` and `ai_agent` nodes. Any other write becomes a suggestion an analyst accepts.
- `max_runs_per_hour`: 0 means unlimited.
- `token_budget_per_run`: the LLM tokens a run may spend, 1 to 10,000,000 (only `ai_agent` nodes spend any). Keep 50000 when the workflow has no AI node.
- `suggestion_audience`: `entity` (people working on the entity) or `owner`.

## 4. Run context and templates

Text fields are **Jinja templates** (sandboxed) rendered with the run context:

| Variable | Content |
|---|---|
| `trigger` | `type`, `hook`, `payload` (the event; the object is `payload.data`), `entity_type`, `entity_id`, `sub_entity` |
| `entity` | A snapshot of the alert / cluster / case / war room the run is about, filled by the trigger node |
| `nodes.<id>.output` | The output of every node already run; `nodes.<id>.port` is the port it left through |
| `vars` | Values stored by `set_variables` nodes |
| `run` | `uuid`, `workflow_id`, `workflow_name`, `version`, `dry_run` |
| `now` | The current UTC time (ISO 8601) |
| `key("NAME")` | The keystore entry `NAME`; see below |

Examples: `{{ entity.alert_title }}`, `{{ nodes.vt.output.body.data.attributes.last_analysis_stats.malicious | default(0) }}`, `{% if vars.score > 5 %}high{% endif %}`.

- A field that is a single `{{ … }}` expression keeps its type in tool arguments, so a number stays a number.
- **`{"$path": "dotted.path"}`.** In tool arguments, `set_variables` values, python inputs and suggested actions, an object of this exact form is replaced by the value at that path *with its structure*: a dict stays a dict, a list stays a list. Use it to pass objects, because a template always yields a string.
  - Example: `{"$path": "nodes.summary.output.result.enrichment"}`.
  - List items are addressed by index: `nodes.x.output.items.0`.
  - A missing path gives `null`.

### Secrets: `key("NAME")`

- **Names.** Secrets live in the **keystore** (Settings → AI workflows → Keystore). A name is 1–64 characters from `A-Z`, `0-9` and `_`.
- **Never** write an API key, password or token in a workflow; write `{{ key("VIRUSTOTAL_API_KEY") }}`.
- **Where the value appears.** `key()` gives the real value only inside the fields of an `http_request` node. Everywhere else, including prompts and scripts, it renders `[secret:NAME]`. The value is masked in every log, output and trace.
- **Headers.** For an API-key header, also set `"secret": true` on the header entry.
- **Export.** Keystore values are never exported. A literal credential left in a definition is replaced on export and reported in `warnings`: an HTTP auth secret, credential-named header, query parameter or argument, URL credentials, a recognisable token (`AKIA…`, `ghp_…`, a JWT, a private key) or a `"password": "…"` style literal in any field. The scan cannot recognise an unlabelled literal, such as a random string under an unusual name, so keep every secret in the keystore.

## 5. Node types

The config fields are listed with their defaults in the catalogue. Highlights:

- **`trigger`**: no config. Output: `type`, `hook`, `entity_type`, `entity_id`, `sub_entity`.
- **`condition`**: ports `true` / `false`. It has two modes.
  - `mode: "rules"`: uses `logic` (`and`/`or`) and `rules`, a list of `{path, operator, value}`.
    - `path` is a dotted context path.
    - Operators: `eq ne gt gte lt lte contains not_contains in exists not_exists truthy falsy`.
    - An empty rule list is true.
  - `mode: "expression"`: `expression` is a Jinja expression, e.g. `nodes.vt.output.status_code == 200`.
- **`http_request`**: ports `out`, `error`, `timeout`.
  - **Request fields:**
    - `method`: GET, POST, PUT, PATCH or DELETE.
    - `url`: the scheme and host must be literal. Values interpolated in the path or query are percent-encoded.
    - `query_params` and `headers`: lists of `{name, value, secret}`.
    - Auth: `auth_type` is `none`, `basic` or `bearer`, with `auth_username` and `auth_secret`.
    - Body: `body_mode` is `default` (a JSON summary of the run), `template` (use `body_template`) or `none`. Also `content_type`.
    - Transport: `timeout_seconds` (max 120), `verify_tls`, `use_proxy`, and `response_format` (`json` or `text`).
  - **Output:** `status_code`, `headers` and `body`; the body is parsed when it is JSON. Up to 1 MiB of the response is read; a body kept as text is cut to 64 KiB.
  - **Non-2xx responses** leave through `error`, with `output.error` set.
  - **Async mode** (`mode: "async"`) sends `callback.url` and `callback.token`. The run then waits up to `wait_timeout_minutes` for the remote system to call back.
  - **Redirects are not followed.** Private addresses are refused unless the instance allows them.
- **`python`**: ports `out` and `error`; a safe data transform, see §6.
  - Config:
    - `code`
    - `inputs`: a list of `{name, value}`, where `value` is a template or a `$path` object.
    - `timeout_seconds`: 1–30.
    - `max_steps`: 1000–2,000,000.
  - Output: `result`, `logs` and `steps`.
- **`set_variables`**: `variables` is a list of `{name, value}` (a template or a `$path` object), stored under `vars`.
- **`action`**: runs an IRIS tool, `{tool, arguments}`; the arguments are templates or `$path` objects.
  - **Write tools** must be in `write_tool_allowlist`.
  - **Scoping.** The case / war room of the run is pinned automatically, so you never pass `case_identifier`.
  - Output: `ok`, `executed`, `result` and `tool_call_id`.
- **`ai_agent`**: ports `out` and `error`. It runs the configured LLM.
  - Config:
    - `prompt` (a template)
    - `tools`: tool names the agent may call
    - `output_schema`: a JSON schema of type object; when set, the agent must answer in that shape
    - `max_turns` and `max_tool_calls`
    - `timeout_minutes`: 1 to 60, 10 by default; past it the node fails (its `error` port)
    - `model`
    - `include_entity`
  - Output: `text`; `output` (the structured answer); `suggestion_ids` and `tool_calls`.
  - Data from IRIS and remote systems is fenced as untrusted in the prompt.
- **`find_related`**: `source: "alert"` gives alerts and cases related to the alert of the run. `source: "search"` with `search_value` searches IRIS for a value. Output: `result`.
- **`find_war_room_tasks`**: ports `found` and `none`. It returns the tasks of the war room created since the last run, or in the last `minutes`.
- **`suggest`**: proposes something to the analysts.
  - Config:
    - `kind`, `title` and `body` (templates)
    - `confidence` and `severity`
    - optionally `proposed_action: {tool, arguments}`, a write tool the analyst runs in one click under their own rights
- **`ask_analyst`**: ports `answered` and `timeout`. It asks a question with a form and waits.
  - Config: `title` and `question`, plus `timeout_minutes`.
  - `fields`: a list of `{name, label, type, required, options}`. The types are `text textarea number boolean select multiselect date`.
  - Output: `answer` and `answered_by`.
- **`notify`**: an in-app notification.
  - Config: `audience` is `entity`, `owner` or `users` (with `user_ids`), plus `title` and `body`.
  - `entity` is the entity's owner (a war room: its members) plus the user who triggered the run or whose action fired the event; when none of them can see the entity, the workflow owner is notified instead. Only active users who can see the entity are notified. The output is `{notified: [user ids]}`, with a `note` when the list differs from the audience.
- **`delay`**: waits `minutes`.
- **`stop`**: ends the run, with `status` `succeeded` or `failed` and a `reason`.

## 6. Python transforms (`python` node)

A script transforms data. It runs in a restricted subset of Python, interpreted by IRIS (never `exec`), in a separate process with CPU, memory and time limits.

- **Inputs.** Global variables: `inputs` (the rendered `inputs` of the node), `trigger`, `entity`, `nodes`, `vars` and `run`. They are plain JSON data; the keystore is never available.
- **Output.** Assign `result = …` or use a top-level `return …`. It must be JSON-like: tuples and sets become lists.
- **Allowed:**
  - assignments, including unpacking and `+=`; `if`, `for`, `while`, `break`, `continue`
  - `def` with default arguments, `lambda`
  - comprehensions, f-strings, slicing
  - `try` / `except [Exception] [as e]`
- **Not allowed:**
  - `import`, `class`, `with`, `global`, `raise`, `del`, `yield`, `async`, `assert`, walrus
  - decorators, `*args` / `**kwargs`
  - **attribute access** (only allow-listed method calls such as `s.lower()`, `d.get(k)`, `l.append(x)`)
  - any name starting with `_`
  - `%` string formatting
- **Built-ins:**
  - Types and conversions: `len str repr int float bool list tuple set dict`.
  - Numbers and sequences: `abs min max sum round sorted reversed enumerate zip range any all map filter`.
  - Type tests: `type_of is_str is_number is_list is_dict`.
  - Output and control: `print` (captured in `logs`), `fail(message)` (ends the script in error; it cannot be caught).
  - JSON, encoding and hashing: `json_parse json_dumps`; `base64_encode(text, urlsafe=False, padding=True)`, `base64_decode(text, urlsafe=False)`; `sha256 sha1 md5` (of text).
  - Regular expressions: `re_search re_match re_fullmatch re_findall re_sub re_split`. They take `(pattern, text, flags='')`, where the flags are from `imsx`. A match gives `{match, groups, named, start, end}` or `None`.
  - Parsing: `ip_info(text)` gives `{version, is_private, is_global, is_loopback, …}` or `None`. `url_parse(text)` gives `{scheme, host, port, path, query, fragment}` or `None`. `iso_datetime(seconds)` gives a Unix timestamp as `YYYY-MM-DDTHH:MM:SSZ` (UTC), or `None`.
  - Maths: `floor ceil sqrt log`.
- **Methods:** str (`lower upper strip split join replace startswith endswith find count title zfill partition removeprefix …`); list (`append extend insert pop remove index count sort reverse copy clear`); dict (`get keys values items pop setdefault update copy clear`); set (`add discard remove union intersection difference …`).
- **Limits:** 20,000 characters of code; strings of 1 MB; 100,000 items per container; integers of 4096 bits; a call depth of 32; output of 1 MB. The step and time budgets come from the node config.
- **Errors.** A failing script leaves through the `error` port if one is wired, otherwise the run fails. The error carries the line number.

```python
# inputs: value = "{{ trigger.payload.data.ioc_value }}", type = "{{ trigger.payload.data.ioc_type.type_name }}"
kind = inputs['type'].lower()
if kind in ('md5', 'sha1', 'sha256'):
    result = {'supported': True, 'collection': 'files', 'id': inputs['value'].strip().lower()}
else:
    result = {'supported': False}
```

## 7. Writing workflows that work

1. **Start from the trigger.** Pick the events from the catalogue, then use `trigger.payload.data` and `trigger.sub_entity` in later nodes.
2. **Normalise first.** Use a `python` node to check the input and compute what later nodes need. Then a `condition` on its output stops early when there is nothing to do.
3. **Call external systems** with `http_request`. Put credentials in the keystore and reference them with `key()`. Wire `error` and `timeout`, for example to a `stop` node with a reason.
4. **Shape results** with a second `python` node, and write them back with an `action` whose arguments use `$path`. Add the tool to `write_tool_allowlist`, or use a `suggest` node to let an analyst decide.
5. **Avoid storms.** Use `dedup_minutes` on event triggers and `max_runs_per_hour`.
6. **Dry runs.** Use **Dry run** in the editor before activating. Dry runs never call external systems and never write; writes become suggestions.

## 8. Example: IOC enrichment from VirusTotal (event on IOC create/update)

```json
{
  "format": "iris-ai-workflow",
  "format_version": 1,
  "workflow": {
    "name": "VirusTotal IOC enrichment",
    "trigger_type": "event",
    "trigger_config": {"hooks": ["on_postload_ioc_create", "on_postload_ioc_update"], "dedup_minutes": 60},
    "write_tool_allowlist": ["iris_case_iocs_update"],
    "graph": {
      "nodes": [
        {"id": "trigger", "type": "trigger", "config": {}},
        {"id": "prepare", "type": "python", "config": {
          "inputs": [{"name": "ioc", "value": {"$path": "trigger.payload.data"}}],
          "code": "kind = ((ioc.get('ioc_type') or {}).get('type_name') or '').lower()\nvalue = (ioc.get('ioc_value') or '').strip()\nif kind in ('md5', 'sha1', 'sha256'):\n    result = {'supported': True, 'collection': 'files', 'id': value.lower()}\nelse:\n    result = {'supported': False}"
        }},
        {"id": "supported", "type": "condition", "config": {"mode": "rules", "rules": [{"path": "nodes.prepare.output.result.supported", "operator": "truthy"}]}},
        {"id": "vt", "type": "http_request", "config": {
          "method": "GET",
          "url": "https://www.virustotal.com/api/v3/{{ nodes.prepare.output.result.collection }}/{{ nodes.prepare.output.result.id }}",
          "headers": [{"name": "x-apikey", "value": "{{ key(\"VIRUSTOTAL_API_KEY\") }}", "secret": true}],
          "body_mode": "none"
        }},
        {"id": "update", "type": "action", "config": {"tool": "iris_case_iocs_update", "arguments": {
          "ioc_identifier": {"$path": "trigger.sub_entity.id"},
          "payload": {"ioc_enrichment": {"$path": "nodes.summary.output.result"}}
        }}},
        {"id": "summary", "type": "python", "config": {"code": "…", "inputs": []}},
        {"id": "done", "type": "stop", "config": {"status": "succeeded", "reason": "Nothing to look up"}}
      ],
      "edges": [
        {"id": "e1", "source": "trigger", "target": "prepare"},
        {"id": "e2", "source": "prepare", "target": "supported"},
        {"id": "e3", "source": "supported", "target": "vt", "source_port": "true"},
        {"id": "e4", "source": "supported", "target": "done", "source_port": "false"},
        {"id": "e5", "source": "vt", "target": "summary"},
        {"id": "e6", "source": "summary", "target": "update"}
      ]
    }
  }
}
```

The complete version of this workflow, which covers hashes, IPs, domains and URLs, merges the VirusTotal verdict into the existing enrichment and tags, and alerts the analysts on malicious verdicts, ships with IRIS as `virustotal_ioc_enrichment.workflow.json`. A matching saved block is `virustotal_lookup.block.json`.

More workflows ship in the workflow library; they are listed at the end of this guide, and the library endpoint returns them in full. Among them:

- `detection_rule_feedback.workflow.json` runs every Monday morning. It pages through the alerts closed in the last 7 days with `iris_alerts_list` and its `fields` argument, so that each page stays small. A Python node groups them per detection rule and computes the false positive rate and the time to close. An agent then proposes tunings for the noisiest rules. The proposals become one suggestion and a notification to the workflow owner.
- `ioc_estate_hunt.workflow.json` runs when an IOC is created. It searches the SIEM for the IOC over the last 30 days with `http_request`, Elasticsearch / OpenSearch or Splunk depending on the `siem` input of its query node, and counts the hits per host in Python. Hosts that are not assets of the case are listed in a notification. Each of them, up to 5, becomes a suggestion to add it to the case as an asset linked to the IOC. Set `SIEM_API_KEY` in the keystore and the search URL to your SIEM before enabling it.
- `alert_triage.workflow.json` runs when an alert of severity Medium or above is created. `find_related` and a Python node list the alerts and open cases that share its IOCs or assets. An agent then picks a verdict, and a Python node checks it before the conditions route it: a suggestion to close the alert (the status and resolution ids are looked up by name with `iris_taxonomies_list`), to merge it into one of those open cases, or to escalate it. When the agent needs facts, `ask_analyst` asks once (a variable remembers it) and the answer goes back to the agent.
- `case_kickoff.workflow.json` runs when a case is opened. An agent drafts a kickoff note and the first tasks; each becomes a suggestion whose proposed action creates the note or the task.
- `case_closure_documentation_review.workflow.json` runs when a case is closed. Its trigger condition compares the case `close_date` with the event `timestamp`, so later updates of a case closed on another day do not run it, and `dedup_minutes` covers the rest of the day. An agent rates the documentation and drafts a closure report, a better description and full rewrites of up to three notes. A Python node checks them, then each becomes a suggestion whose proposed action creates the note (`iris_case_notes_create`), updates the case (`iris_cases_update`) or the note (`iris_case_notes_update`).

## 9. Checklist before answering with a workflow

- [ ] One `trigger` node; every node is reachable; node ids are unique and short.
- [ ] Every `source_port` exists on its node type; `error` / `timeout` ports are wired where a failure matters.
- [ ] Event names, tool names and keystore names come from the catalogue below.
- [ ] Write tools used by `action` nodes are in `write_tool_allowlist`.
- [ ] No literal secret anywhere: only `{{ key("NAME") }}`, in `http_request` fields, with `"secret": true` on headers.
- [ ] Objects passed to tools use `{"$path": …}`; Python scripts use only the allowed subset.
- [ ] Event triggers that write to their own object use `dedup_minutes`.
