"""Quick check that the Azure OpenAI settings in .env work.

Run: .venv\\Scripts\\python check_azure.py
"""
import os
import sys
import time

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

REQUIRED = [
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT",
]

missing = [k for k in REQUIRED if not os.getenv(k) or "your-" in os.getenv(k, "")]
if missing:
    sys.exit(f"Fill these in .env first: {', '.join(missing)}")

client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_version=os.environ["AZURE_OPENAI_API_VERSION"],
    timeout=30,
)

start = time.perf_counter()
try:
    # GPT-5 family: use max_completion_tokens, leave temperature unset.
    resp = client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        messages=[{"role": "user", "content": "Reply with exactly: Azure OpenAI is working"}],
        max_completion_tokens=500,
    )
except Exception as e:
    sys.exit(f"FAILED: {type(e).__name__}: {e}")

latency_ms = (time.perf_counter() - start) * 1000
u = resp.usage
print("OK")
print(f"Model reply : {resp.choices[0].message.content!r}")
print(f"Model       : {resp.model}")
print(f"Tokens      : prompt={u.prompt_tokens} completion={u.completion_tokens} total={u.total_tokens}")
print(f"Latency     : {latency_ms:.0f} ms")
