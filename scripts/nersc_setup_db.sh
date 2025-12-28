#!/bin/bash
# nersc_setup_db.sh

# For the moment lets use SCRATCH, maybe we want to store/backup on CFS?
DB_ROOT="$SCRATCH/kb-mcp-db"
DB_DATA_DIR="$DB_ROOT/pgdata"

# Container Configuration
DB_IMAGE="docker.io/pgvector/pgvector:pg16"
DB_CONTAINER="kb-mcp-postgres-$USER"
DB_PORT_EXTERNAL=54321

# Settings
PROJECT_ID="${PROJECT_ID:-m5115}"
SECRETS_DIR="/global/cfs/cdirs/$PROJECT_ID/secrets"
SECRETS_FILE="$SECRETS_DIR/kb-mcp.env"

check_remote_connection() {
    local host=$1
    local port=$2
    if [ -z "$host" ]; then return 1; fi
    # Try to open a TCP connection (timeout 1s)
    timeout 1 bash -c "cat < /dev/null > /dev/tcp/$host/$port" 2>/dev/null
}

# Source the settings file to get the DB_HOST and DB_PORT
if [ -f "$SECRETS_FILE" ]; then
    source "$SECRETS_FILE"
    if [ -n "$DB_HOST" ] && [ -n "$DB_PORT" ]; then
        if check_remote_connection "$DB_HOST" "$DB_PORT"; then
            echo "Database is already running on $DB_HOST:$DB_PORT"
            echo "----------------------------------------------------------------"
            echo "To connect to the database (localhost:$DB_PORT_EXTERNAL) from your local machine, use port forwarding:"
            echo "  ssh -J $USER@perlmutter.nersc.gov -L $DB_PORT:localhost:$DB_PORT $CURRENT_HOST"
            echo "----------------------------------------------------------------"
            return 0
        fi
    fi
fi
CURRENT_HOST=$(hostname -f)
echo "Starting the database on $CURRENT_HOST:$DB_PORT_EXTERNAL"

update_env() {
    local key=$1
    local val=$2
    local file=$3
    # Update existing key or append new one
    if grep -q "^$key=" "$file"; then
        sed -i "s|^$key=.*|$key=$val|" "$file"
    else
        echo "$key=$val" >> "$file"
    fi
}

# make sure we have the directories
mkdir -p "$DB_DATA_DIR"
mkdir -p "$SECRETS_DIR"
# lets make sure its only group readable (and not by others)
if [ -d "$SECRETS_DIR" ]; then
    chmod g+s "$SECRETS_DIR" 2>/dev/null      # Enforce Group Inheritance
    chmod 2770 "$SECRETS_DIR" 2>/dev/null      # User/Group=RWX, World=None
fi
if [ ! -f "$SECRETS_FILE" ]; then
    echo "Creating new secrets file..."
    touch "$SECRETS_FILE"
    chmod 640 "$SECRETS_FILE"
fi


# Create the database if it doesn't exist

# Only start on Login Nodes
if [[ $(hostname) != *"login"* ]]; then
    echo "Database ($DB_HOST) is unreachable and this is not a Login Node."
    echo "   Please run setup on a Login Node to restart the database."
    return 1
fi

# Cleanup
podman-hpc rm -f $DB_CONTAINER > /dev/null 2>&1

# Get the container image if it doesn't exist
if ! podman-hpc images --format "{{.Repository}}:{{.Tag}}" | grep -q "$DB_IMAGE"; then

        echo "Image $DB_IMAGE not found. Pulling from Docker Hub..."
        podman-hpc pull "$DB_IMAGE"
        podman-hpc migrate "$DB_IMAGE"
fi



if [ -n "$DB_PASSWORD" ]; then
    echo "Using existing DB password from secrets file."
else
    echo "Generating new DB password and storing in $SECRETS_FILE"
    DB_PASSWORD=`openssl rand -base64 12`
    echo "DB_PASSWORD=$DB_PASSWORD" >> "$SECRETS_FILE"
fi


# Start Container
# Bind 0.0.0.0 to allow other nodes to connect
# POSTGRES_PASSWORD is only used if no datafile exists yet (aka the first time)
echo "Starting container $DB_CONTAINER"
podman-hpc run -d --rm \
    --name "$DB_CONTAINER" \
    --userns=keep-id \
    --network=host \
    -e PGPORT=$DB_PORT_EXTERNAL \
    -e POSTGRES_PASSWORD=$DB_PASSWORD \
    -v "$DB_DATA_DIR":/var/lib/postgresql/data \
    "$DB_IMAGE"

echo "Waiting 5s for boot..."
sleep 5

update_env() {
    local key=$1
    local val=$2
    local file=$3
    # If key exists, replace it using sed. If not, append it.
    if grep -q "^$key=" "$file"; then
        # Mac/BSD sed requires '', Linux does not. We assume Linux (NERSC).
        sed -i "s|^$key=.*|$key=$val|" "$file"
    else
        echo "$key=$val" >> "$file"
    fi
}

# lets make sure we have the vector extension
podman-hpc exec "$DB_CONTAINER" psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"

if [ $? -eq 0 ]; then
    echo "Extension 'vector' enabled."
else
    echo "Warning: Could not enable vector extension. Check container logs."
fi

FULL_URL="postgresql://postgres:$DB_PASSWORD@$CURRENT_HOST:$DB_PORT_EXTERNAL/postgres"
update_env "DB_HOST"     "$CURRENT_HOST"     "$SECRETS_FILE"
update_env "DB_PORT"     "$DB_PORT_EXTERNAL" "$SECRETS_FILE"
update_env "DB_USER"     "postgres"          "$SECRETS_FILE"
update_env "DB_NAME"     "postgres"          "$SECRETS_FILE"
update_env "DB_URL"       "$FULL_URL"        "$SECRETS_FILE"

echo "Database started on $CURRENT_HOST. '$SECRETS_FILE' updated. See DB_URL for connection string."
echo "----------------------------------------------------------------"
echo "To connect to the database (localhost:$DB_PORT_EXTERNAL) from your local machine, use port forwarding:"
echo "  ssh -J $USER@perlmutter.nersc.gov -L $DB_PORT_EXTERNAL:localhost:$DB_PORT_EXTERNAL $CURRENT_HOST"
echo "----------------------------------------------------------------"
