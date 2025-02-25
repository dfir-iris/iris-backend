#!/usr/bin/env bash

###############################################################################
# Bash script to manage Docker Compose tasks for a Flask app (iriswebapp).
#
# Usage:
#   ./iris_helper.sh [options]
#
#   If run with no options, the script will prompt for all inputs interactively.
#
# Options:
#   -e, --env-file <path>       Path to the .env file to use.
#   -m, --mode <dev|production> Mode to run (development or production).
#   -r, --reset-db              Reset database (dev mode only).
#   -b, --build                 Rebuild containers (dev mode only).
#   -s, --services <list>       Comma-separated list of services to start (dev).
#                               e.g. "app,worker,rabbitmq,nginx,db"
#   -l, --logs                  Print Docker logs after starting (dev mode only).
#   -v, --version <version>     Set version tags for containers (prod mode only).
#                               e.g. "2.4.20" or "2.5.0-beta"
#   -h, --help                  Show this help message.
#
###############################################################################

set -e

# ---------------------------
# DEFAULTS & CONSTANTS
# ---------------------------
DEFAULT_ENV_FILE=".env"
DEV_DOCKER_FILE="docker-compose.dev.yml"
PROD_DOCKER_FILE="docker-compose.yml"   # Adjust if your production file is different
VALID_VERSIONS=("2.4.20" "2.5.0-beta")   # Extend as needed

# If you have other containers in dev, adjust accordingly:
DEV_CONTAINERS=("app" "worker" "rabbitmq" "nginx" "db")

# ---------------------------
# HELPER FUNCTIONS
# ---------------------------

print_help() {
  sed -n '2,24p' "$0"  # prints lines 2-24 from this file (the usage block)
  exit 0
}

ask() {
  # Prompt for user input (yes/no). Default is 'no' if pressing Enter with no input.
  local prompt default reply

  if [ "${2:-}" = "Y" ]; then
    prompt="Y/n"
    default="Y"
  else
    prompt="y/N"
    default="N"
  fi

  # Ask the question
  read -r -p "$1 [$prompt] " reply

  # If user just presses Enter, $reply is empty => use default
  if [ -z "$reply" ]; then
    reply=$default
  fi

  # Return 0 for yes, 1 for no
  case "$reply" in
    [yY][eE][sS]|[yY]) return 0 ;;
    *)                 return 1 ;;
  esac
}

select_env_file() {
  # If ./.env is found, offer to use it. Otherwise, prompt for path.
  # In either case, let the user override by specifying a different path.

  local default="./.env"
  if [[ -n "$ENV_FILE" ]] && [[ -f "$ENV_FILE" ]]; then
    # If user already provided an ENV_FILE and it's valid, skip
    echo "Using .env file (from CLI arg): $ENV_FILE"
    return
  fi

  if [[ -f "$default" ]]; then
    echo "Found .env in the current directory: $default"
    if ask "Do you want to use $default?" "Y"; then
      ENV_FILE="$default"
    else
      read -r -p "Please enter the path to your .env file: " user_env_path
      if [[ ! -f "$user_env_path" ]]; then
        echo "Error: specified .env file does not exist."
        exit 1
      fi
      ENV_FILE="$user_env_path"
    fi
  else
    # If no default .env was found, ask user
    read -r -p "No .env found in current dir. Please provide path to your .env file: " user_env_path
    if [[ ! -f "$user_env_path" ]]; then
      echo "Error: specified .env file does not exist."
      exit 1
    fi
    ENV_FILE="$user_env_path"
  fi

  echo "Using .env file at: $ENV_FILE"
}

# Cross-platform sed approach for macOS (BSD) and Linux:
# Use '-i.bak' and remove the backup file afterward.
set_version_in_env() {
  local version="$1"
  local envfile="$2"

  echo "Setting NGINX_IMAGE_TAG, DB_IMAGE_TAG, and APP_IMAGE_TAG to '$version' in $envfile"

  sed -i.bak "s|^NGINX_IMAGE_TAG=.*|NGINX_IMAGE_TAG=$version|" "$envfile" && rm -f "$envfile.bak"
  sed -i.bak "s|^DB_IMAGE_TAG=.*|DB_IMAGE_TAG=$version|" "$envfile" && rm -f "$envfile.bak"
  sed -i.bak "s|^APP_IMAGE_TAG=.*|APP_IMAGE_TAG=$version|" "$envfile" && rm -f "$envfile.bak"
}

###############################################################################
# manage_running_containers_for_services:
#   In dev or prod, if we want to specifically check certain services,
#   we see if "iriswebapp_<service>" is running. Currently, we just
#   inform the user. You can adapt to ask or do something else if needed.
###############################################################################
manage_running_containers_for_services() {
  local services_list=("$@")
  for svc in "${services_list[@]}"; do
    local container_name="iriswebapp_${svc}"
    local container_id
    container_id=$(docker ps --filter "name=^${container_name}$" --format "{{.ID}}")
    if [[ -n "$container_id" ]]; then
      echo "Container '$container_name' is already running."
      echo "You may wish to stop/remove/restart from the command line, or proceed anyway."
    fi
  done
}

###############################################################################
# manage_all_running_iriswebapp_containers:
#   If any `iriswebapp_` containers are running, let the user apply a single
#   action to all of them: Stop, Remove, Restart, or Do nothing.
###############################################################################
manage_all_running_iriswebapp_containers() {
  local running_containers
  running_containers=$(docker ps --filter "name=^iriswebapp_" --format "{{.Names}}")

  if [[ -z "$running_containers" ]]; then
    echo "No 'iriswebapp_' containers are currently running."
    return
  fi

  echo "The following 'iriswebapp_' containers are currently running:"
  echo "$running_containers"
  echo "Choose an action to apply to ALL of these containers:"
  echo "1) Stop all"
  echo "2) Stop & Remove all (WILL DELETE ALL DATA FOREVER)"
  echo "3) Restart all"
  echo "4) Do nothing"

  local choice
  read -r -p "Enter choice [1-4]: " choice
  if [[ -z "$choice" ]]; then
    choice="4"  # default to "Do nothing" if empty
  fi

  local containers_arr=( $running_containers )
  case "$choice" in
    1)
      echo "Stopping all containers..."
      docker stop "${containers_arr[@]}"
      ;;
    2)
      echo "Stopping and removing all containers..."
      docker stop "${containers_arr[@]}"
      docker rm "${containers_arr[@]}"
      ;;
    3)
      echo "Restarting all containers..."
      docker restart "${containers_arr[@]}"
      ;;
    4)
      echo "Skipping..."
      ;;
    *)
      echo "Invalid choice: '$choice'. Skipping..."
      ;;
  esac
}

# ---------------------------
# PARSE ARGUMENTS
# ---------------------------
ENV_FILE=""
MODE=""
RESET_DB=false
REBUILD=false
SERVICES=""
PRINT_LOGS=false
VERSION=""

while [[ $# -gt 0 ]]; do
  case $1 in
    -e|--env-file)
      ENV_FILE="$2"
      shift; shift
      ;;
    -m|--mode)
      MODE="$2"
      shift; shift
      ;;
    -r|--reset-db)
      RESET_DB=true
      shift
      ;;
    -b|--build)
      REBUILD=true
      shift
      ;;
    -s|--services)
      SERVICES="$2"
      shift; shift
      ;;
    -l|--logs)
      PRINT_LOGS=true
      shift
      ;;
    -v|--version)
      VERSION="$2"
      shift; shift
      ;;
    -h|--help)
      print_help
      ;;
    *)
      echo "Unknown option: $1"
      print_help
      ;;
  esac
done

# ---------------------------
# STEP 1: Check existing iriswebapp_ containers
# ---------------------------
manage_all_running_iriswebapp_containers

# ---------------------------
# STEP 2: INTERACTIVE MODE SELECTION
# ---------------------------
if [[ -z "$MODE" ]]; then
  echo "Select mode: "
  echo "1) Development"
  echo "2) Production"
  read -r -p "Enter choice [1 or 2]: " mode_choice
  if [[ -z "$mode_choice" ]]; then
    # If empty, default to 1 => development
    mode_choice="1"
  fi

  case "$mode_choice" in
    1) MODE="development" ;;
    2) MODE="production"  ;;
    *) echo "Invalid choice. Exiting." ; exit 1 ;;
  esac
fi

# ---------------------------
# STEP 3: ENV FILE SELECTION
# ---------------------------
select_env_file

# ---------------------------
# DEVELOPMENT MODE LOGIC
# ---------------------------
if [[ "$MODE" == "development" ]]; then

  # If not specified via CLI, ask if we want to reset DB
  if ! $RESET_DB; then
    if ask "Do you want to reset the database (docker compose down -v)?" "N"; then
      RESET_DB=true
    fi
  fi
  if $RESET_DB; then
    echo "Bringing down containers and removing volumes..."
    docker compose --file "$DEV_DOCKER_FILE" --env-file "$ENV_FILE" down -v
  fi

  # If not specified via CLI, ask if we want to rebuild
  if ! $REBUILD; then
    if ask "Do you want to rebuild containers?" "N"; then
      REBUILD=true
    fi
  fi

  if $REBUILD; then
    echo "Which services do you want to rebuild? Available: ${DEV_CONTAINERS[*]}"
    echo "Enter 'all' for all containers, or space-separated list: e.g. 'app worker'."
    read -r -p "Services to rebuild: " rebuild_services
    if [[ -z "$rebuild_services" ]]; then
      # If empty, default to "all"
      rebuild_services="all"
    fi
    if [[ "$rebuild_services" == "all" ]]; then
      rebuild_services="${DEV_CONTAINERS[*]}"
    fi
    echo "Building containers: $rebuild_services"
    docker compose --file "$DEV_DOCKER_FILE" --env-file "$ENV_FILE" build $rebuild_services
  fi

  # If user didn't specify which services to start, ask:
  if [[ -z "$SERVICES" ]]; then
    echo "Which services do you want to start? Options: ${DEV_CONTAINERS[*]}"
    echo "Enter 'all' for all, or space-separated list: e.g. 'app worker'."
    read -r -p "Services to start: " start_services
    if [[ -z "$start_services" ]]; then
      start_services="all"
    fi
    if [[ "$start_services" == "all" ]]; then
      start_services="${DEV_CONTAINERS[*]}"
    fi
    SERVICES="$start_services"
  fi

  # Optionally check if they're already running & let user decide
  IFS=' ' read -r -a services_array <<< "$SERVICES"
  manage_running_containers_for_services "${services_array[@]}"

  echo "Starting services: $SERVICES"
  docker compose --file "$DEV_DOCKER_FILE" --env-file "$ENV_FILE" up -d $SERVICES

  # If user didn't specify logs option, ask
  if ! $PRINT_LOGS; then
    if ask "Do you want to tail logs?" "N"; then
      PRINT_LOGS=true
    fi
  fi
  if $PRINT_LOGS; then
    echo "Tailing logs. Press Ctrl+C to stop."
    docker compose --file "$DEV_DOCKER_FILE" --env-file "$ENV_FILE" logs -f
  fi

# ---------------------------
# PRODUCTION MODE LOGIC
# ---------------------------
elif [[ "$MODE" == "production" ]]; then

  # If user did not specify a version in the CLI, ask them
  if [[ -z "$VERSION" ]]; then
    echo "Which version do you want to run?"
    for i in "${!VALID_VERSIONS[@]}"; do
      echo "$((i+1))) ${VALID_VERSIONS[$i]}"
    done
    echo "$(( ${#VALID_VERSIONS[@]} + 1 )) ) Custom version"

    read -r -p "Enter choice (1-${#VALID_VERSIONS[@]}, or ${#VALID_VERSIONS[@]}+1 for custom): " version_choice
    if [[ -z "$version_choice" ]]; then
      # Default to 1 if empty => 2.4.20 (first in the array)
      version_choice=1
    fi
    if (( version_choice >= 1 && version_choice <= ${#VALID_VERSIONS[@]} )); then
      VERSION="${VALID_VERSIONS[$((version_choice-1))]}"
    else
      read -r -p "Enter your custom version tag: " custom_version
      VERSION="$custom_version"
    fi
  fi

  # Now set the version in .env (with cross-platform sed)
  set_version_in_env "$VERSION" "$ENV_FILE"

  # Optionally define production services if you want to check them individually
  ALL_PROD_SERVICES=("app" "worker" "rabbitmq" "nginx" "db")
  manage_running_containers_for_services "${ALL_PROD_SERVICES[@]}"

  echo "Starting production with version: $VERSION"
  docker compose --env-file "$ENV_FILE" -f "$PROD_DOCKER_FILE" up -d

  echo "Production containers are up."
  if ! $PRINT_LOGS; then
    if ask "Do you want to tail logs?" "N"; then
      PRINT_LOGS=true
    fi
  fi
  if $PRINT_LOGS; then
    echo "Tailing logs. Press Ctrl+C to stop."
    docker compose --file "$DEV_DOCKER_FILE" --env-file "$ENV_FILE" logs -f
  fi

else
  echo "Invalid mode selected: $MODE"
  exit 1
fi

exit 0
