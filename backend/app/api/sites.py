from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_session
from app.core.session import SessionData
from app.models import SiteOption, SitesResponse
from app.services.mist_service import ConfigurationError, MistAPIError, MistService

router = APIRouter()


@router.get("/sites", response_model=SitesResponse)
async def list_sites(
    org_id: str,
    session: SessionData = Depends(get_session),
):
    # Verify org access
    if org_id not in session.org_ids:
        raise HTTPException(status_code=403, detail="Access denied to this organization")

    try:
        mist = MistService(
            org_id=org_id,
            cloud_region=session.mist_cloud,
            api_token=session.mist_token,
            cookies=session.mist_cookies,
            csrftoken=session.mist_csrftoken,
        )
        sites = await mist.get_sites()
        options = sorted(
            [SiteOption(id=s["id"], name=s.get("name", s["id"])) for s in sites],
            key=lambda x: x.name.lower(),
        )
        return SitesResponse(sites=options)
    except (MistAPIError, ConfigurationError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sites")
