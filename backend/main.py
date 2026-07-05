"""FastAPI + WebSocket server.

Bridges the Chrome sidepanel UI and the LangGraph agent:
  * sidepanel  --{instruction}-->  agent
  * agent      --{reasoning, action, observation}-->  sidepanel  (live log)
  * agent      --{confirmation_required}-->  sidepanel  (safety gate)
  * sidepanel  --{confirm approved/denied}-->  agent  (resume via Command)

Also serves the demo site at /demo so the whole thing runs from one process.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command

from backend import config
from backend.agent.graph import build_graph, initial_state
from backend.browser.controller import BrowserController

app = FastAPI(title="GemmaPilot Browser Agent")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo-site"
if DEMO_DIR.is_dir():
    app.mount("/demo", StaticFiles(directory=str(DEMO_DIR), html=True), name="demo")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "model": config.OLLAMA_MODEL,
        "ollama": config.OLLAMA_BASE_URL,
        "cdp": config.CDP_ENDPOINT,
    })


async def _stream(graph, websocket: WebSocket, thread_id: str, stream_input) -> bool:
    """Run/resume the graph, forwarding events. Returns True if paused at a gate."""
    cfg = {"configurable": {"thread_id": thread_id}}
    async for mode, chunk in graph.astream(stream_input, cfg, stream_mode=["custom", "updates"]):
        if mode == "custom":
            await websocket.send_json(chunk)
        elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
            interrupt_obj = chunk["__interrupt__"][0]
            payload = getattr(interrupt_obj, "value", interrupt_obj)
            await websocket.send_json({"type": "confirmation_required", **payload})
            return True  # wait for the human's confirm/deny
    await websocket.send_json({"type": "complete", "thread_id": thread_id})
    return False


@app.websocket("/ws")
async def agent_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    controller = BrowserController()
    graph = None
    thread_id: str | None = None

    try:
        try:
            await controller.connect()
            graph = build_graph(controller)
            await websocket.send_json({"type": "connected", "model": config.OLLAMA_MODEL})
        except Exception as exc:  # Chrome debug port not up yet, etc.
            await websocket.send_json({"type": "error", "message": str(exc)})

        while True:
            msg = await websocket.receive_json()
            kind = msg.get("type")

            if kind == "instruction":
                if graph is None:
                    # Try to (re)connect now — user may have just launched Chrome.
                    try:
                        await controller.connect()
                        graph = build_graph(controller)
                    except Exception as exc:
                        await websocket.send_json({"type": "error", "message": str(exc)})
                        continue
                thread_id = uuid.uuid4().hex
                text = (msg.get("text") or "").strip()
                if not text:
                    await websocket.send_json({"type": "error", "message": "Empty instruction."})
                    continue
                await websocket.send_json({"type": "accepted", "instruction": text, "thread_id": thread_id})
                try:
                    await _stream(graph, websocket, thread_id, initial_state(text))
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": f"Agent error: {exc}"})

            elif kind == "confirm":
                if graph is None or thread_id is None:
                    await websocket.send_json({"type": "error", "message": "No pending action to confirm."})
                    continue
                approved = bool(msg.get("approved"))
                await websocket.send_json({"type": "confirmation_ack", "approved": approved})
                try:
                    await _stream(graph, websocket, thread_id, Command(resume={"approved": approved}))
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": f"Agent error: {exc}"})

            elif kind == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    finally:
        await controller.close()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
