from types import SimpleNamespace

from app.llm_client import LLMClient


def test_call_reports_available_token_usage_without_openai_requests():
    client = LLMClient.__new__(LLMClient)
    client._max_attempts = 1
    usage_records = []
    client._usage_callback = usage_records.append
    response = SimpleNamespace(
        model="mock-deployment",
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7, total_tokens=19),
    )

    assert client._call("mock-step", lambda: response) is response
    assert usage_records == [
        {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    ]
