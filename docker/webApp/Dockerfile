#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

#################
# COMPILE IMAGE #
#################
FROM python:3.12 AS compile-image
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && (apt-get purge -y --auto-remove 'imagemagick*' 'libmagickcore-*' 'libmagickwand-*' || true) \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
# Make sure we use the virtualenv:
ENV PATH="/opt/venv/bin:$PATH"

COPY source/dependencies /dependencies
COPY source/requirements.txt /

RUN pip3 install -r requirements.txt

###############
# BUILD IMAGE #
###############
FROM python:3.12 AS iriswebapp
ENV PYTHONUNBUFFERED=1 DOCKERIZED=1

COPY --from=compile-image /opt/venv /opt/venv

# Make sure we use the virtualenv:
ENV PATH="/opt/venv/bin:$PATH"

# The iris-db image runs PostgreSQL 18, and pg_dump refuses to dump a newer major
# server version. Debian trixie ships pg_dump 17, so install the 18 client from the
# PostgreSQL PGDG repository instead (a newer client can also dump older servers).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg p7zip-full pgp rsync \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && (apt-get purge -y --auto-remove 'imagemagick*' 'libmagickcore-*' 'libmagickwand-*' || true) \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /iriswebapp/
RUN mkdir -p /home/iris/certificates/web_certificates
RUN mkdir -p /home/iris/user_templates
RUN mkdir -p /home/iris/server_data
RUN mkdir -p /home/iris/server_data/backup
RUN mkdir -p /home/iris/server_data/updates
RUN mkdir -p /home/iris/server_data/custom_assets
RUN mkdir -p /home/iris/server_data/datastore

WORKDIR /iriswebapp

COPY docker/webApp/iris-entrypoint.sh .
COPY docker/webApp/wait-for-iriswebapp.sh .
COPY ./source .

RUN chmod +x iris-entrypoint.sh
RUN chmod +x wait-for-iriswebapp.sh
