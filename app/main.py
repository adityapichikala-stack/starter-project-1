"""FastAPI app: POST /orders {"email": "<raw email text>"} -> order result."""
import logging
from decimal import Decimal
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.llm_client import LLMClient, LLMError
from app.models import OrderRequest, OrderResult
from app.observability import OrderMetrics
from app.rag import DocumentRepository
from app.steps.stock import StockRepository
from app.workflow import OrderWorkflow

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("api")


def escape_raw_json_string_controls(body: bytes) -> bytes:
    """Escape literal control characters inside JSON strings, such as pasted email newlines."""
    out = bytearray()
    in_string = False
    escaped = False
    changed = False
    for char in body:
        if in_string and char < 0x20:
            replacements = {
                0x08: b"\\b",
                0x09: b"\\t",
                0x0A: b"\\n",
                0x0C: b"\\f",
                0x0D: b"\\r",
            }
            out.extend(replacements.get(char, f"\\u{char:04x}".encode("ascii")))
            changed = True
            continue
        out.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == ord("\\"):
                escaped = True
            elif char == ord('"'):
                in_string = False
        elif char == ord('"'):
            in_string = True
    return bytes(out) if changed else body


class SwaggerEmailJSONMiddleware:
    """Allow raw multiline email text pasted into Swagger's JSON string field."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/orders"
            or not any(
                name.lower() == b"content-type" and b"application/json" in value.lower()
                for name, value in scope.get("headers", [])
            )
        ):
            await self.app(scope, receive, send)
            return

        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        body = escape_raw_json_string_controls(b"".join(chunks))
        if body != b"".join(chunks):
            scope["headers"] = [
                (name, value)
                for name, value in scope.get("headers", [])
                if name.lower() != b"content-length"
            ] + [(b"content-length", str(len(body)).encode("ascii"))]

        delivered = False

        async def replay_body():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_body, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.repo = StockRepository(settings)
    app.state.workflow = OrderWorkflow(LLMClient(settings), app.state.repo, settings.lead_time_days,
                                       settings.extraction_max_iterations,
                                       settings.llm_input_cost_per_million_tokens,
                                       settings.llm_output_cost_per_million_tokens,
                                       DocumentRepository(settings, LLMClient(settings)),
                                       Decimal(str(settings.free_delivery_threshold)),
                                       Decimal(str(settings.delivery_charge)),
                                       settings.quote_validity_days,
                                       settings.discount_tiers)
    yield


app = FastAPI(title="Order Desk - Starter Project 1", lifespan=lifespan)
app.add_middleware(SwaggerEmailJSONMiddleware)


@app.exception_handler(LLMError)
async def llm_error(request: Request, exc: LLMError):
    metrics = getattr(request.state, "order_metrics", None)
    log.error(
        "event=order_failed request_id=%s error_category=%s error_type=%s",
        metrics.request_id if metrics else "-",
        exc.error_category,
        type(exc).__name__,
    )
    return JSONResponse(status_code=502, content={"error": "llm_unavailable", "detail": str(exc)})


@app.get("/health")
def health(request: Request):
    try:
        request.app.state.repo.ping()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"database unavailable: {e}")
    return {"status": "ok"}


@app.post("/orders", response_model=OrderResult)
def create_order(body: OrderRequest, request: Request) -> OrderResult:
    metrics = OrderMetrics(
        settings.llm_input_cost_per_million_tokens,
        settings.llm_output_cost_per_million_tokens,
    )
    request.state.order_metrics = metrics
    log.info("event=order_received request_id=%s", metrics.request_id)
    try:
        return request.app.state.workflow.run(body.email, metrics)
    except LLMError:
        raise
    except Exception as e:  # e.g. database down
        log.error(
            "event=order_failed request_id=%s error_category=%s error_type=%s",
            metrics.request_id,
            type(e).__name__.lower(),
            type(e).__name__,
        )
        raise HTTPException(status_code=503, detail=f"{type(e).__name__}: {e}")
