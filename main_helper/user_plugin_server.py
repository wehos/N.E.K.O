from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

app = FastAPI(title="N.E.K.O User Plugin Server")

logger = logging.getLogger("user_plugin_server")
logging.basicConfig(level=logging.INFO)

# In-memory plugin registry (initially empty). Plugins are dicts with keys:
# { "id": str, "name": str, "description": str, "endpoint": str, "input_schema": dict }
# Registration endpoints are intentionally not implemented now.
_plugins: Dict[str, Dict[str, Any]] = {}

# Simple bounded in-memory event queue for inspection
EVENT_QUEUE_MAX = 1000
_event_queue: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)

def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

@app.get("/health")
async def health():
    return {"status": "ok", "time": _now_iso()}

@app.get("/available")
async def available():
    """Return availability and basic stats."""
    return {
        "status": "ok",
        "available": True,
        "plugins_count": len(_plugins),
        "time": _now_iso()
    }

@app.get("/plugins")
async def list_plugins():
    """
    Return the list of known plugins.
    Each plugin item contains at least: id, name, description, input_schema, endpoint (if any).
    """
    try:
        return {"plugins": list(_plugins.values()), "count": len(_plugins)}
    except Exception as e:
        logger.exception("Failed to list plugins")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/plugin/events/messages")
async def receive_messages(payload: Dict[str, Any], request: Request):
    """
    Receive conversation messages forwarded from agent/cross_server.
    Payload expected:
      {"messages": [...], "lanlan_name": "...", "received_at": "..."}
    """
    try:
        event = {
            "type": "messages",
            "payload": payload,
            "received_at": _now_iso(),
            "client": request.client.host if request.client else None
        }
        try:
            _event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping oldest then enqueue")
            try:
                _ = _event_queue.get_nowait()
            except Exception:
                pass
            try:
                _event_queue.put_nowait(event)
            except Exception:
                logger.error("Failed to enqueue event after dropping")
        logger.info(f"Received messages event: lanlan_name={payload.get('lanlan_name')}, messages_count={len(payload.get('messages', []))}")
        return {"success": True}
    except Exception as e:
        logger.exception("Failed to receive messages")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/plugin/events/tool_call")
async def receive_tool_call(payload: Dict[str, Any], request: Request):
    """
    Receive tool/plugin call events.
    Payload expected:
      {
        "task_id": "...",
        "source": "...",
        "tool": "...",
        "args": {...},
        "status": "started|completed|failed",
        "result": {...} (optional),
        "timestamp": "..."
      }
    """
    try:
        event = {
            "type": "tool_call",
            "payload": payload,
            "received_at": _now_iso(),
            "client": request.client.host if request.client else None
        }
        try:
            _event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping oldest then enqueue")
            try:
                _ = _event_queue.get_nowait()
            except Exception:
                pass
            try:
                _event_queue.put_nowait(event)
            except Exception:
                logger.error("Failed to enqueue event after dropping")
        logger.info(f"Received tool_call event: tool={payload.get('tool')}, status={payload.get('status')}, task_id={payload.get('task_id')}")
        return {"success": True}
    except Exception as e:
        logger.exception("Failed to receive tool_call")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/events/recent")
async def recent_events(limit: int = 20):
    """
    Return the most recent events from the in-memory queue for inspection.
    Non-destructive: drains into a temp list then requeues items to preserve state.
    """
    items = []
    try:
        # Drain up to queue size
        while not _event_queue.empty() and len(items) < min(limit, EVENT_QUEUE_MAX):
            try:
                it = _event_queue.get_nowait()
            except Exception:
                break
            items.append(it)
        # Requeue them to preserve queue contents
        for it in items:
            try:
                _event_queue.put_nowait(it)
            except Exception:
                # if can't requeue, ignore to avoid blocking
                pass
        return {"events": items, "count": len(items)}
    except Exception as e:
        logger.exception("Failed to read recent events")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/admin/clear")
async def admin_clear():
    """Clear the internal event queue."""
    cleared = 0
    try:
        while not _event_queue.empty():
            try:
                _ = _event_queue.get_nowait()
                cleared += 1
            except Exception:
                break
        logger.info(f"Cleared {cleared} events")
        return {"success": True, "cleared": cleared}
    except Exception as e:
        logger.exception("Failed to clear events")
        raise HTTPException(status_code=500, detail=str(e))

# Utility to allow other parts of the application (same process) to query plugin list
def get_plugins() -> List[Dict[str, Any]]:
    """Return list of plugin dicts (in-process access)."""
    return list(_plugins.values())

# Utility to register a plugin programmatically (internal use only)
def _register_plugin(plugin: Dict[str, Any]) -> None:
    """Internal helper to insert plugin into registry (not exposed as HTTP)."""
    pid = plugin.get("id")
    if not pid:
        raise ValueError("plugin must have id")
    _plugins[pid] = plugin

# NOTE: Registration endpoints are intentionally not exposed per request.
# The server exposes plugin listing and event ingestion endpoints and a small in-process helper
# so task_executor can either call GET /plugins remotely or import main_helper.user_plugin_server.get_plugins
# if running in the same process.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18090)
