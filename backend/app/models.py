from typing import Literal
from uuid import UUID

from pydantic import BaseModel, model_validator


class SiteOption(BaseModel):
    id: str
    name: str


class SitesResponse(BaseModel):
    sites: list[SiteOption]
    tdr_site_ids: list[str] = []
    tdr_group_name: str = ""
    tdr_group_exists: bool = True


class ReportCreateRequest(BaseModel):
    site_id: UUID | None = None
    org_id: UUID
    scope: Literal["site", "org"] = "site"
    include_cable_tests: bool = False
    include_config_errors: bool = False

    @model_validator(mode="after")
    def _validate_site_id(self):
        if self.scope == "site" and self.site_id is None:
            raise ValueError("site_id is required for site-level reports")
        return self


class BudgetResponse(BaseModel):
    allowed: bool
    reason: str
    available: int
    estimated: int
    config_errors_allowed: bool
    config_errors_reason: str
    site_count: int
    device_counts: dict


class ReportResponse(BaseModel):
    id: str
    org_id: str
    org_name: str
    site_id: str
    site_name: str
    scope: str
    status: str
    progress: dict
    result: dict | None
    error: str | None
    include_cable_tests: bool
    include_config_errors: bool
    created_at: str
    completed_at: str | None


class ReportListResponse(BaseModel):
    reports: list[ReportResponse]
    total: int
