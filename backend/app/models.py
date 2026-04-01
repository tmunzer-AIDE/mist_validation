from pydantic import BaseModel


class AuthRequest(BaseModel):
    auth_type: str  # "token" or "credentials"
    token: str | None = None
    email: str | None = None
    password: str | None = None
    cloud: str  # e.g. "global_01", "emea_01"


class OrgInfo(BaseModel):
    id: str
    name: str


class AuthResponse(BaseModel):
    user_id: str
    user_email: str
    orgs: list[OrgInfo]


class SiteOption(BaseModel):
    id: str
    name: str


class SitesResponse(BaseModel):
    sites: list[SiteOption]


class ReportCreateRequest(BaseModel):
    site_id: str
    org_id: str
    include_cable_tests: bool = False


class ReportResponse(BaseModel):
    id: str
    mist_user_id: str
    org_id: str
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
