# TheHive 4 → IRIS v3 migration

One-way migration script. Reads TheHive 4 over its REST API v0, writes into an
IRIS backend Postgres database and datastore.

## Install

```
cd iris-backend/scripts/th4_to_iris
pip install -e .
```

## Run

```
th4-to-iris \
  --th4-url https://th4.example \
  --th4-apikey $TH4_API_KEY \
  --th4-orgs org-a,org-b \
  --iris-db postgresql+psycopg2://iris:iris@localhost:5432/iris_db \
  --datastore /opt/iris/datastore \
  --migration-user iris_migrator \
  --state ./migration.sqlite
```

Optional flags:

| Flag | Effect |
| --- | --- |
| `--th4-all-orgs` | Enumerate orgs via `GET /api/organisation` instead of `--th4-orgs` |
| `--include-deleted` | Migrate TH4 rows with `status: "Deleted"` (default: skip) |
| `--include-alerts` | Migrate TH4 alerts (default: skip) |
| `--dry-run` | Fetch and transform but do not commit |
| `--verify-only` | Run phase 7 (verify) only |
| `--force <entity>` | Re-migrate a specific entity type (cases, tasks, ...) |
| `--phase <n>` | Run only phase N (1..7). Default: all |
| `--parallel N` | Per-case parallelism (default: 4) |

## Phases

1. Reference data (severities, states, TLP, IOC types, asset types, task
   statuses, evidence types, tags, custom-attribute definitions).
2. Identities: TH4 orgs → IRIS `Client` (customer), TH4 users → IRIS `User` +
   `UserClient` + `UserGroup`.
3. Cases + case tags + owner.
4. Tasks + task logs (as `Notes` under a per-task `NoteDirectory`).
5. Observables split: `ioc=true` → `Ioc`, `ioc=false` → `CaseAssets`; matching
   `data` in both → `IocAssetLink`; attachments → `DataStoreFile`.
6. Alerts (opt-in).
7. Verify: counts per entity, sample cases, attachment SHA-256 audit.

## Idempotency

State is kept in a SQLite sidecar (`--state ./migration.sqlite`). Re-runs skip
already-migrated rows. Use `--force <entity>` to re-migrate a specific type.

## Non-migrated

Case Templates, Cortex analyzer report bodies, tag colours, MISP integration
state, timeline events (IRIS `cases_events` stays empty), TH4 case-merge
history (recorded on `custom_attributes.th4_merged_into`).
