import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import db
from app.core.session import session_store
from app.core.websocket import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_SUBSCRIPTIONS = 20


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Authenticate via session cookie
    session_id = websocket.cookies.get("session_id", "")
    session = session_store.get(session_id)
    if not session:
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await websocket.accept()
    ws_manager.connect(websocket)
    ws_manager.start_heartbeat()
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            if msg_type == "subscribe":
                channel = data.get("channel", "")
                if not isinstance(channel, str) or len(channel) > 128:
                    continue
                current_subs = ws_manager._client_channels.get(websocket, set())
                if len(current_subs) >= MAX_SUBSCRIPTIONS:
                    continue
                if channel and await _authorize_channel(channel, session):
                    ws_manager.subscribe(websocket, channel)
            elif msg_type == "unsubscribe":
                channel = data.get("channel", "")
                if channel:
                    ws_manager.unsubscribe(websocket, channel)
            elif msg_type == "pong":
                ws_manager.record_pong(websocket)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


async def _authorize_channel(channel: str, session) -> bool:
    """Verify the session user owns the report referenced by the channel."""
    if not channel.startswith("report:"):
        return False
    job_id = channel[len("report:"):]
    job = await db.get_job(job_id)
    if not job:
        return False
    if job["mist_user_id"] != session.user_identifier:
        return False
    if job["org_id"] not in session.org_ids:
        return False
    return True
