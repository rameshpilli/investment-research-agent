"""
Legacy REST Compatibility API
=============================

Optional FastAPI transport retained for compatibility and simple health checks.

The MCP Streamable HTTP server is the primary programmatic interface for this
project. These REST endpoints are not part of the default docker runtime.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import settings
from src.models import list_supported_tickers
from src.pipeline import answer_question, run_pipeline

app = FastAPI(title="Investment Research API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResearchRequest(BaseModel):
    ticker: str


class FollowUpRequest(BaseModel):
    ticker: str
    question: str
    conversation_id: str | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tickers")
async def tickers() -> dict[str, list[str]]:
    return {"tickers": list_supported_tickers()}


@app.post("/research/run")
async def run_research(request: ResearchRequest) -> dict[str, object]:
    try:
        return await run_pipeline(request.ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/research/question")
async def ask_follow_up(request: FollowUpRequest) -> dict[str, str]:
    try:
        answer = await answer_question(request.ticker, request.question, conversation_id=request.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"answer": answer}
