# Parser CLI

## Installation

```bash
pip install -e ".[parse]"
```

The `kb-parse` command-line tool provides document parsing and image description generation.

## Commands

### Parse Document (default)

Parse a document and extract text. This is the default command.

```bash
# Basic usage
kb-parse document.pdf

# With options
kb-parse parse document.pdf --json --preview 1000
```

### Generate Image Description

Generate an LLM-based description for an image file.

```bash
# Basic usage (requires OPENAI_API_KEY)
kb-parse image test.png

# With custom model
kb-parse image test.png --model gpt-4o --json
```

## Options

See `kb-parse --help` for full option list.
