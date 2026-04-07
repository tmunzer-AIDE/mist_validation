from uuid import UUID

from pydantic import BaseModel


class SiteOption(BaseModel):
    id: str
    name: str


class SitesResponse(BaseModel):
    sites: list[SiteOption]
    tdr_site_ids: list[str] = []
    tdr_group_name: str = ""
    tdr_group_exists: bool = True


class ReportCreateRequest(BaseModel):
    site_id: UUID
    org_id: UUID
    include_cable_tests: bool = False


class ReportResponse(BaseModel):
    id: str
    org_id: str
    org_name: str
    site_id: str
    site_name: str
    status: str
    progress: dict
    result: dict | None
    error: str | None
    include_cable_tests: bool
    created_at: str
    completed_at: str | None


class ReportListResponse(BaseModel):
    reports: list[ReportResponse]
    total: int
