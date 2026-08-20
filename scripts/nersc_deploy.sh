#/bin/bash

podman login registry.nersc.gov
podman-hpc build -t registry.nersc.gov/m5115/kb-mcp:latest .
podman-hpc push registry.nersc.gov/m5115/kb-mcp:latest
