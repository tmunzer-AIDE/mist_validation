from fastapi import APIRouter, Header, HTTPException

from app.models import SiteOption, SitesResponse
from app.services.mist_service import ConfigurationError, MistAPIError, MistService

router = APIRouter()


@router.get("/sites", response_model=SitesResponse)
async def list_sites(
    x_mist_cloud: str = Header(...),
    x_mist_org_id: str = Header(...),
    x_mist_token: str | None = Header(default=None),
    x_mist_email: str | None = Header(default=None),
    x_mist_password: str | None = Header(default=None),
):
    try:
        mist = MistService(
            org_id=x_mist_org_id,
            cloud_region=x_mist_cloud,
            api_token=x_mist_token,
            email=x_mist_email,
            password=x_mist_password,
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
