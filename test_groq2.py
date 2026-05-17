import os
from llm_code_agent import make_backend

api_key = os.environ.get("GROQ_API_KEY")

try:
    backend = make_backend("groq", "", api_key)
    res = backend.chat("llama-3.3-70b-versatile", [{"role": "user", "content": "hi"}], True)
    print("SUCCESS", res)
except Exception as e:
    import traceback
    traceback.print_exc()
