"""Tests for gateway port_config merging and AE (LACP) interface resolution."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.validation_service import _merge_port_configs, _validate_single_gateway


# ---------------------------------------------------------------------------
# _merge_port_configs
# ---------------------------------------------------------------------------

class TestMergePortConfigs:
    """Per-port deep merge preserves fields from all layers."""

    def test_device_override_preserves_template_aggregated(self):
        template = {
            "port_config": {
                "ge-0/0/2-3": {
                    "usage": "lan",
                    "aggregated": True,
                    "ae_idx": "2",
                    "networks": ["data_prod", "data_iot"],
                    "port_network": "mgmt",
                }
            }
        }
        device = {
            "port_config": {
                "ge-0/0/2-3": {
                    "usage": "lan",
                    "networks": ["data_prod", "data_iot"],
                    "port_network": "mgmt",
                }
            }
        }
        result = _merge_port_configs(template, {}, device)
        port = result["ge-0/0/2-3"]
        assert port["aggregated"] is True
        assert port["ae_idx"] == "2"

    def test_shallow_merge_regression(self):
        """Shallow merge loses fields — deep merge preserves them."""
        template_pc = {"ge-0/0/2-3": {"aggregated": True, "ae_idx": "2", "usage": "lan"}}
        device_pc = {"ge-0/0/2-3": {"usage": "lan"}}

        shallow = {**template_pc, **device_pc}
        assert "aggregated" not in shallow["ge-0/0/2-3"]

        deep = _merge_port_configs({"port_config": template_pc}, {}, {"port_config": device_pc})
        assert deep["ge-0/0/2-3"]["aggregated"] is True

    def test_device_adds_new_port(self):
        template = {"port_config": {"ge-0/0/0": {"usage": "wan"}}}
        device = {"port_config": {"ge-0/0/1": {"usage": "wan", "name": "wan2"}}}
        result = _merge_port_configs(template, {}, device)
        assert "ge-0/0/0" in result
        assert "ge-0/0/1" in result

    def test_device_overrides_value(self):
        template = {"port_config": {"ge-0/0/0": {"usage": "wan", "name": "old"}}}
        device = {"port_config": {"ge-0/0/0": {"name": "new"}}}
        result = _merge_port_configs(template, {}, device)
        assert result["ge-0/0/0"]["name"] == "new"
        assert result["ge-0/0/0"]["usage"] == "wan"

    def test_three_layer_merge(self):
        template = {"port_config": {"ge-0/0/2-3": {"aggregated": True, "ae_idx": "2"}}}
        profile = {"port_config": {"ge-0/0/2-3": {"usage": "lan", "networks": ["net1"]}}}
        device = {"port_config": {"ge-0/0/2-3": {"port_network": "mgmt"}}}
        result = _merge_port_configs(template, profile, device)
        assert result["ge-0/0/2-3"]["aggregated"] is True
        assert result["ge-0/0/2-3"]["usage"] == "lan"
        assert result["ge-0/0/2-3"]["port_network"] == "mgmt"

    def test_empty_configs(self):
        assert _merge_port_configs({}, {}, {}) == {}


# ---------------------------------------------------------------------------
# _validate_single_gateway — AE detection, unconfigured filtering, members
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_gw_stat(*, if_stat=None, **extra):
    base = {
        "id": "gw-001",
        "name": "test-gw",
        "mac": "aabbccddeeff",
        "status": "connected",
        "model": "SRX320",
        "if_stat": if_stat or {},
    }
    base.update(extra)
    return base


class TestAeDetectionFromPortStats:
    """ae interfaces are discovered from port stats, not port_config."""

    def test_ae_discovered_from_port_stats(self):
        """When derived template loses range key, ae is detected from port stats."""
        gw_stat = _make_gw_stat(if_stat={
            "ae2.0": {"port_id": "ae2", "up": True, "network_name": "mgmt", "vlan": 0},
            "ae2.170": {"port_id": "ae2", "up": True, "network_name": "data_iot", "vlan": 170},
        })
        # Derived template only has wan — range key ge-0/0/2-3 was dropped
        template = {"port_config": {"ge-0/0/0": {"usage": "wan", "name": "wan_0_0"}}}
        # Port stats include ae2 and its members
        port_stats = {
            "ge-0/0/0": {"port_id": "ge-0/0/0", "up": True, "unconfigured": False},
            "ae2": {"port_id": "ae2", "up": True, "port_usage": "lan", "unconfigured": False,
                    "lacp_stats": [{"name": "ge-0/0/2"}, {"name": "ge-0/0/3"}]},
            "ge-0/0/2": {"port_id": "ge-0/0/2", "up": True, "port_parent": "ae2",
                         "unconfigured": False, "neighbor_system_name": "sw1"},
            "ge-0/0/3": {"port_id": "ge-0/0/3", "up": True, "port_parent": "ae2",
                         "unconfigured": False, "neighbor_system_name": "sw2"},
        }

        result = _run(_validate_single_gateway(
            gw_stat, template, {}, {}, port_stats, {}, {},
        ))

        wan_ifaces = [p["interface"] for p in result["wan_ports"]]
        lan_ifaces = [p["interface"] for p in result["lan_ports"]]

        assert "ge-0/0/0" in wan_ifaces
        assert "ae2" in lan_ifaces
        # Member ports should NOT appear as separate entries
        assert "ge-0/0/2" not in lan_ifaces
        assert "ge-0/0/3" not in lan_ifaces

    def test_ae_port_is_up(self):
        """ae port status comes from port stats."""
        gw_stat = _make_gw_stat(if_stat={
            "ae2.0": {"port_id": "ae2", "up": True, "network_name": "mgmt", "vlan": 0},
        })
        template = {"port_config": {"ge-0/0/0": {"usage": "wan", "name": "wan"}}}
        port_stats = {
            "ge-0/0/0": {"port_id": "ge-0/0/0", "up": True, "unconfigured": False},
            "ae2": {"port_id": "ae2", "up": True, "port_usage": "lan", "unconfigured": False,
                    "lacp_stats": []},
        }

        result = _run(_validate_single_gateway(
            gw_stat, template, {}, {}, port_stats, {}, {},
        ))

        ae_port = next(p for p in result["lan_ports"] if p["interface"] == "ae2")
        assert ae_port["up"] is True

    def test_ae_networks_from_if_stat(self):
        """ae port networks are built from if_stat subinterfaces."""
        gw_stat = _make_gw_stat(if_stat={
            "ae2.0": {"port_id": "ae2", "up": True, "network_name": "mgmt", "vlan": 0},
            "ae2.100": {"port_id": "ae2", "up": True, "network_name": "data", "vlan": 100},
        })
        template = {"port_config": {}}
        port_stats = {
            "ae2": {"port_id": "ae2", "up": True, "port_usage": "lan", "unconfigured": False,
                    "lacp_stats": []},
        }

        result = _run(_validate_single_gateway(
            gw_stat, template, {}, {}, port_stats, {}, {},
        ))

        ae_port = next(p for p in result["lan_ports"] if p["interface"] == "ae2")
        assert "mgmt" in ae_port["network"]
        assert "data" in ae_port["network"]

    def test_ae_members_included(self):
        """ae port includes member details from port stats."""
        gw_stat = _make_gw_stat(if_stat={
            "ae2.0": {"port_id": "ae2", "up": True, "network_name": "mgmt", "vlan": 0},
        })
        template = {"port_config": {}}
        port_stats = {
            "ae2": {"port_id": "ae2", "up": True, "port_usage": "lan", "unconfigured": False,
                    "lacp_stats": [{"name": "ge-0/0/2"}, {"name": "ge-0/0/3"}]},
            "ge-0/0/2": {"port_id": "ge-0/0/2", "up": True, "port_parent": "ae2",
                         "unconfigured": False, "neighbor_system_name": "switch-1",
                         "neighbor_port_desc": "ge-0/0/0"},
            "ge-0/0/3": {"port_id": "ge-0/0/3", "up": True, "port_parent": "ae2",
                         "unconfigured": False, "neighbor_system_name": "switch-2",
                         "neighbor_port_desc": "ge-0/0/1"},
        }

        result = _run(_validate_single_gateway(
            gw_stat, template, {}, {}, port_stats, {}, {},
        ))

        ae_port = next(p for p in result["lan_ports"] if p["interface"] == "ae2")
        assert "members" in ae_port
        members = ae_port["members"]
        assert len(members) == 2
        member_ifaces = {m["interface"] for m in members}
        assert member_ifaces == {"ge-0/0/2", "ge-0/0/3"}
        assert all(m["up"] for m in members)


class TestUnconfiguredPortFiltering:
    """Ports marked unconfigured in port stats are excluded."""

    def test_unconfigured_port_skipped(self):
        """ge-0/0/5 with unconfigured=True in port stats should not appear."""
        gw_stat = _make_gw_stat()
        template = {
            "port_config": {
                "ge-0/0/0": {"usage": "wan", "name": "wan"},
                "ge-0/0/5": {"usage": "lan"},
            }
        }
        port_stats = {
            "ge-0/0/0": {"port_id": "ge-0/0/0", "up": True, "unconfigured": False},
            "ge-0/0/5": {"port_id": "ge-0/0/5", "up": False, "unconfigured": True},
        }

        result = _run(_validate_single_gateway(
            gw_stat, template, {}, {}, port_stats, {}, {},
        ))

        all_ifaces = [p["interface"] for p in result["wan_ports"] + result["lan_ports"]]
        assert "ge-0/0/0" in all_ifaces
        assert "ge-0/0/5" not in all_ifaces

    def test_configured_port_included(self):
        """Ports with unconfigured=False appear normally."""
        gw_stat = _make_gw_stat()
        template = {
            "port_config": {
                "ge-0/0/0": {"usage": "wan", "name": "wan"},
                "ge-0/0/2": {"usage": "lan"},
            }
        }
        port_stats = {
            "ge-0/0/0": {"port_id": "ge-0/0/0", "up": True, "unconfigured": False},
            "ge-0/0/2": {"port_id": "ge-0/0/2", "up": True, "unconfigured": False},
        }

        result = _run(_validate_single_gateway(
            gw_stat, template, {}, {}, port_stats, {}, {},
        ))

        all_ifaces = [p["interface"] for p in result["wan_ports"] + result["lan_ports"]]
        assert "ge-0/0/0" in all_ifaces
        assert "ge-0/0/2" in all_ifaces
