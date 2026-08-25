#!/bin/bash

#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.




target=${1-:app}

if [[ -z $LOG_LEVEL ]]; then
  LOG_LEVEL='info'
fi

# ----------------------------------------------------------------------
# Operator-supplied CA bundle install.
#
# Two supported layouts under /etc/iris-ca/ (bind-mounted from
# ./certificates/ca-bundle/ per docker-compose.base.yml):
#
#   1. Legacy — a single concatenated bundle at
#      /etc/iris-ca/extra-ca.crt. Preserved as-is for backward
#      compatibility with existing deployments.
#
#   2. Drop-in — one or more *.crt / *.pem files placed anywhere
#      under /etc/iris-ca/. Each is installed into the OS trust store
#      individually. No manual concatenation with the system roots
#      required — update-ca-certificates ADDS to the store, it does
#      not replace it (unlike REQUESTS_CA_BUNDLE / SSL_CERT_FILE).
#
# Registering CAs at the OS level makes *every* TLS library inside
# the image trust them (requests, urllib3, the oic library, ldap,
# ...). Without this, setting REQUESTS_CA_BUNDLE only fixes the
# `requests` calls — the `oic` library opens its own session that
# ignores the env var and fails on internal CAs at the token
# endpoint.
#
# Idempotent: safe on every boot, tolerates the directory being
# missing or the operator not opting in.
_iris_ca_installed_any=0

if [[ -s /etc/iris-ca/extra-ca.crt ]]; then
  if [[ ! -f /usr/local/share/ca-certificates/iris-extra-ca.crt ]] \
     || ! cmp -s /etc/iris-ca/extra-ca.crt /usr/local/share/ca-certificates/iris-extra-ca.crt; then
    printf "Installing operator CA bundle into system trust store...\n"
    cp /etc/iris-ca/extra-ca.crt /usr/local/share/ca-certificates/iris-extra-ca.crt
    _iris_ca_installed_any=1
  fi
fi

if [[ -d /etc/iris-ca ]]; then
  while IFS= read -r -d '' ca_src; do
    # Skip the legacy single-file bundle — already handled above.
    [[ "${ca_src}" == "/etc/iris-ca/extra-ca.crt" ]] && continue
    # Flatten nested paths into a unique filename so multiple CAs
    # from subdirectories don't collide in the destination dir.
    rel="${ca_src#/etc/iris-ca/}"
    safe_name="iris-dropin-${rel//\//_}"
    # update-ca-certificates only picks up files ending in .crt.
    [[ "${safe_name}" == *.crt ]] || safe_name="${safe_name}.crt"
    dst="/usr/local/share/ca-certificates/${safe_name}"
    if [[ ! -f "${dst}" ]] || ! cmp -s "${ca_src}" "${dst}"; then
      printf "Installing drop-in CA: %s\n" "${rel}"
      cp "${ca_src}" "${dst}"
      _iris_ca_installed_any=1
    fi
  done < <(find /etc/iris-ca -maxdepth 4 -type f \( -name '*.crt' -o -name '*.pem' \) -print0 2>/dev/null)
fi

if [[ "${_iris_ca_installed_any}" -eq 1 ]]; then
  update-ca-certificates --fresh >/dev/null 2>&1 || \
    printf "WARN: update-ca-certificates failed (CAs not registered)\n"
fi

# ----------------------------------------------------------------------
# Point Python's HTTPS libraries at the OS trust store.
#
# `requests`, `urllib3`, and any library that goes through them (the
# `oic` OIDC client, webhook integrations, ...) default to certifi's
# bundle (/opt/venv/.../certifi/cacert.pem) — a hard-coded copy of
# Mozilla's public roots that does NOT include any CA we just
# installed. Setting REQUESTS_CA_BUNDLE / SSL_CERT_FILE to Debian's
# consolidated bundle (which update-ca-certificates rebuilt above)
# makes every Python HTTPS call trust the OS store, so the drop-in
# CAs are effective.
#
# Only set when the operator hasn't pinned these env vars themselves,
# so existing deployments that point at a bespoke bundle continue to
# work unchanged.
if [[ -z "${REQUESTS_CA_BUNDLE:-}" ]] && [[ -f /etc/ssl/certs/ca-certificates.crt ]]; then
  export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
fi
if [[ -z "${SSL_CERT_FILE:-}" ]] && [[ -f /etc/ssl/certs/ca-certificates.crt ]]; then
  export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
fi

printf "Running ${target} ...\n"

trap 'pkill "^(celery|gunicorn)"; exit 0' TERM INT

if [[ "${target}" == iris-worker ]] ; then
    if [[ -z $NUMBER_OF_CHILD ]]; then
        celery -A app.celery worker -E -B -l $LOG_LEVEL &
    else
        celery -A app.celery worker -c $NUMBER_OF_CHILD -E -B -l $LOG_LEVEL &
    fi
else
    # One-shot database bootstrap, in the foreground, before any
    # worker starts. `post_init` (tables, alembic upgrade, base data,
    # admin user, demo data) used to run at `app/__init__.py` module
    # scope; with `--preload` dropped that meant one full pass per
    # gunicorn worker — 4 per boot, serialized by a Postgres advisory
    # lock, the last 3 no-ops.
    #
    # Hard gate: on failure IRIS must not serve traffic against a
    # half-migrated schema. Exiting here surfaces a single clean
    # traceback and lets the container's restart policy retry, instead
    # of 4 workers crash-looping on import.
    #
    # Only on this branch — the iris-worker target above must not
    # bootstrap the database. `post_init.run()` already self-gated on
    # $IRIS_WORKER, so celery never did the work anyway.
    printf "Running database initialisation ...\n"
    if ! python -m scripts.run_post_init; then
      printf "Database initialisation failed. IRIS not started.\n" >&2
      exit 1
    fi

    # gevent worker: cooperative concurrency, one greenlet per open
    # connection. `wsgi:app` (source/wsgi.py) does gevent.monkey.patch_all()
    # + psycogreen.gevent.patch_psycopg() BEFORE `from app import app`
    # so socket / ssl / threading / psycopg2 are all patched by the
    # time Flask-SocketIO's `async_mode='gevent'` engages.
    #
    # `--preload` would import the app ONCE in the arbiter and fork
    # into workers; see the note below for why it is not used. Schema
    # creation is no longer a factor either way — the app no longer
    # touches the database at import time, the init step above owns it.
    # Gunicorn's gevent worker re-runs monkey-patching post-fork so
    # cooperative concurrency still works in each child.
    #
    # 4 workers × 1000 greenlets = 4000 concurrent sockets before we
    # need horizontal scale. Timeout raised to 300s to give the
    # chatbot's LLM streaming turns headroom.
    # `geventwebsocket.gunicorn.workers.GeventWebSocketWorker` extends
    # the plain gevent worker with the WS handshake logic — plain
    # `--worker-class gevent` refuses the /socket.io/ WS upgrade with
    # NS_ERROR_WEBSOCKET_CONNECTION_REFUSED (polling fallback still
    # works, but that's not the point of gevent). This is the standard
    # Flask-SocketIO + gevent recipe.
    # `--config scripts/gunicorn-cfg.py` wires the `post_fork` hook
    # that disposes the SQLAlchemy engine after each worker forks —
    # required alongside `--preload` to prevent workers from sharing
    # inherited psycopg2 connections (protocol desync → "lost sync
    # with server" fatal). Flags below override the config file's
    # `bind` / `workers` / `timeout` / `worker-class` — the config
    # file exists mostly for the hook now.
    # NOTE: `--preload` deliberately dropped. With preload, the arbiter
    # imports `wsgi:app` and runs `post_init.run()` at module scope,
    # which opens psycopg2 sockets. Those live fds get dup()'d into
    # every worker at fork(); a fresh psycopg2 connection in worker A
    # can end up sharing an fd number with an inherited-but-forgotten
    # socket in worker B, and `gevent.socket.wait_read` reads bytes off
    # the wrong wire → "lost synchronization with server" desync.
    # Without preload, each worker imports the app independently after
    # fork, so no fd inheritance is possible. Importing the app is now
    # side-effect-free with respect to the database, so N independent
    # imports cost nothing beyond the imports themselves.
    gunicorn wsgi:app --config scripts/gunicorn-cfg.py --bind 0.0.0.0:8000 --timeout 300 --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --worker-connections 1000 -w 4 --log-level=info &
fi

while :; do tail -f /dev/null & wait $!; done
