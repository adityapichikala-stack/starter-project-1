import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.main import SwaggerEmailJSONMiddleware, escape_raw_json_string_controls


def test_raw_multiline_email_can_be_parsed_as_orders_json():
    raw = (
        b'{\n  "email": "From: Imran Qureshi <imran@metromobile.example>\n'
        b'Date: Tue, 17 Nov 2026 18:45:00 +0530\n'
        b'Subject: Stock request for Hazratganj store\n\n'
        b'Hi,\n\nPlease send 5 Corelink Book 15 Pro and 20 Volt 20000 Power Bank.\n'
        b'Imran Qureshi"\n}'
    )

    decoded = json.loads(escape_raw_json_string_controls(raw))

    assert decoded["email"] == (
        "From: Imran Qureshi <imran@metromobile.example>\n"
        "Date: Tue, 17 Nov 2026 18:45:00 +0530\n"
        "Subject: Stock request for Hazratganj store\n\n"
        "Hi,\n\nPlease send 5 Corelink Book 15 Pro and 20 Volt 20000 Power Bank.\n"
        "Imran Qureshi"
    )


def test_existing_json_escapes_and_formatting_remain_unchanged():
    valid = b'{\n  "email": "first line\\nsecond line"\n}'

    assert escape_raw_json_string_controls(valid) is valid
    assert json.loads(escape_raw_json_string_controls(valid)) == {
        "email": "first line\nsecond line"
    }


def test_control_characters_outside_json_strings_are_not_rewritten():
    body = b'{\n"email": "ok"\n}'

    assert escape_raw_json_string_controls(body) == body


def test_http_request_with_raw_multiline_email_passes_json_parsing():
    app = FastAPI()
    app.add_middleware(SwaggerEmailJSONMiddleware)

    @app.post("/orders")
    async def accept_email(request: Request):
        return await request.json()

    raw = (
        b'{\n  "email": "From: Imran Qureshi <imran@metromobile.example>\n'
        b'Date: Tue, 17 Nov 2026 18:45:00 +0530\n'
        b'Subject: Stock request\n\nPlease send 5 Corelink Book 15 Pro."\n}'
    )
    response = TestClient(app).post(
        "/orders",
        content=raw,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["email"].endswith("Please send 5 Corelink Book 15 Pro.")
