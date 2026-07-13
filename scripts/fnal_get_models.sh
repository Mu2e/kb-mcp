#!/bin/bash

# Define Endpoints
OLLAMA_URL="https://ollama.fnal.gov/api/tags"
VLLM_URL="https://vllm.fnal.gov/v1/models"

echo "================================================================================"
echo "FETCHING AVAILABLE MODELS"
echo "================================================================================"

# ---------------------------------------------------------
# 1. Fetch and Parse vLLM Models (Fixed Syntax)
# ---------------------------------------------------------
echo ""
echo "--- vLLM Server ($VLLM_URL) ---"
curl -s $VLLM_URL | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if 'data' in data:
        # Use .format() instead of f-strings for compatibility
        print('{:<40}'.format('MODEL ID'))
        print('-' * 40)
        for model in data['data']:
            print('{:<40}'.format(model['id']))
    else:
        print('No data field found in response.')
except Exception as e:
    print('Error parsing JSON:', e)
"

# ---------------------------------------------------------
# 2. Fetch and Parse Ollama Models (Fixed Syntax)
# ---------------------------------------------------------
echo ""
echo "--- Ollama Server ($OLLAMA_URL) ---"
curl -s $OLLAMA_URL | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if 'models' in data:
        # Use .format() to avoid quoting issues
        print('{:<30} | {:<10} | {}'.format('MODEL NAME', 'SIZE', 'QUANTIZATION'))
        print('-' * 60)
        
        # Sort models by name
        models = sorted(data['models'], key=lambda x: x['name'])
        
        for m in models:
            name = m.get('name', 'N/A')
            details = m.get('details', {})
            param_size = details.get('parameter_size', 'N/A')
            quant = details.get('quantization_level', 'N/A')
            
            print('{:<30} | {:<10} | {}'.format(name, param_size, quant))
    else:
        print('No models field found in response.')
except Exception as e:
    print('Error parsing JSON:', e)
"

echo ""
echo "================================================================================"

