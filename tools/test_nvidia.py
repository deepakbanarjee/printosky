import os
import requests
from dotenv import load_dotenv

# Load variables from .env
load_dotenv(override=True)

def test_nvidia_api():
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("[ERROR] NVIDIA_API_KEY is not set in the environment or .env file.")
        return

    print(f"[KEY] Using API key: {api_key[:10]}...{api_key[-10:]}")
    
    url_models = "https://integrate.api.nvidia.com/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    
    print("[INFO] Listing available models...")
    try:
        response = requests.get(url_models, headers=headers)
        response.raise_for_status()
        data = response.json()
        models = [m["id"] for m in data.get("data", [])]
        
        llama_models = [m for m in models if "llama" in m.lower()]
        print(f"Found {len(llama_models)} Llama models. First few:")
        for lm in llama_models[:5]:
            print(f" - {lm}")
        
        if not llama_models:
            print("[ERROR] No Llama models found in your available models list.")
            return
            
        test_model = llama_models[0]
        print(f"[INFO] Testing chat completions with model: {test_model}")
        
        url_chat = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers_chat = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": test_model,
            "messages": [
                {"role": "user", "content": "Say 'NVIDIA API Connection Successful!'"}
            ],
            "temperature": 0.1,
            "max_tokens": 50
        }
        
        response_chat = requests.post(url_chat, headers=headers_chat, json=payload)
        response_chat.raise_for_status()
        
        chat_data = response_chat.json()
        content = chat_data["choices"][0]["message"]["content"]
        print("[SUCCESS] Chat completions output:")
        print(f"\n{content}\n")
        
    except Exception as e:
        print(f"[ERROR] Run failed: {e}")
        if 'response_chat' in locals() and response_chat is not None:
            print(f"Status Code: {response_chat.status_code}")
            print(f"Response: {response_chat.text}")
        elif 'response' in locals() and response is not None:
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text}")

if __name__ == "__main__":
    test_nvidia_api()
