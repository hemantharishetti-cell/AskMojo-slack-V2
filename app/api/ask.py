"""
Thin API route for /ask endpoint.

No business logic — validates request, calls orchestrator.run_pipeline(),
and returns the response.  All heavy lifting lives in the pipeline modules.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.response import AskRequest, AskResponse
from app.sqlite.database import get_db
from app.pipeline.orchestrator import run_pipeline, pipeline_response_to_ask_response
from app.pipeline.retrieval import ChromaDBIndexUnavailableError
from app.api.query_logging import log_query
from app.utils.logging import get_logger

logger = get_logger("askmojo.api.ask")

router = APIRouter(tags=["Ask"])


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    request: AskRequest,
    db: Session = Depends(get_db),
):
    """
    Answer a user's question using the 3-stage RAG pipeline.

    This endpoint is async — it frees the FastAPI worker during
    LLM I/O waits instead of blocking for 10-25s.
    """
    q = (request.question or "").strip()[:80]
    logger.info("[ASK] New question: %s%s", q, "..." if len(request.question or "") > 80 else "")
    if request.slack_user_email:
        logger.info("[ASK] Slack user: %s", request.slack_user_email)

    try:
        final = await run_pipeline(
            question=request.question,
            db=db,
            slack_user_email=request.slack_user_email,
            conversation_history=request.conversation_history,
            max_tokens=request.max_tokens,
            model_preference=request.model_preference,
        )

        logger.info(
            "[ASK] Pipeline done in %.2fs | intent=%s",
            final.processing_time_seconds or 0,
            getattr(final.pipeline_metadata, "intent", None) or "—",
        )

        # Log query so admin panel shows it (non-fatal: do not 500 if logging fails)
        try:
            log_query(request.question, request.slack_user_email, final)
            logger.info("[ASK] Query logged to DB for admin panel")
        except Exception as log_err:
            logger.warning("[ASK] Query logging failed (response still returned): %s", log_err)

        response = pipeline_response_to_ask_response(final)
        return response

    except ChromaDBIndexUnavailableError as e:
        logger.warning("[ASK] ChromaDB index unavailable (returning friendly message): %s", e)
        return AskResponse(
            answer=(
                "The search index is temporarily unavailable (this can happen after a restart or before documents are uploaded). "
                "Please try again in a moment. If the issue persists, ask your administrator to re-upload documents to rebuild the index."
            ),
            sources=None,
            followups=None,
            token_usage=None,
            toon_savings=None,
        )

    except Exception as e:
        logger.error("[ASK] Error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing question: {e}",
        )
