# Parser Configuration

Configuration for the document parser module (`test_mcp.parser`).

For shared configuration (logging, LLM settings), see [Configuration](../configuration.md).

All configuration is done via environment variables in the `.env` file.

## Image Processing Configuration

### PARSE_IMAGE_ADDITIONAL_DOC
Create separate `Document` objects for extracted images (default: false).

```bash
PARSE_IMAGE_ADDITIONAL_DOC=false  # Images embedded in main document (default)
PARSE_IMAGE_ADDITIONAL_DOC=true    # Create separate image documents
```

**Default:** `false`

**Behavior:** When enabled, images extracted from documents (e.g., PDFs) are stored as separate `Document` objects with:
- Image binary data in the `binary` field
- LLM-generated description (if enabled) in the `text` field
- Parent document information in the `meta` field

### PARSE_IMAGE_LLM_DESCRIPTION
Generate LLM-based descriptions for extracted images (default: false).

```bash
PARSE_IMAGE_LLM_DESCRIPTION=false  # No LLM descriptions (default)
PARSE_IMAGE_LLM_DESCRIPTION=true   # Generate descriptions using OpenAI API
```

**Default:** `false`

**Requirements:**
- `OPENAI_API_KEY` must be set (see [Configuration](../configuration.md))
- Only applies when `PARSE_IMAGE_ADDITIONAL_DOC=true`

**Note:** This feature is currently only implemented for PDF documents.

### PARSE_IMAGE_DESCRIPTION_MODEL
OpenAI model to use for image description generation.

```bash
PARSE_IMAGE_DESCRIPTION_MODEL=gpt-4o-mini
```

**Default:** `gpt-4o-mini`

**Options:** Any OpenAI vision-capable model (e.g., `gpt-4o`, `gpt-4o-mini`, `gpt-4-vision-preview`)

### PARSE_IMAGE_DESCRIPTION_NUMWORKERS
Number of parallel workers for generating image descriptions.

```bash
PARSE_IMAGE_DESCRIPTION_NUMWORKERS=6
```

**Default:** `6`

**Note:** Higher values increase throughput but may hit API rate limits.

## Example Configuration

```bash
# Enable image extraction and LLM descriptions
PARSE_IMAGE_ADDITIONAL_DOC=true
PARSE_IMAGE_LLM_DESCRIPTION=true
PARSE_IMAGE_DESCRIPTION_MODEL=gpt-4o-mini
PARSE_IMAGE_DESCRIPTION_NUMWORKERS=6

# Required for LLM descriptions
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1  # Optional, for custom endpoints
```

See [Configuration](../configuration.md) for shared settings like `OPENAI_API_KEY` and `OPENAI_BASE_URL`.

