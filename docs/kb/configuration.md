# Knowledge Base Configuration

Configuration for the knowledge base module (`test_mcp.kb`).

For shared configuration (logging, data directory), see [Configuration](../configuration.md).

All configuration is done via environment variables in the `.env` file.

## Database Configuration

The knowledge base supports both PostgreSQL (production) and SQLite (development).

### PostgreSQL Configuration

#### DATABASE_URL
Complete PostgreSQL connection URL (takes precedence over individual components).

```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

**Note:** If `DATABASE_URL` is set, individual `DB_*` variables are ignored.

#### DB_HOST
PostgreSQL server hostname.

```bash
DB_HOST=localhost
```

#### DB_PORT
PostgreSQL server port.

```bash
DB_PORT=5432
```

**Default:** `5432`

#### DB_NAME
PostgreSQL database name.

```bash
DB_NAME=test_mcp
```

**Default:** `test_mcp`

#### DB_USER
PostgreSQL username.

```bash
DB_USER=postgres
```

#### DB_PASSWORD
PostgreSQL password.

```bash
DB_PASSWORD=your_password
```

**Connection Priority:**
1. `DATABASE_URL` (if set)
2. Individual `DB_*` components (if all required are set)
3. SQLite (fallback)

### SQLite Configuration

#### SQLITE_DB_PATH
Path to SQLite database file (used when PostgreSQL is not configured).

```bash
SQLITE_DB_PATH=data/kb.db
```

**Default:** `data/kb.db` (relative to project root)

**Note:** The directory will be created automatically if it doesn't exist.

### Database Connection Settings

#### DB_ECHO
Enable SQLAlchemy query logging (useful for debugging).

```bash
DB_ECHO=false  # Disable query logging (default)
DB_ECHO=true   # Enable query logging
```

**Default:** `false`

**Note:** When enabled, all SQL queries are logged to the console.

#### DB_POOL_SIZE
Connection pool size for PostgreSQL (SQLite ignores this).

```bash
DB_POOL_SIZE=5
```

**Default:** `5`

#### DB_MAX_OVERFLOW
Maximum overflow connections for PostgreSQL (SQLite ignores this).

```bash
DB_MAX_OVERFLOW=10
```

**Default:** `10`

## Deduplication Configuration

### KB_DEDUPLICATION_LEVEL
Controls how duplicate documents are handled when adding to the knowledge base.

```bash
KB_DEDUPLICATION_LEVEL=2
```

**Default:** `2`

**Options:**
- `0` - **Insert duplicates, no warnings**: Always insert new documents, even if duplicates exist
- `1` - **Insert with warnings**: Insert duplicates but log warnings for existing source_id+doc_id or content_hash
- `2` - **Overwrite if same hash** (default): Update existing document if content_hash matches
- `3` - **Overwrite hash with warnings**: Same as level 2, but also warn about existing source_id+doc_id with different hash
- `4` - **Overwrite all matches**: Update existing document if either source_id+doc_id matches OR content_hash matches

**Behavior Details:**

**Level 0:** No duplicate checking. All documents are inserted, potentially creating duplicates.

**Level 1:** Checks for duplicates and logs warnings, but still inserts all documents:
- Warns if document with same `source_id` + `doc_id` exists
- Warns if document with same `content_hash` exists

**Level 2 (Default):** Updates existing document if `content_hash` matches:
- If a document with the same `content_hash` exists, it is updated with new data
- If no hash match, document is inserted
- No warnings for existing `source_id` + `doc_id` with different hash

**Level 3:** Same as level 2, but also warns about existing `source_id` + `doc_id`:
- Updates existing document if `content_hash` matches
- Warns if `source_id` + `doc_id` exists but `content_hash` is different

**Level 4:** Updates existing document if either condition matches:
- If `source_id` + `doc_id` matches, update that document
- Otherwise, if `content_hash` matches, update that document
- Otherwise, insert new document

**Note:** The deduplication level can also be specified per-operation using the `dedup_level` parameter in `add()`, `add_many()`, and `add_from_path()` functions.

## Example Configurations

### PostgreSQL (Production)

```bash
# Option 1: Using DATABASE_URL
DATABASE_URL=postgresql://user:password@localhost:5432/test_mcp

# Option 2: Using individual components
DB_HOST=localhost
DB_PORT=5432
DB_NAME=test_mcp
DB_USER=postgres
DB_PASSWORD=your_password

# Optional: Connection pool settings
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_ECHO=false
```

### SQLite (Development)

```bash
# Uses SQLite automatically when PostgreSQL is not configured
SQLITE_DB_PATH=data/kb.db
DB_ECHO=false  # Enable for debugging
```

**Note:** SQLite is automatically used when:
- `DATABASE_URL` is not set, AND
- Not all of `DB_HOST`, `DB_USER`, `DB_PASSWORD` are set

See [Configuration](../configuration.md) for shared settings like `DATA_DIR` and logging.

