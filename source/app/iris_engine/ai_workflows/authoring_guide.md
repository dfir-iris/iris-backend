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
- The triggers are set on the `trigger` nodes of the graph (section 3). A document written before, with a `triggers` list (or a single `trigger_type` with its `trigger_config`) and one config-less `trigger` node, is still accepted: each trigger becomes a `trigger` node.
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
  "nodes": [{"id": "trigger", "type": "trigger", "label": "New alert",
             "config": {"trigger_type": "event", "hooks": ["on_postload_alert_create"]}, "position": {"x": 0, "y": 0}}],
  "edges": [{"id": "e1", "source": "trigger", "target": "next", "source_port": "out"}]
}
```

- **Node ids** are 1–64 characters from letters, digits, `_ . : -`, and are unique. Use short meaningful ids (`vt`, `summary`): later nodes reference outputs as `nodes.<id>.output`.
- **Triggers.** A workflow has 1 to 10 `trigger` nodes, each starting the run its own way (section 3). Every node but a `note` must be reachable from one of them, and no edge may lead to a trigger.
- **Edges** leave a node through a **port** (`source_port`, default `out`). The ports of each node type are listed in the catalogue, for example a condition has `true`/`false` and an HTTP request has `out`/`error`/`timeout`. Several edges may leave one port; their targets run in order.
- **Errors.** When a node fails, the run follows its `error` port if one is wired; otherwise the run fails.
- **Loops** are allowed: an edge may lead back to an earlier node (never to a trigger). Exit the loop with a `condition`, for example on a counter kept by a `set_variables` node, and read each pass through `vars` or `nodes.<id>.output`, which holds the output of the latest pass. A run fails once it has executed `IRIS_AI_WORKFLOWS_MAX_STEPS_PER_RUN` nodes (1000 by default), so a loop that never exits stops there; every node of every pass counts.
- **Optional fields.** `label` and `position` are optional; the editor lays out nodes without a position. `handles` (editor only) sets the sides a node's connectors are drawn on: `{"input": "top", "output": "bottom"}`, each side being `left`, `top`, `right` or `bottom` (default: input on the left, outputs on the right). An edge may also carry `source_side` / `target_side` (same values) when it leaves or enters a node on another side than its default; the editor sets them when a link is drawn from or dropped on another side.
- **Limits.** A workflow has at most 200 nodes, 1000 edges and 1 MB.

## 3. Triggers

Each `trigger` node carries one trigger: its config is `trigger_type` plus the keys of that type, e.g. `{"trigger_type": "cron", "cron": "0 7 * * *", "target": "none"}`. A workflow can, for example, run on an event, every night and from a menu, each from its own `trigger` node wired to its own path or to shared nodes. A type may appear on several nodes (two schedules, two events with different conditions, two menu entries), except `webhook` and `workflow`: at most one each. The run starts at the node whose trigger fired; `trigger.type` and `trigger.node_id` tell which one.

- An event starts one run per workflow: at the first event trigger, in node order, whose `hooks` and `condition` match.
- Each schedule fires on its own.
- Each manual trigger with `menus` is its own menu entry. A manual run from the editor picks its trigger node.

| `trigger_type` | Other config keys |
|---|---|
| `event` | `hooks`: list of `on_postload_*` event names (see the catalogue) or `["*"]`. Optional `condition`: a Jinja expression over `event`, `data`, `hook`, `entity_type`, `entity_id`; e.g. `data.alert_severity_id >= 4`. Optional `dedup_minutes`: ignore the same entity again for that long. Optional `skip_if_active` (default `true`): don't start while a run on the same entity is active. |
| `cron` | `cron`: 5-field expression (`*/15 * * * *`). `target`: `none`, `war_rooms` or `cases` (one run per open target the owner can access), and `max_targets`. |
| `manual` | Optional `entity_types`: restricts and requires the entity it runs on (`alert`, `alert_cluster`, `case`, `war_room`). Optional `menus` (`alert`, `case`, `asset`, `ioc`, `note`, `event`, `evidence`, `task`) and `menu_label`: shows the workflow in those object menus; a click runs it as the analyst who clicked (their access and personal keys), with the object as `trigger.payload`. The menus need `entity_types` to accept `alert` (alert menu) or `case` (the others). Selecting several objects starts one run per object, unless `batch` is `true`: then one run on their case, with the list of the selected objects in `trigger.payload.data` (a list even for one object), their ids in `trigger.payload.object_ids` and `trigger.sub_entity.ids`. `batch` is for case objects only (not the `alert` or `case` menus). |
| `webhook` | Inbound HTTP endpoint (the token is created after import). Optional `require_signature`, `entity_type` with `entity_id_path`. |
| `workflow` | No config. Lets other workflows call this one with a `run_workflow` node; the inputs are in `trigger.payload.inputs` and the caller in `trigger.payload.caller`. |

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
- `suggestion_audience`: `entity` (people working on the entity: its owner, the user the run acts as and the user who started the run by hand) or `owner`.

## 4. Run context and templates

Text fields are **Jinja templates** (sandboxed) rendered with the run context:

| Variable | Content |
|---|---|
| `trigger` | `type`, `node_id` (the trigger node that started the run), `hook`, `payload` (the event; the object is `payload.data`), `entity_type`, `entity_id`, `sub_entity` |
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

- **`trigger`**: config `trigger_type` and that type's keys (section 3). Output: `type`, `node_id`, `hook`, `entity_type`, `entity_id`, `sub_entity`.
- **`condition`**: ports `true` / `false`. It has two modes.
  - `mode: "rules"`: uses `logic` (`and`/`or`) and `rules`, a list of `{path, operator, value}`.
    - `path` is a dotted context path.
    - Operators: `eq ne gt gte lt lte contains not_contains in exists not_exists truthy falsy`.
    - An empty rule list is true.
  - `mode: "expression"`: `expression` is a Jinja expression, e.g. `nodes.vt.output.status_code == 200`.
- **`http_request`**: ports `out`, `error`, `timeout`.
  - **Request fields:**
    - `method`: GET, POST, PUT, PATCH or DELETE.
    - `url`: the scheme and host are literal or come from `{{ key("NAME") }}` (e.g. `https://{{ key("SPLUNK_HOST") }}:8089/...`, or a key holding a whole base URL: `{{ key("SPLUNK_URL") }}/services/...`); no other value may choose the host. Values interpolated in the path or query are percent-encoded.
    - `query_params` and `headers`: lists of `{name, value, secret}`.
    - Auth: `auth_type` is `none`, `basic` or `bearer`, with `auth_username` and `auth_secret`; or `oauth2_client_credentials` (below).
    - **OAuth 2.0 client credentials** (`auth_type: "oauth2_client_credentials"`, e.g. Microsoft Graph): the node first asks `oauth_token_url` for an access token, then sends it as `Authorization: Bearer …`.
      - Fields: `oauth_token_url` (https only; its host is literal or from `key()`, like `url`, but the path may hold a value such as a tenant ID), `oauth_client_id`, `oauth_client_secret` (always `{{ key("NAME") }}`), `oauth_scope` (e.g. `https://graph.microsoft.com/.default`), and `oauth_client_auth`: `body` (the default, credentials in the form) or `basic` (an HTTP Basic header).
      - The host restrictions of the client secret entry apply to the token request only, so restrict it to the identity provider (`login.microsoftonline.com`) and the API host stays free.
      - The token is cached until shortly before it expires, per token URL, client and scope, and dropped when the API answers 401. It is masked like a secret and never stored; the step logs `oauth: {token_url, client_id, scope, client_auth}` instead.
      - A dry run asks for no token.
      - A token endpoint error fails the node (its `error` port), with the provider's `error` and `error_description`.
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
  - **Customer.** `iris_customers_get` reads the customer of the run's case (pinned like a case) or a `customer_id`: name, description, SLA, custom attributes, contacts and `customer_key_values`, free per-customer settings such as a tenant ID (`{"ENTRA_TENANT_ID": "…"}`) edited in Settings → Customers. The key/values are not secret: keep credentials in the keystore, and use a key/value to choose, per customer, what a request targets.
  - **Customers.** `iris_customers_list` lists the customers the run-as user can access (`search` on the name or description, or `customer_ids`), with their `customer_key_values`; it is the way to find the customer of an external tenant (a key/value such as `{"EXTERNAL_TENANT_ID": "…"}`, else the name). Under a customer scope it only lists those customers. Like `iris_customers_get`, the administrator must enable it in the MCP settings.
  - **Alerts.** `iris_alerts_create` creates an alert: `alert_customer_id` (a customer the run-as user can access and, under a customer scope or on a run about an entity, one of the customers of the run), `payload` with `alert_title` and `alert_severity_id` (required), `alert_status_id` (New by default) and the other alert fields (`alert_description`, `alert_source`, `alert_source_ref`, `alert_source_link`, `alert_source_event_time`, `alert_tags`, `alert_note`, `alert_classification_id`, `alert_owner_id`, `alert_context`, `alert_source_content`), plus optional `alert_iocs` (`ioc_value`, `ioc_type_id`, `ioc_tlp_id`, …) and `alert_assets` (`asset_name`, `asset_type_id`, …). The new alert fires `on_postload_alert_create` like an ingested one, so an event workflow on that hook runs on it (up to `AI_WORKFLOWS_MAX_CHAIN_DEPTH` levels): a workflow creating alerts from an alert event should skip its own alerts with a trigger `condition`, e.g. on `alert_source`. Output `result` is the alert, with `alert_id`. `iris_alerts_update` changes an alert (`alert_identifier` and a partial `payload`: status, resolution, severity, classification, owner, tags, note…); on a run about an alert, the alert is pinned. The `iris_alerts_*_list` read tools give the numeric ids. Both tools need `alerts_write` and must be in `write_tool_allowlist`.
  - **War rooms.** `iris_war_rooms_create` creates a room (`name`, `description`, `state`, `severity_id`); the run-as user becomes its lead. `iris_war_room_cases_attach` attaches a case (full access on the case needed), `iris_war_room_members_add` adds a user (`role`: `lead`, `responder` or `observer`; `access`: `full_access` or `read_only`; the administrator must enable it, and an `ai_agent` can never use it). A run without a war room may still pass `war_room_id`, for the rooms the runs of the same workflow created (while their runs are kept, `AI_WORKFLOWS_RETENTION_DAYS`) and, under a customer scope, only while every case attached to them belongs to these customers. Any other war room is refused.
  - **Case access.** `iris_case_access_list` reads who can access the case (and, for a run-as user who may manage its access, every group with its level). `iris_case_access_set_user` and `iris_case_access_set_group` set an access level (`full_access`, `read_only` or `deny_all`) for a user or group id. The run-as user needs `case_access_manage` and full access to the case, or to be a server administrator. Typical use: on `on_postload_case_create`, a `condition` with the expression `'restricted' in (trigger.payload.data.tags | map(attribute='tag_title') | list)` (the tags are `{id, tag_title}` objects), then a set-group `action`.
- **`ai_agent`**: ports `out` and `error`. It runs the configured LLM.
  - Config:
    - `prompt` (a template)
    - `tools`: tool names the agent may call; `iris_case_access_set_user`, `iris_case_access_set_group` and `iris_war_room_members_add` are refused, for `action` and `suggest` nodes only
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
    - or `actions`: up to 10 `{label, tool, arguments, when}` in one suggestion. The analyst accepts all of them, or ticks the ones to run. `label` is a template. `when` is an optional expression, like a `condition` node's, and the action is left out when it is false. Prefer one suggestion with several actions to several suggestions about the same object: a run proposes at most 5 suggestions, and a bundle counts once.
- **`ask_analyst`**: ports `answered` and `timeout`. It asks a question with a form and waits.
  - Config: `title` and `question`, plus `timeout_minutes`.
  - `fields`: a list of `{name, label, type, required, options}`. The types are `text textarea number boolean select multiselect date`.
  - `audience` is who is asked: `entity` (the workflow's suggestion audience, the default), `triggerer` (the user who started the run or whose action fired the event), `owner` or `users` (with `user_ids`). Only active users who can see the entity are asked; when none of them can, the question goes to the workflow's suggestion audience and the output has a `note`.
  - `popup` (default true) opens the question in a dialog for the users asked, wherever they are in IRIS, instead of only listing it on the entity and in the suggestions inbox.
  - Output: `answer` and `answered_by`.
- **`notify`**: an in-app notification, a toast, or both.
  - Config: `audience` is `entity`, `triggerer` (the user who started the run or whose action fired the event), `owner` or `users` (with `user_ids` and/or `users_from`), plus `title` and `body`.
  - `users_from` reads the users from the run: a template or a `$path` object giving a user id, a login or an email, a comma-separated list of them, or a list of them or of objects with a `user_id` or an `id` (up to 50). E.g. `"{{ vars.analyst }}"` or `{"$path": "nodes.lookup.output.result.owners"}`. Names that are no user are listed in the output `note`.
  - `delivery`: `notification` (the default) goes to the bell and follows the users' notification settings; `toast` pops up at once in the browsers of the users connected, and nothing is kept: a user who is not connected never sees it; `both` does both.
  - `severity`: `info` (the default), `success`, `warning` or `critical`; it picks the icon of the notification and the colour of the toast.
  - `timeout_seconds`: how long a toast stays up, 5 by default, 0–3600; 0 keeps it until the user closes it.
  - `entity` is the entity's owner (a war room: its members) plus the user who triggered the run or whose action fired the event; when none of them can see the entity, the workflow owner is notified instead. Only active users who can see the entity are notified. The output is `{notified: [user ids], delivery, severity}`, with a `note` when the list differs from the audience.
  - `about: {entity_type, entity_id}` points the notification at a `case`, `alert` or `war_room` other than the run's entity, such as one the run created: `entity_id` is a template or a `$path` object, e.g. `{"$path": "nodes.room.output.result.war_room_id"}`. The notification links to it, `entity` means its owner (a war room: its members), and only users who can see it are notified. It must be in the scope of the run, like a tool argument. Without `about`, a run with no entity (a webhook, a schedule) has no link, and only checks that the users are active.
- **`run_workflow`**: ports `out`, `error`, `timeout`. It starts another workflow, which must be active and have a `workflow` trigger.
  - Config: `workflow_id`, or `"self"` for the workflow calling itself (it keeps working after an export and an import; the workflow then needs its own `workflow` trigger); `inputs`, a list of `{name, value}` (a template or a `$path` object) given to the called workflow as `trigger.payload.inputs`; `mode`; `timeout_minutes` (1–43200, 60 by default).
  - The called run is about the same entity and acts as the calling run: the same run-as user and access. It counts towards the chain depth (`AI_WORKFLOWS_MAX_CHAIN_DEPTH`).
  - `mode: "wait"` (the default) waits for the called run. Output: `run_uuid`, `workflow_id`, `workflow_name`, `status`, `error`, `vars` and `output` (the output of its last node). A failed or cancelled run leaves through `error` if it is wired, otherwise the calling run fails. Past `timeout_minutes` the node leaves through `timeout`; the called run goes on.
  - `mode: "async"` starts the run and goes on at once. Output: `run_uuid`, `workflow_id`, `workflow_name`, `status`.
  - In a dry run nothing is started.
- **`delay`**: waits `minutes`.
- **`stop`**: ends the run, with `status` `succeeded` or `failed` and a `reason`.
- **`note`**: documents part of the workflow on the canvas, like a sticky note. It has no ports, no edge may lead to it and it never runs.
  - Config: `text` (Markdown, never rendered as a template; shown without images, forms or styles, and with `https:` or relative links only), `color` (`yellow`, `blue`, `green`, `pink`, `purple` or `gray`), `width` and `height` (80–4000 pixels).
  - Use a few notes to explain the intent of a group of nodes, the expected inputs or a manual step around the workflow.

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
    "write_tool_allowlist": ["iris_case_iocs_update"],
    "graph": {
      "nodes": [
        {"id": "trigger", "type": "trigger", "config": {
          "trigger_type": "event", "hooks": ["on_postload_ioc_create", "on_postload_ioc_update"], "dedup_minutes": 60
        }},
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
- `ioc_estate_hunt.workflow.json` runs when an IOC is created. `ask_analyst` asks the analysts of the case whether to look it up, with a boolean field; the run leaves through `timeout` when the question is dismissed or expires, and its trigger sets `skip_if_active` to false so that a waiting question does not hold back the other IOCs of the case. When the answer is yes, it searches the SIEM for the IOC over the last 30 days with `http_request`, Elasticsearch / OpenSearch or Splunk depending on the `siem` input of its query node, and counts the hits per host in Python. Hosts that are not assets of the case are listed in a notification. Each of them, up to 5, becomes a suggestion to add it to the case as an asset linked to the IOC. Set `SIEM_API_KEY` in the keystore and the search URL to your SIEM before enabling it.
- `alert_triage.workflow.json` runs when an alert of severity Medium or above is created. `find_related` and a Python node list the alerts and open cases that share its IOCs or assets. An agent then picks a verdict, and a Python node checks it before the conditions route it: a suggestion to close the alert (the status and resolution ids are looked up by name with `iris_taxonomies_list`), to merge it into one of those open cases, or to escalate it. When the agent needs facts, `ask_analyst` asks once (a variable remembers it) and the answer goes back to the agent.
- `case_kickoff.workflow.json` runs when a case is opened. An agent drafts a kickoff note and the first tasks; each becomes a suggestion whose proposed action creates the note or the task.
- `case_closure_documentation_review.workflow.json` runs when a case is closed. Its trigger condition compares the case `close_date` with the event `timestamp`, so later updates of a case closed on another day do not run it, and `dedup_minutes` covers the rest of the day. An agent rates the documentation and drafts a closure report, a better description and full rewrites of up to three notes. A Python node checks them, then each becomes a suggestion whose proposed action creates the note (`iris_case_notes_create`), updates the case (`iris_cases_update`) or the note (`iris_case_notes_update`).

## 9. Checklist before answering with a workflow

- [ ] Each `trigger` node has a `trigger_type`; every node but a `note` is reachable from a trigger; node ids are unique and short.
- [ ] Every `source_port` exists on its node type; `error` / `timeout` ports are wired where a failure matters.
- [ ] Event names, tool names and keystore names come from the catalogue below.
- [ ] Write tools used by `action` nodes are in `write_tool_allowlist`.
- [ ] No literal secret anywhere: only `{{ key("NAME") }}`, in `http_request` fields, with `"secret": true` on headers.
- [ ] Objects passed to tools use `{"$path": …}`; Python scripts use only the allowed subset.
- [ ] Event triggers that write to their own object use `dedup_minutes`.
