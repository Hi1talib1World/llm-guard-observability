"""
Example 01: Drop-In FastAPI Middleware Integration.

Demonstrates how to attach LLMGuardMiddleware to any existing FastAPI application.
All incoming /v1/chat/completions requests are automatically inspected:
- Prompt injections and leaks are blocked with HTTP 400.
- PII (emails, phone numbers, SSNs, secrets) is redacted before hitting the model.
- Time-to-First-Token (TTFT), token counts, and latencies are captured.
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from src.llm_guard_obs import LLMGuardMiddleware

app = FastAPI(
    title="Protected AI Service",
    description="FastAPI service secured with LLM Guard & Observability middleware",
    version="1.0.0"
)

# 1. Attach the drop-in middleware
app.add_middleware(LLMGuardMiddleware)

# 2. Define standard LLM chat completion endpoint
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    data = await request.json()
    messages = data.get("messages", [])
    user_prompt = messages[-1].get("content", "") if messages else ""

    # Simulated LLM generation response
    return JSONResponse({
        "id": "chatcmpl-demo-101",
        "model": data.get("model", "gpt-4o"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"Secure response generated for sanitized prompt: '{user_prompt}'"
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": len(user_prompt.split()) * 2,
            "completion_tokens": 25,
            "total_tokens": len(user_prompt.split()) * 2 + 25
        }
    })

@app.get("/health")
async def health():
    return {"status": "healthy", "guardrail": "active"}

if __name__ == "__main__":
    print("Starting Protected AI Gateway on http://localhost:8000...")
    print("Try sending a POST request to http://localhost:8000/v1/chat/completions")
    uvicorn.run(app, host="127.0.0.1", port=8000)
