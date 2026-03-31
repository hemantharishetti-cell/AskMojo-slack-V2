"""
Simplified API route for /ask endpoint.

Validates request and calls deep_agent.run_agent().
"""

from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.response import AskRequest, AskResponse
from app.sqlite.database import get_db
from app.api.query_logging import log_query
from app.utils.logging import get_logger
from app.utils.request_locks import (
    USER_REQUEST_BUSY_MESSAGE,
    generate_processing_request_id,
    release_user_request,
    try_acquire_user_request,
)

import anyio

# Deep agent system (using dev_v2 agent)
from dev_v2 import run_agent

logger = get_logger("askmojo.api.ask")
router = APIRouter(tags=["Ask"])

_AGENT_RUN_TIMEOUT_SECONDS = 180.0


def _extract_embedding_mismatch_warning(agent_data: dict) -> dict | None:
    steps = agent_data.get("steps", [])
    if not isinstance(steps, list):
        return None

    for step in steps:
        if not isinstance(step, dict):
            continue
        output = step.get("output")
        if isinstance(output, dict) and output.get("error_type") == "embedding_dim_mismatch":
            return {
                "type": "embedding_dim_mismatch",
                "severity": "critical",
                "collection_name": output.get("collection_name"),
                "message": output.get("message"),
                "original_error": output.get("original_error"),
            }
    return None

@router.post("/ask", response_model=AskResponse)
async def ask_question(
    request: AskRequest,
    db: Session = Depends(get_db),
):
    """
    Answer a user's question using the Deep Agent system.
    """

    q_preview = (request.question or "").strip()[:80]
    logger.info("[ASK] New question: %s...", q_preview)

    if request.slack_user_email:
        logger.info("[ASK] Slack user: %s", request.slack_user_email)
    try:
        processing_request_id = generate_processing_request_id()
        user_lock_acquired = try_acquire_user_request(
            db,
            processing_request_id=processing_request_id,
            request_user_key=request.request_user_key,
            slack_user_email=request.slack_user_email,
        )
        if not user_lock_acquired:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=USER_REQUEST_BUSY_MESSAGE,
            )

        try:
            logger.info("[ASK] Running deep agent system (dev_v2.run_agent)")
            try:
                with anyio.fail_after(_AGENT_RUN_TIMEOUT_SECONDS):
                    agent_result_raw = await anyio.to_thread.run_sync(run_agent, request.question)
            except TimeoutError:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Agent processing timed out. Please try again.",
                )

            try:
                agent_data = json.loads(agent_result_raw) if isinstance(agent_result_raw, str) else agent_result_raw
            except Exception as e:
                logger.error("[ASK] Failed to parse agent result: %s", e)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid response from agent engine",
                )

            answer = agent_data.get("answer", "I'm sorry, I couldn't find an answer to that question.")

            # source_names = []
            # retrieved_chunks = agent_data.get("retrieved_chunks", {})
            # if isinstance(retrieved_chunks, dict):
            #     for col_docs in retrieved_chunks.values():
            #         if isinstance(col_docs, list):
            #             for doc in col_docs:
            #                 source = doc.get("file_name") or doc.get("title")
            #                 if source and source not in source_names:
            #                     source_names.append(source)
            #
            # if not source_names and answer:
            #     import re
            #     source_match = re.search(r"Source:\s*(.*)", answer, re.IGNORECASE | re.DOTALL)
            #     if source_match:
            #         source_text = source_match.group(1).strip()
            #         potential_sources = re.split(r'[,.\n]', source_text)
            #         for s in potential_sources:
            #             s = s.strip()
            #             if s and s not in source_names and s.lower() != "<document title>":
            #                 source_names.append(s)

            response = AskResponse(
                answer=answer,
                sources=None,
                followups=None,
                token_usage=None,
                toon_savings=None
            )

            try:
                from app.schemas.response import FinalResponse

                agent_steps = agent_data.get("steps", [])
                api_calls = []
                for idx, step_data in enumerate(agent_steps):
                    api_calls.append({
                        "call_name": step_data.get("tool", f"Step {idx+1}"),
                        "request_prompt": json.dumps(step_data.get("input", {}), indent=2),
                        "response_content": json.dumps(step_data.get("output", {}), indent=2),
                        "model_used": step_data.get("model_used", "N/A"),
                        "tokens_used": step_data.get("tokens_used", 0),
                        "time_taken_seconds": step_data.get("time_taken_seconds", 0.0)
                    })

                token_usage_dict = {
                    "total_tokens_used": agent_data.get("total_tokens_used", 0),
                    "api_calls": api_calls
                }

                openai_non_embedding_usage = agent_data.get("openai_non_embedding_token_usage") if isinstance(agent_data, dict) else None
                if isinstance(openai_non_embedding_usage, dict):
                    token_usage_dict["openai_non_embedding_token_usage"] = openai_non_embedding_usage

                tool_timing_summary = agent_data.get("tool_timing_summary") if isinstance(agent_data, dict) else None
                if isinstance(tool_timing_summary, list):
                    token_usage_dict["tool_timing_summary"] = tool_timing_summary

                toon_usage = agent_data.get("toon_token_usage") if isinstance(agent_data, dict) else None
                if isinstance(toon_usage, dict):
                    if "total_json_tokens" in toon_usage and "total_tokens_without_toon" not in token_usage_dict:
                        token_usage_dict["total_tokens_without_toon"] = toon_usage.get("total_json_tokens")
                    if "total_savings" in toon_usage and "total_savings" not in token_usage_dict:
                        token_usage_dict["total_savings"] = toon_usage.get("total_savings")
                    if "total_savings_percent" in toon_usage and "total_savings_percent" not in token_usage_dict:
                        token_usage_dict["total_savings_percent"] = toon_usage.get("total_savings_percent")
                    if "breakdown_by_call" in toon_usage and "breakdown_by_call" not in token_usage_dict:
                        token_usage_dict["breakdown_by_call"] = toon_usage.get("breakdown_by_call")
                mismatch_warning = _extract_embedding_mismatch_warning(agent_data)
                if mismatch_warning:
                    token_usage_dict["system_warnings"] = [mismatch_warning]

                mock_final = FinalResponse(
                    answer=answer,
                    processing_time_seconds=agent_data.get("total_time_seconds", 0.0),
                    token_usage=token_usage_dict,
                    toon_savings=agent_data.get("toon_savings") if isinstance(agent_data, dict) else None,
                )
                log_query(request.question, request.slack_user_email, mock_final)
            except Exception as log_err:
                logger.warning("[ASK] Query logging failed: %s", log_err)

            return response
        finally:
            if user_lock_acquired:
                try:
                    release_user_request(
                        db,
                        processing_request_id=processing_request_id,
                        request_user_key=request.request_user_key,
                        slack_user_email=request.slack_user_email,
                    )
                except Exception as release_err:
                    logger.warning("[ASK] Failed to release user processing lock: %s", release_err)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[ASK] Error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing question: {e}",
        )
