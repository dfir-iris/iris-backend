# IRIS Backend

[![License: LGPL v3](https://img.shields.io/badge/License-LGPL_v3-blue.svg)](./LICENSE.txt)

The Python/Flask backend of [IRIS](https://github.com/dfir-iris/iris-web) — an open-source
collaborative platform for incident response teams. It serves the REST API, owns the Postgres
schema, and runs the Celery workers behind it.

> **Looking to run IRIS?** You are in the wrong repository.
> Go to **[dfir-iris/iris-web](https://github.com/dfir-iris/iris-web)** and follow its
> `docker compose` instructions. This repository is a submodule of it and does not start on its
> own — it has no `docker-compose.yml`, no frontend, and no reverse proxy.

User documentation lives at [docs.dfir-iris.org](https://docs.dfir-iris.org). There is a live
demo at [preview.dfir-iris.org](https://preview.dfir-iris.org).

Current version: **v3.0.0-beta.2** (`source/app/configuration.py`). Betas are not
production-ready.

## Where IRIS v3 lives

| Repository | Contents |
| --- | --- |
| [`iris-web`](https://github.com/dfir-iris/iris-web) | Meta-repo. `docker-compose.yml`, `.env.example`, deployment docs, end-to-end CI. **Start here.** |
| `iris-backend` (this one) | Flask REST API, Celery workers, Postgres schema and migrations. |
| [`iris-frontend`](https://github.com/dfir-iris/iris-frontend) | SvelteKit UI. |

## What is in this repository

```
source/app/            the Flask application
  blueprints/          HTTP surface; rest/v2/ is the current REST API.
                       Authorization decorators live here and only here.
  business/            domain processing, one module per domain
  datamgmt/            persistence; the only layer that talks to the DB engine
  models/              SQLAlchemy models and business objects
  schema/              marshmallow (de)serialisation
  iris_engine/         cross-cutting engine: modules, tasker, notifications,
                       collab (Yjs), llm, mail, access_control, observability, backup
  alembic/             92 migration revisions, applied automatically at boot
source/tests/          pytest unit tests (no database needed)
tests/                 REST API suite, runs against a live stack
tests_database_migration/   migration suite
docker/                Dockerfiles for the three images built here
deploy/                Helm chart and EKS manifests
```

The layering is enforced by [import-linter](https://import-linter.readthedocs.io); the contracts
are in `pyproject.toml` and a violating import fails CI. `architecture.md` has the long version.

## Developing

Python 3.12, matching the image (`docker/webApp/Dockerfile`).

```bash
python -m venv venv && source venv/bin/activate
pip install -r source/requirements.txt
```

You will want a running stack to work against. Either bring up the full one from `iris-web`,
or the backend-only one from here:

```bash
cp .env.tests.model .env
docker compose --file docker-compose.test.yml up --detach --wait
```

That gives you `rabbitmq`, `db`, `app` and `worker` — no nginx, no frontend — with the API on
`http://127.0.0.1:8000`. After changing application code:

```bash
docker compose --file docker-compose.test.yml restart app
```

Configuration is by environment variable; `CONFIGURATION.md` is the full reference.

## Static checks

These are the CI gates. They are fast and need no database.

```bash
ruff check
PYTHONPATH=source lint-imports     # layering contracts
vulture                            # dead code; allow-list in .vulture.ignore
```

Every `rest/v2` route carries an `@api_doc(...)` decorator that feeds the OpenAPI generator.
**If you change a v2 route or schema, regenerate the spec and commit it** — CI diffs it and
fails on drift:

```bash
cd source && python -m scripts.generate_openapi \
    --output app/blueprints/rest/openapi.generated.yaml
```

## Tests

Three suites, in increasing order of what they need.

**Unit tests** — `source/tests/`, pure pytest, no stack required. Not yet wired into CI, and
`pytest` is not in `source/requirements.txt`, so install it yourself:

```bash
pip install pytest
set -a && . ./.env.tests.model && set +a
PYTHONPATH=source python -m pytest source/tests
```

**REST API tests** — `tests/`, driven over HTTP against a live stack (see *Developing* above for
bringing one up). There is a harness in `tests/iris.py`; `tests/README.md` has the details.

```bash
cd tests && python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m unittest --verbose
python -m unittest tests_rest_assets.TestsRestAssets.test_create_asset_should_return_201
```

**Migration tests** — `tests_database_migration/`, restores database dumps from older versions
and checks that the migrations bring them up to the current schema. Own virtualenv, same
`python -m unittest --verbose`.

End-to-end tests against the UI are not here — they live in `iris-web/.github/workflows/e2e.yml`.

## Database migrations

Any schema change needs an Alembic revision. They are applied automatically when the app boots,
before gunicorn binds, which is why a cold start is slow.

```bash
cd source && alembic -c app/alembic.ini revision -m "what changed"
```

## Images

Three images are built from `docker/` and published to GHCR on pushes to `develop` / `master`
and on `v*.*.*` tags:

| Context | Image | Workflow |
| --- | --- | --- |
| `docker/webApp/Dockerfile` | `ghcr.io/dfir-iris/iris-backend` | `build-app.yml` |
| `docker/db` | `ghcr.io/dfir-iris/iris-db` | `build-db.yml` |
| `docker/nginx` | `ghcr.io/dfir-iris/iris-nginx` | `build-nginx.yml` |

`iris-web`'s `docker-compose.yml` consumes them. `ci.yml` runs the static checks and both
`unittest` suites; `notify-meta-repo.yml` tells `iris-web` to run its end-to-end suite once an
image is published.

`deploy/` is published as a Helm chart by `chart-releaser.yml`, but its `appVersion` is still
`2.4.5` — it targets IRIS v2 and has not been updated for v3. Docker Compose from `iris-web` is
the supported path for the beta.

## Contributing

Work off `develop` and open pull requests against `develop` — GitHub will default to `master`,
so change it. `master` is the last released version.

Read [`CONTRIBUTING.md`](./CONTRIBUTING.md) and [`CODESTYLE.md`](./CODESTYLE.md) before your
first pull request. Security issues go to [report@dfir-iris.org](mailto:report@dfir-iris.org),
not to the issue tracker — see [`SECURITY.md`](./SECURITY.md).

## Help

[Discord](https://discord.gg/76tM6QUJza) · [mail](mailto:contact@dfir-iris.org) ·
[Matrix](https://matrix.to/#/#dfir-iris:matrix.org) ·
[Twitter](https://twitter.com/dfir_iris)

## License

[LGPL v3](./LICENSE.txt).

Special thanks to Deutsche Telekom Security GmbH for sponsoring us.
