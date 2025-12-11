# Configuration 

KB configurations are managed through environment variables. Settings in `.env` are imported through `dotenv`. An example environment file [.env.example](env.example.md) can be copied (to `.env`) and modified as desired. 

The codebase uses centralized configuration management via `config.py`:

## Usage Examples

```python
from test_mcp.config import (
    get_database_url,
    get_server_config,
    get_llm_config,
    get_parser_config,
)

# Access configuration
db_url = get_database_url()
server_config = get_server_config()
```

## Configurations

::: test_mcp.config
    options:
      filters: ["^get_"]  # Only show functions starting with 'get_'
      show_root_heading: false
      heading_level: 4