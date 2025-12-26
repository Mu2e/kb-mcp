#!/bin/bash

ENV_PATH=".venv"
KERNEL_NAME=kb-mcp

if [ ! -d "$ENV_PATH" ]; then
    echo "Error: $ENV_PATH directory not found."
    exit 1
fi

echo "Installing ipykernel in $ENV_PATH..."
$ENV_PATH/bin/pip install ipykernel

echo "Registering kernel: $KERNEL_NAME"
$ENV_PATH/bin/python -m ipykernel install --user --name="$KERNEL_NAME" --display-name="Python ($KERNEL_NAME)"

echo "Done! You can now select '$KERNEL_NAME' in Jupyter."
