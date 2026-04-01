"""
Mist API service wrapper using the mistapi package.
Provides abstraction layer for all Mist API interactions.
"""

import logging
from typing import Any

import mistapi
from mistapi import APISession
from mistapi.api.v1 import orgs as orgs_api
from mistapi.api.v1.orgs import sites, templates
from mistapi.api.v1.orgs import wlans as org_wlans
from mistapi.api.v1.sites import devices
from mistapi.api.v1.sites import sites as site_sites
from mistapi.api.v1.sites import wlans as site_wlans

logger = logging.getLogger(__name__)


class MistAPIError(Exception):
    pass


class ConfigurationError(Exception):
    pass


_HOST_MAP = {
    "global_01": "api.mist.com",
    "global_02": "api.gc1.mist.com",
    "global_03": "api.ac2.mist.com",
    "global_04": "api.gc2.mist.com",
    "global_05": "api.gc4.mist.com",
    "emea_01": "api.eu.mist.com",
    "emea_02": "api.gc3.mist.com",
    "emea_03": "api.ac6.mist.com",
    "emea_04": "api.gc6.mist.com",
    "apac_01": "api.ac5.mist.com",
    "apac_02": "api.gc5.mist.com",
    "apac_03": "api.gc7.mist.com",
}


class MistService:
    """Service for interacting with Mist API using mistapi package."""

    def __init__(
        self,
        org_id: str | None = None,
        cloud_region: str = "global_01",
        api_token: str | None = None,
        email: str | None = None,
        password: str | None = None,
    ):
        self.org_id = org_id
        self.cloud_region = cloud_region
        self._api_token = api_token
        self._email = email
        self._password = password

        if not self.org_id:
            raise ConfigurationError("Mist Organization ID not configured")
        if not api_token and not (email and password):
            raise ConfigurationError("Either API token or email+password required")

        self.session = self._create_session()

    def _create_session(self) -> APISession:
        host = _HOST_MAP.get(self.cloud_region, "api.mist.com")
        try:
            if self._api_token:
                session = APISession(host=host, apitoken=self._api_token)
            else:
                session = APISession(host=host, email=self._email, password=self._password)
            logger.info("mist_api_session_created org_id=%s cloud_region=%s", self.org_id, self.cloud_region)
            return session
        except Exception as e:
            logger.error("mist_api_session_creation_failed error=%s", str(e))
            raise MistAPIError("Failed to create Mist API session") from e

    async def test_connection(self) -> tuple[bool, str | None]:
        """
        Test Mist API connection and credentials.

        Returns:
            tuple: (success, error_message)
        """
        try:
            result = await mistapi.arun(orgs_api.orgs.getOrg, self.session, self.org_id)

            if result.status_code == 200:
                logger.info("mist_api_connection_successful org_id=%s", self.org_id)
                return True, None
            else:
                error_msg = f"API returned status {result.status_code}"
                logger.warning("mist_api_connection_failed error=%s", error_msg)
                return False, error_msg

        except Exception as e:
            logger.error("mist_api_connection_error error=%s", str(e))
            return False, "Connection test failed"

    # ===== Organization Operations =====

    async def get_org_info(self) -> dict[str, Any]:
        """Get organization information."""
        try:
            result = await mistapi.arun(orgs_api.orgs.getOrg, self.session, self.org_id)

            if result.status_code != 200:
                raise MistAPIError(f"Failed to get org info: {result.status_code}")

            logger.debug("org_info_retrieved org_id=%s", self.org_id)
            return result.data

        except Exception as e:
            logger.error("get_org_info_failed error=%s", str(e))
            raise MistAPIError("Mist API request failed") from e

    # ===== Site Operations =====

    async def get_sites(self) -> list[dict[str, Any]]:
        """Get all sites in the organization."""
        try:
            result = await mistapi.arun(sites.listOrgSites, self.session, self.org_id)

            if result.status_code != 200:
                raise MistAPIError(f"Failed to get sites: {result.status_code}")

            logger.debug("sites_retrieved org_id=%s count=%d", self.org_id, len(result.data))
            return result.data

        except Exception as e:
            logger.error("get_sites_failed error=%s", str(e))
            raise MistAPIError("Mist API request failed") from e

    async def get_site(self, site_id: str) -> dict[str, Any]:
        """Get site details."""
        try:
            result = await mistapi.arun(site_sites.getSiteInfo, self.session, site_id)

            if result.status_code != 200:
                raise MistAPIError(f"Failed to get site: {result.status_code}")

            logger.debug("site_retrieved site_id=%s", site_id)
            return result.data

        except Exception as e:
            logger.error("get_site_failed site_id=%s error=%s", site_id, str(e))
            raise MistAPIError("Mist API request failed") from e

    # ===== WLAN Operations =====

    async def get_wlans(self, site_id: str | None = None) -> list[dict[str, Any]]:
        """Get WLANs (org-level or site-level)."""
        try:
            if site_id:
                result = await mistapi.arun(site_wlans.listSiteWlans, self.session, site_id)
            else:
                result = await mistapi.arun(org_wlans.listOrgWlans, self.session, self.org_id)

            if result.status_code != 200:
                raise MistAPIError(f"Failed to get WLANs: {result.status_code}")

            logger.debug("wlans_retrieved site_id=%s count=%d", site_id, len(result.data))
            return result.data

        except Exception as e:
            logger.error("get_wlans_failed site_id=%s error=%s", site_id, str(e))
            raise MistAPIError("Mist API request failed") from e

    # ===== Template Operations =====

    async def get_templates(self) -> list[dict[str, Any]]:
        """Get all config templates in the organization."""
        try:
            result = await mistapi.arun(templates.listOrgTemplates, self.session, self.org_id)

            if result.status_code != 200:
                raise MistAPIError(f"Failed to get templates: {result.status_code}")

            logger.debug("templates_retrieved org_id=%s count=%d", self.org_id, len(result.data))
            return result.data

        except Exception as e:
            logger.error("get_templates_failed error=%s", str(e))
            raise MistAPIError("Mist API request failed") from e

    # ===== Device Operations =====

    async def get_devices(self, site_id: str | None = None) -> list[dict[str, Any]]:
        """Get devices (org-level or site-level)."""
        try:
            if site_id:
                result = await mistapi.arun(devices.listSiteDevices, self.session, site_id, type="all")
            else:
                result = await mistapi.arun(orgs_api.devices.listOrgDevices, self.session, self.org_id)

            if result.status_code != 200:
                raise MistAPIError(f"Failed to get devices: {result.status_code}")

            logger.debug("devices_retrieved site_id=%s count=%d", site_id, len(result.data))
            return result.data

        except Exception as e:
            logger.error("get_devices_failed site_id=%s error=%s", site_id, str(e))
            raise MistAPIError("Mist API request failed") from e

    def get_session(self) -> APISession:
        """Return the underlying APISession for direct mistapi access."""
        return self.session


async def verify_mist_credentials(
    auth_type: str,
    token: str | None,
    email: str | None,
    password: str | None,
    cloud: str,
) -> dict:
    """Verify Mist credentials and return user info.

    Returns {user_id, user_email, orgs: [{id, name}]}.
    """
    import mistapi as _mistapi
    from mistapi import APISession
    from mistapi.api.v1.self import self as self_api

    host = _HOST_MAP.get(cloud, "api.mist.com")

    if auth_type == "token":
        if not token:
            raise ConfigurationError("API token required")
        session = APISession(host=host, apitoken=token)
    else:
        if not email or not password:
            raise ConfigurationError("Email and password required")
        session = APISession(host=host, email=email, password=password)

    # Call /api/v1/self
    resp = await _mistapi.arun(self_api.getSelf, session)
    if resp.status_code != 200:
        raise MistAPIError(f"Authentication failed: {resp.status_code}")

    user_data = resp.data
    user_id = user_data.get("id", "")
    user_email = user_data.get("email", "")

    # Extract orgs from privileges
    privileges = user_data.get("privileges", [])
    orgs_seen = {}
    for priv in privileges:
        if isinstance(priv, dict) and priv.get("scope") == "org":
            org_id = priv.get("org_id", "")
            org_name = priv.get("name", "") or priv.get("org_name", "") or org_id[:8]
            if org_id and org_id not in orgs_seen:
                orgs_seen[org_id] = org_name

    return {
        "user_id": user_id,
        "user_email": user_email,
        "orgs": [{"id": k, "name": v} for k, v in orgs_seen.items()],
        "cloud": cloud,
    }
