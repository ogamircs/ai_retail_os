"""Operator chat — single SSE endpoint that drives the Chief of Staff
orchestrator. Specialist events are interleaved into the stream in
roughly the order they happened."""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.agents.chief_of_staff import run_chief
from app.llm import get_provider
from app.schemas import ChatRequest

router = APIRouter()


@router.post("/api/chat")
async def chat(req: ChatRequest):
    """SSE stream of agent events."""
    try:
        llm = get_provider()
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e

    async def event_gen():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()
        # Set when the client goes away (sse-starlette cancels this generator
        # on disconnect). Without it the producer thread would run the whole
        # multi-specialist turn to completion — tokens spent for nobody.
        cancel = threading.Event()

        def producer():
            try:
                for ev in run_chief(req.message, llm, cancel=cancel):
                    payload = {
                        "kind": ev.kind,
                        "agent": ev.agent,
                        "data": ev.data,
                    }
                    asyncio.run_coroutine_threadsafe(queue.put(payload), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(
                    queue.put({"kind": "error", "agent": "system", "data": {"error": str(e)}}),
                    loop,
                )
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(SENTINEL), loop)

        task = loop.run_in_executor(None, producer)
        try:
            while True:
                item = await queue.get()
                if item is SENTINEL:
                    break
                yield {"event": "agent", "data": json.dumps(item)}
        finally:
            # Stops the turn before its next LLM call; the in-flight call
            # (if any) still completes, so this await is bounded by one call.
            cancel.set()
            await task

    return EventSourceResponse(event_gen())
