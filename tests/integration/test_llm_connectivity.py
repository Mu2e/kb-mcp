import pytest
import os
from kb_mcp.llm.llm import get_openai_client
from kb_mcp.config import get_llm_config, get_parser_config, get_default_llm_model

def test_llm_connectivity():
    """
    Test connectivity to all 5 configured LLM model settings:
    1. Default Model
    2. Summary Model
    3. Eval Gen Model
    4. Eval Judge Model
    5. Image Description Model
    """
    llm_cfg = get_llm_config()
    parser_cfg = get_parser_config()
    
    # Collect all 5 settings as requested
    model_settings = {
        "DEFAULT_LLM_MODEL": get_default_llm_model(),
        "SUMMARY_MODEL": llm_cfg['summary_model'],
        "EVAL_GEN_MODEL": llm_cfg['eval_gen_model'],
        "EVAL_JUDGE_MODEL": llm_cfg['eval_judge_model'],
        "PARSE_IMAGE_DESCRIPTION_MODEL": parser_cfg['image_description_model'],
    }
    
    print("\nLLM Configuration Settings:")
    for setting, model in model_settings.items():
        print(f"  {setting}: {model}")
    
    # Use a set for actual testing to avoid redundant API calls if models are same
    unique_models = set(model_settings.values())
    print(f"\nUnique models to test: {unique_models}")
    
    #client = get_openai_client()
    
    results = {}
    errors = []
    
    for model in unique_models:
        print(f"Testing connectivity for model: {model}...")
        try:
            # Simple lightweight completion with timeout
            client = get_openai_client(model)
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Respond with 'True' if you receive this."}],
                max_tokens=500,
                temperature=0.0,
                timeout=30.0 # Don't hang forever
            )
            
            if not response.choices:
                print(f"  [FAIL] Model '{model}' returned NO choices.")
                errors.append(f"Model '{model}' returned no choices. Full response: {response}")
                continue
                
            msg = response.choices[0].message
            content = msg.content
            
            if content is None:
                # Some providers put reasons in refusal or elsewhere
                refusal = getattr(msg, 'refusal', None)
                print(f"  [FAIL] Model '{model}' returned NULL content. Refusal: {refusal}")
                errors.append(f"Model '{model}' returned NULL content. Refusal: {refusal}")
                continue
                
            content = content.strip()
            print(f"  [OK] Model '{model}' responded: {content}")
            results[model] = True
        except Exception as e:
            print(f"  [FAIL] Model '{model}' failed: {e}")
            results[model] = False
            errors.append(f"Model '{model}' connection failed: {e}")
    
    # Assert that all unique models are accessible
    if errors:
        pytest.fail("\n".join(errors))
    
    print("\nAll configured LLMs are available and connected.")

if __name__ == "__main__":
    # Set up environment if running directly (optional if already in shell)
    test_llm_connectivity()
