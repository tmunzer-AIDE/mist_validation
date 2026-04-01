from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.websocket import ws_manager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_manager.connect(websocket)
    ws_manager.start_heartbeat()
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            if msg_type == "subscribe":
                channel = data.get("channel", "")
                if channel:
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
