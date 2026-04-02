import os

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_session
from app.core.session import SessionData
from app.models import SiteOption, SitesResponse
from app.services.mist_service import ConfigurationError, MistAPIError, MistService

router = APIRouter()

TDR_SITE_GROUP = os.environ.get("TDR_SITE_GROUP", "tdr_validation")


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

        # Resolve TDR-eligible sites
        tdr_site_ids: list[str] = []
        tdr_group_exists = True
        if TDR_SITE_GROUP:
            try:
                site_groups = await mist.get_site_groups()
                tdr_group = next(
                    (g for g in site_groups if g.get("name") == TDR_SITE_GROUP),
                    None,
                )
                if tdr_group:
                    tdr_group_id = tdr_group["id"]
                    tdr_site_ids = [
                        s["id"] for s in sites
                        if tdr_group_id in s.get("sitegroup_ids", [])
                    ]
                else:
                    tdr_group_exists = False
            except MistAPIError:
                tdr_group_exists = False
        else:
            # TDR_SITE_GROUP is empty → no group gating, all sites eligible
            tdr_site_ids = [s["id"] for s in sites]

        return SitesResponse(
            sites=options,
            tdr_site_ids=tdr_site_ids,
            tdr_group_name=TDR_SITE_GROUP,
            tdr_group_exists=tdr_group_exists,
        )
    except (MistAPIError, ConfigurationError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch sites")
