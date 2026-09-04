
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

## IRIS

- `IRIS_SECRET_KEY` - The secret key used by Flask.
- `IRIS_SECURITY_PASSWORD_SALT` - ??
- `IRIS_ALLOW_PRIVATE_EGRESS` - `True` lets report templates reference images on private, loopback or link-local addresses. Defaults to `False`: the renderer fetches those URLs from inside the backend network, so allowing them exposes internal services and the cloud metadata endpoint to whoever can upload a template.
- `IRIS_LOGIN_MAX_ATTEMPTS` - Consecutive failed logins tolerated for one account before it is locked out. Defaults to `10`.
- `IRIS_LOGIN_MAX_ATTEMPTS_PER_CLIENT` - Consecutive failed logins tolerated from one client address, across all accounts. Defaults to `50`. Deliberately looser than the per-account ceiling because a whole office can share one source address; raise it if a reverse proxy is in front and `remote_addr` is the proxy.
- `IRIS_LOGIN_LOCKOUT_SECONDS` - How long a lockout lasts once either ceiling is crossed. Defaults to `900` (15 minutes). Counting is in-process and per worker, so this raises the cost of brute force rather than making it impossible.