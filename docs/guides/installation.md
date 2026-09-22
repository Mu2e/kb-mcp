# Installation

For instructions on connecting an MCP client to a remote MCP server (this codebase), see the [mc client documentation](https://github.com/HEP-KE/kb-mcp/docs).

## 0. Clone the Repository

Clone this repository using:

```bash
git clone https://github.com/HEP-KE/kb-mcp.git
cd kb-mcp
```

## 1. Python Dependencies

### (Optional) Create Virtual Environment

```bash
# (Recommended) Create a new Python 3.11+ virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### Install Core Dependencies

The core install contains everything needed to **serve** a knowledge base
with the default configuration -- the MCP endpoint, the web UI, search/graph
reads, and embedding the query:

```bash
pip install -e .
```

This includes `sentence-transformers`, because the default embedding provider
is `st` (`BAAI/bge-small-en-v1.5`). A query has to be embedded in the same
vector space as the stored chunks, so the default install has to be able to
run the default configuration.

!!! note "Install CPU-only torch first on a server"
    On Linux, `sentence-transformers` resolves `torch` to the CUDA build from
    PyPI -- several GB of NVIDIA wheels. Embedding one query at a time is CPU
    work, so for any deployment (and most laptops) install the `+cpu` wheel
    first and the rest follows:

    ```bash
    pip install --index-url https://download.pytorch.org/whl/cpu torch
    pip install -e .
    ```

    That brings a full environment to roughly 1.3 GB instead of several GB.
    `scripts/deploy-mu2e.sh` does this automatically.

### Ingestion extra

Adding documents to the knowledge base -- `kb-import`, `kb-parse`,
`kb tools ingest` -- needs the parser stack, which serving never imports:

```bash
pip install -e ".[ingest]"      # or ".[all]", which is the same plus nothing else
```

`python-magic` in this extra also needs the `libmagic` system library. A
server that only answers queries against an already-populated knowledge base
does not need any of it.

### Optional Dependencies

```bash
# Plotting for the scripts under analysis/
pip install -e ".[analysis]"

# Add Google Cloud Platform support (for Firestore session storage)
pip install -e ".[gcp]"

# Add documentation tools (for building docs)
# (Optional) Build and preview the documentation locally
pip install -e ".[doc]"
cd docs
mkdocs serve
```

## 2. Environment Configuration

### Create Environment File

```bash
# Copy the example environment file
cp .env.example .env
```

### Configure Environment Variables

Edit your `.env` file with your configuration. See [Configuration Reference](../reference/config.md) for detailed explanations of all available environment variables.

**Key settings include:**

- **Authentication**: See the [Authentication Guide](authentication.md) for complete setup instructions
  - **OAuth** (GitHub or Globus): Required for web interface, optional for MCP clients. See [Authentication](authentication.md) for details.
  - **API Keys**: Always available for MCP clients. See [API Keys](api-keys.md) for details. 
  - **DISABLE_AUTH**: Development mode only (or localhost binding)
- **OPENAI_BASE_URL**: Base URL for OpenAI-compatible LLM interface
- **OPENAI_API_KEY**: Optional API key for `OPENAI_BASE_URL`
- **Database Configuration**: See [Database Setup](#3-database-setup) below

### ALCF (Argonne Leadership Computing Facility) Setup

If you're using [ALCF's inference service](https://docs.alcf.anl.gov/services/inference-endpoints), you can use the provided setup script to automatically configure your `.env` file with the appropriate credentials.

The script will:
- Check if you have an active ALCF token
- Update your `.env` file with `OPENAI_API_KEY` and `OPENAI_BASE_URL` for the specified cluster
- If `OPENAI_BASE_URL` is already set correctly, only `OPENAI_API_KEY` will be updated

**Usage:**

```bash
# Use default .env file and sophia cluster
./scripts/setup_alcf.sh --help
```

## 3. Database Setup

The knowledge base supports both **SQLite** (for development) and **PostgreSQL** (for production).

### SQLite (Default, Development)

SQLite is the default and requires no additional setup. The database file will be created automatically at `data/kb.db`.

### PostgreSQL (Production)

For production use, configure PostgreSQL connection in `.env`:

```bash
DB_HOST=localhost
DB_PORT=5432
DB_USER=user
DB_PASSWORD=password
DB_NAME=dbname
DB_SCHEMA=public # defaults to public
```

### Test Python Import

```bash
python -c "import kb_mcp; print('Installation successful!')"
```

### Test CLI Commands

```bash
# Test KB CLI
kb --help
```

## 6. Next Steps

After installation, you may want to:


### **Start the Server**

   ```bash
   kb-server
   ```
   The default setting start the server at [localhost:8443](https://localhost:8443) which you can access with your browser.

   **Note**: For production (if bound other than localhost), use an authentification scheme with https.


### **Connect an MCP Client**
   See the [MCP client documentation](https://github.com/corrodis/mc) for instructions on connecting to a remote MCP server.

### **Add New Documents**

See the [Adding Documents Guide](adding-documents.md) for instructions on how to add new documents to your knowledge base.


### **Use the CLI**

   For detailed usage of all available CLI tools, see the [CLI Guide](cli.md).

   For example, you can test the knowledge base CLI:
   ```bash
   kb --help
   ```


### Explore Documentation
   
   - [Authentication Guide](authentication.md) - OAuth, API keys, and security
   - [API Keys Guide](api-keys.md) - Managing API keys
   - [Configuration Guide](../reference/config.md) - All configuration options
   - [Knowledge Base Guide](../reference/kb.md) - Using the KB module
   - [Server Guide](../reference/server.md) - Running the MCP server
   - [Web Interface Guide](web-interface.md) - Using the web UI

