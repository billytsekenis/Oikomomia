from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..analyst import llm
from ..analyst.engine import Analyst
from ..analyst.toolkit import Toolkit
from ..db import get_db
from ..models import User
from ..security import get_current_user
from .common import clean

router = APIRouter(prefix="/api/analyst", tags=["analyst"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict] = []
    ticker: str | None = None
    use_llm: bool = True


@router.post("/chat")
def chat(body: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    analyst = Analyst(Toolkit(db=db, user=user))
    det = analyst.answer(body.message, history=body.history, context_ticker=body.ticker)
    out = llm.narrate(body.message, det, body.history) if body.use_llm else det
    if "llm_status" not in out:
        out["llm_status"] = "not configured (set ANTHROPIC_API_KEY to enable conversational rewriting)" if not llm.available() else "disabled for this request"
    return clean(out)


@router.get("/status")
def status(_: User = Depends(get_current_user)):
    return {"llm_available": llm.available(), "engine": "deterministic evidence engine" + (" + Claude narration" if llm.available() else "")}
