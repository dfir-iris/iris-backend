
# IRIS Configuration
In order to connect to the database and other systems certain configurations are needed. This document lists all available configurations.


## How to set configuration variables
There are 3 different options to set configuration variables
1. Azure Key Vault
2. Environment Variables
3. The config.ini file

### Azure Key Vault
The first option that is checked is the Azure Key Vault. In order to use this the `AZURE_KEY_VAULT_NAME` should be specified. 

Since Azure Key Vault does not support underscores you should remove this from the configuration name. For example: `POSTGRES_USER` becomes `POSTGRES-USER`.

### Environment Variables
The second option is using environment variables, which gives the most amount of flexibility. 
### Config.ini
The last and fallback option is the config.ini. Within the project there is a `config.model.ini`, which is not used but gives the example how the file should look like. If the application is started with the environment variable `DOCKERIZED=1` then the `config.docker.ini` is loaded, otherwhise the `config.priv.ini` is loaded.

## Environment variable only
A few configs are environment variables only:

- `IRIS_WORKER` - Specifies if the process is the worker
- `DOCKERIZED` - Is set when running in docker, also loads the other config.ini

## Configuration options

## POSTGRES
The POSTGRES section has the following configurations:

- `POSTGRES_USER` - The user IRIS uses
- `POSTGRES_PASSWORD` - The password for the user IRIS uses
- `POSTGRES_ADMIN_USER` - The user IRIS uses for table migrations
- `POSTGRES_ADMIN_PASSWORD` - The password for the user IRIS uses for table migrations
- `POSTGRES_HOST` - The server address
- `POSTGRES_PORT` - The server port

## CELERY

- `CELERY_BROKER` - The broker address used by [Celery](https://github.com/celery/celery)

## SOCKETIO

- `SOCKETIO_MESSAGE_QUEUE` - Message queue that carries live updates (chat, notifications, collaborative note edits) between the backend's gunicorn workers, and from Celery tasks to the browsers. Defaults to `CELERY_BROKER`, so the stack's RabbitMQ is used with no extra setup; any [kombu](https://docs.celeryq.dev/projects/kombu/) URL works. Each worker only holds the Socket.IO connections that landed on it, so without a queue an update reaches only the users connected to the worker that produced it. `none` turns the queue off; only do that when running a single worker.

## IRIS

- `IRIS_SECRET_KEY` - The secret key used by Flask.
- `IRIS_SECURITY_PASSWORD_SALT` - ??
- `IRIS_ALLOW_PRIVATE_EGRESS` - `True` lets report templates reference images on private, loopback or link-local addresses. Defaults to `False`: the renderer fetches those URLs from inside the backend network, so allowing them exposes internal services and the cloud metadata endpoint to whoever can upload a template.
- `IRIS_WEBHOOKS_ALLOW_PRIVATE_EGRESS` - Defaults to `True`: webhooks may target private, loopback, link-local and reserved addresses (an internal SOAR or chat server), since only server administrators configure them. Set to `False` to refuse those destinations, redirect hops included. Independent of `IRIS_ALLOW_PRIVATE_EGRESS`, which only governs report templates.
- `IRIS_WEBHOOKS_DELIVERY_RETENTION_DAYS` - Days of webhook delivery logs kept. Older deliveries are pruned daily by the Celery beat. Defaults to `30`.
- `IRIS_AI_WORKFLOWS_ENABLED` - Defaults to `True`. `False` stops every AI workflow trigger (events, schedules, inbound webhooks, manual runs) and parks running workflows where they are; definitions, runs and suggestions stay readable. AI agent nodes also need the chatbot provider configured in the server settings.
- `IRIS_AI_WORKFLOWS_ALLOW_PRIVATE_EGRESS` - Defaults to `False`: HTTP request nodes of AI workflows refuse private, loopback, link-local and reserved destinations, redirect hops included. Any holder of `ai_workflows_write` can configure those nodes, hence the stricter default than webhooks. Set to `True` when workflows must call internal systems.
- `IRIS_AI_WORKFLOWS_MAX_CHAIN_DEPTH` - How many levels of workflows a workflow-originated change may trigger (a workflow creating a case that triggers another workflow is one level). Defaults to `2`; `0` means changes made by workflows never trigger workflows.
- `IRIS_AI_WORKFLOWS_MAX_STEPS_PER_RUN` - Node executions allowed in one run before it fails, which stops runaway loops. Defaults to `100`.
- `IRIS_AI_WORKFLOWS_PYTHON_ENABLED` - Whether the `python` (Python transform) node may run. Scripts are interpreted by IRIS itself (a restricted Python subset, no imports, files, network or attributes) in a short-lived child process with CPU, memory and time limits. Set to `False` to refuse them. Defaults to `True`.
- `IRIS_AI_WORKFLOWS_RETENTION_DAYS` - Days of AI workflow runs (steps, tool calls, LLM calls), closed suggestions and inbound webhook logs kept. Pruned daily by the Celery beat. Defaults to `90`.
- `IRIS_AI_WORKFLOWS_MAX_INBOUND_BYTES` - Largest body accepted by the AI workflow inbound webhook and callback endpoints. Defaults to `1048576` (1 MB). The bundled nginx also caps `/api/v2/ai-workflows/hooks/` and `/callbacks/` at 1 MB and rate limits them to 10 requests per second per client address (burst 20, then 429); raise both together if needed. A webhook payload over 256 KB is kept on the run as a summary (size, sha256, 16 KB text preview) rather than in full.
  - Webhook triggers authenticate with the workflow inbound token as `Authorization: Bearer <token>`. Rotating the token also generates a separate signing secret; both are shown once, and the signing secret can be rotated on its own (`POST /api/v2/ai-workflows/<id>/signing-secret`). It is stored encrypted. A request is signed with `X-IRIS-Timestamp: <unix seconds>` and `X-IRIS-Signature: sha256=<hex HMAC-SHA256(signing secret, X-IRIS-Timestamp + "." + raw body)>`. The timestamp is signed exactly as sent in the header and must be within 300 seconds of the server clock. The signature is mandatory when the trigger sets `require_signature`. A signature already accepted for the workflow within the tolerance window is refused as a replay.
  - Callbacks of async HTTP request nodes use the per-wait callback token as the bearer token. Their signature is optional and uses the same headers, with the callback token as the HMAC key.
  - Every refusal (token, signature, replay, expiry, unknown id) answers the same 401. An accepted trigger answers 202 `{"run_uuid", "status": "accepted"}` whether or not the workflow owner can access the entity named in the payload; a refused run shows up on the workflow as skipped. JSON bodies with `NaN`/`Infinity`, integers outside 64 bits or nesting deeper than 32 get a 400. Attempts on a known workflow or wait are logged as inbound events. Attempts on unknown or malformed ids only go to the application log.
  - Keystore entries: a shared entry must list at least one allowed host. An empty `allowed_hosts` on a personal entry means its owner's HTTP request nodes may send it to any host. Secret values are encrypted with a key derived from `IRIS_SECRET_KEY`, and so are workflow signing secrets. Rotating `IRIS_SECRET_KEY` makes every stored secret unreadable, so they must be entered again and the signing secrets rotated.
- `IRIS_AI_WORKFLOWS_CALLBACK_BASE_URL` - Base URL external systems use to reach IRIS for AI workflow callbacks and inbound webhooks, e.g. `https://iris.example.org`. Defaults to the public URL (first entry of `IRIS_ALLOW_ORIGIN`).
- `IRIS_CVE_SYNC_ENABLED` - Defaults to `True`: public CVE entries of the vulnerability catalogue can be synchronised from the CVE Program API (title, description, CVSS, CWEs, affected products, references, dates, and the CISA KEV / exploitation flags of the CISA ADP container). Quick-adding an unknown CVE from a finding fills it the same way. Only the CVE identifier leaves the instance; private `IRIS-VULN-…` entries are never synchronised. Set to `False` on instances without outbound internet access. The HTTP(S) proxies of the server settings apply.
- `IRIS_CVE_API_URL` - Base URL of the CVE record API, the identifier is appended. Defaults to `https://cveawg.mitre.org/api/cve/`; point it at an internal mirror serving the same CVE JSON 5 records if needed.
- `IRIS_CVE_API_TIMEOUT_SECONDS` - Timeout of a CVE record request. Defaults to `10`. The automatic fill on quick add uses at most half of it so a slow API never stalls finding creation for long.
- `IRIS_LOGIN_MAX_ATTEMPTS` - Consecutive failed logins tolerated for one account before it is locked out. Defaults to `10`.
- `IRIS_LOGIN_MAX_ATTEMPTS_PER_CLIENT` - Consecutive failed logins tolerated from one client address, across all accounts. Defaults to `50`. Deliberately looser than the per-account ceiling because a whole office can share one source address; raise it if a reverse proxy is in front and `remote_addr` is the proxy.
- `IRIS_LOGIN_LOCKOUT_SECONDS` - How long a lockout lasts once either ceiling is crossed. Defaults to `900` (15 minutes). Counting is in-process and per worker, so this raises the cost of brute force rather than making it impossible.

## OIDC

Only read when `IRIS_AUTHENTICATION_TYPE` is `oidc_proxy`, except where an entry states otherwise.

- `OIDC_IRIS_PROXY_TRUSTED_IPS` - Comma-separated addresses or CIDR blocks of the reverse proxies allowed to assert an identity when `OIDC_IRIS_TOKEN_VERIFY_MODE` is `lazy`. Matched against the peer address of the connection, which a client cannot forge. **Lazy mode authenticates nobody while this is empty**: it takes the caller's identity from the `X-Email` header without validating any token, so anything that can reach the backend directly could otherwise log in as any registered user. Set it to the address of the proxy that terminates authentication and overwrites `X-Email` (`127.0.0.1` for a sidecar), and make sure that proxy strips client-supplied `X-Email` and `X-Forwarded-Access-Token` headers.
- `OIDC_IRIS_ISSUER_URL` - Issuer pinned when verifying `X-Forwarded-Access-Token` signatures in `signature` mode. Defaults to the issuer advertised by the discovery document; override it only when IRIS should accept tokens from a different issuer URL. Signature mode refuses to authenticate if this and `OIDC_IRIS_AUDIENCE` are not both set, because an unpinned issuer accepts any token the configured JWKS can validate — including one from another tenant whose `sub` names a local user.
- `OIDC_IRIS_REQUIRE_VERIFIED_EMAIL` - Read when `IRIS_AUTHENTICATION_TYPE` is `oidc`. Governs whether a token the provider has not vouched for may take over an **existing** local account — the trust-on-first-use step that binds an unbound account to the OIDC subject presenting its name. `True` refuses that adoption unless the token carries `email_verified: true`. Defaults to `False`, which accepts a token that simply omits the claim, so deployments whose provider never sends it keep working across the upgrade. Independently of this setting, a token stating `email_verified: false` never adopts an account: the provider is saying the profile it just sent was never validated, which makes `preferred_username` no more trustworthy than the address, and either claim would otherwise be enough to be handed someone else's account. Neither setting affects a subject already bound to an account (the binding itself is the proof of identity) nor the creation of a brand-new account (nothing is being taken over).