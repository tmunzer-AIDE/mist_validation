from fastapi import APIRouter, HTTPException

from app.models import AuthRequest, AuthResponse, OrgInfo
from app.services.mist_service import ConfigurationError, MistAPIError, verify_mist_credentials

router = APIRouter()


@router.post("/auth/verify", response_model=AuthResponse)
async def verify_auth(request: AuthRequest):
    try:
        result = await verify_mist_credentials(
            auth_type=request.auth_type,
            token=request.token,
            email=request.email,
            password=request.password,
            cloud=request.cloud,
        )
        return AuthResponse(
            user_id=result["user_id"],
            user_email=result["user_email"],
            orgs=[OrgInfo(id=o["id"], name=o["name"]) for o in result["orgs"]],
        )
    except (MistAPIError, ConfigurationError) as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Authentication failed")
