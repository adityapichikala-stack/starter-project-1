"""FastAPI app: POST /orders {"email": "<raw email text>"} -> order result."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.llm_client import LLMClient, LLMError
from app.models import OrderRequest, OrderResult
from app.steps.stock import StockRepository
from app.workflow import OrderWorkflow

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.repo = StockRepository(settings)
    app.state.workflow = OrderWorkflow(LLMClient(settings), app.state.repo, settings.lead_time_days,
                                       settings.extraction_max_iterations)
    yield


app = FastAPI(title="Order Desk - Starter Project 1", lifespan=lifespan)


@app.exception_handler(LLMError)
async def llm_error(_: Request, exc: LLMError):
    log.error("LLM error: %s", exc)
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
    try:
        return request.app.state.workflow.run(body.email)
    except LLMError:
        raise
    except Exception as e:  # e.g. database down
        log.exception("workflow failed")
        raise HTTPException(status_code=503, detail=f"{type(e).__name__}: {e}")
