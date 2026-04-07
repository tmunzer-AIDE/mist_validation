"""Tests for gateway port_config merging and AE (LACP) interface resolution."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.validation_service import (
    _expand_port_config_ranges,
    _merge_port_configs,
    _parse_interface_key,
    _validate_single_gateway,
)


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
        # Range keys are expanded into individual interfaces
        for port_key in ("ge-0/0/2", "ge-0/0/3"):
            port = result[port_key]
            assert port["aggregated"] is True
            assert port["ae_idx"] == "2"

    def test_shallow_merge_regression(self):
        """Shallow merge loses fields — deep merge preserves them."""
        template_pc = {"ge-0/0/2-3": {"aggregated": True, "ae_idx": "2", "usage": "lan"}}
        device_pc = {"ge-0/0/2-3": {"usage": "lan"}}

        shallow = {**template_pc, **device_pc}
        assert "aggregated" not in shallow["ge-0/0/2-3"]

        deep = _merge_port_configs({"port_config": template_pc}, {}, {"port_config": device_pc})
        assert deep["ge-0/0/2"]["aggregated"] is True
        assert deep["ge-0/0/3"]["aggregated"] is True

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
        for port_key in ("ge-0/0/2", "ge-0/0/3"):
            assert result[port_key]["aggregated"] is True
            assert result[port_key]["usage"] == "lan"
            assert result[port_key]["port_network"] == "mgmt"

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


# ---------------------------------------------------------------------------
# _parse_interface_key
# ---------------------------------------------------------------------------

class TestParseInterfaceKey:

    def test_single_interface(self):
        assert _parse_interface_key("ge-0/0/2") == ["ge-0/0/2"]

    def test_simple_range(self):
        assert _parse_interface_key("ge-0/0/2-5") == [
            "ge-0/0/2", "ge-0/0/3", "ge-0/0/4", "ge-0/0/5",
        ]

    def test_comma_separated(self):
        assert _parse_interface_key("ge-0/0/2,ge-0/0/6") == [
            "ge-0/0/2", "ge-0/0/6",
        ]

    def test_comma_and_range_combined(self):
        assert _parse_interface_key("ge-0/0/2-4,ge-0/0/6") == [
            "ge-0/0/2", "ge-0/0/3", "ge-0/0/4", "ge-0/0/6",
        ]

    def test_both_segments_ranged(self):
        assert _parse_interface_key("ge-0/0/2-3,ge-0/0/6-7") == [
            "ge-0/0/2", "ge-0/0/3", "ge-0/0/6", "ge-0/0/7",
        ]

    def test_no_slash_passthrough(self):
        assert _parse_interface_key("ae2") == ["ae2"]

    def test_different_interface_types(self):
        assert _parse_interface_key("xe-0/0/10-12") == [
            "xe-0/0/10", "xe-0/0/11", "xe-0/0/12",
        ]
        assert _parse_interface_key("mge-0/0/0-2") == [
            "mge-0/0/0", "mge-0/0/1", "mge-0/0/2",
        ]

    def test_reversed_range_passthrough(self):
        assert _parse_interface_key("ge-0/0/7-2") == ["ge-0/0/7-2"]

    def test_multi_digit_fpc_pic(self):
        assert _parse_interface_key("ge-1/2/0-2") == [
            "ge-1/2/0", "ge-1/2/1", "ge-1/2/2",
        ]


# ---------------------------------------------------------------------------
# _expand_port_config_ranges
# ---------------------------------------------------------------------------

class TestExpandPortConfigRanges:

    def test_single_interface_passthrough(self):
        pc = {"ge-0/0/0": {"usage": "wan"}}
        assert _expand_port_config_ranges(pc) == {"ge-0/0/0": {"usage": "wan"}}

    def test_simple_range(self):
        pc = {"ge-0/0/2-4": {"usage": "lan", "networks": ["corp"]}}
        result = _expand_port_config_ranges(pc)
        assert set(result.keys()) == {"ge-0/0/2", "ge-0/0/3", "ge-0/0/4"}
        for cfg in result.values():
            assert cfg["usage"] == "lan"
            assert cfg["networks"] == ["corp"]

    def test_comma_separated(self):
        pc = {"ge-0/0/2,ge-0/0/6": {"usage": "lan"}}
        result = _expand_port_config_ranges(pc)
        assert set(result.keys()) == {"ge-0/0/2", "ge-0/0/6"}

    def test_comma_and_range_combined(self):
        pc = {"ge-0/0/2-4,ge-0/0/6": {"usage": "lan"}}
        result = _expand_port_config_ranges(pc)
        assert set(result.keys()) == {"ge-0/0/2", "ge-0/0/3", "ge-0/0/4", "ge-0/0/6"}

    def test_non_standard_name_passthrough(self):
        pc = {"ae2": {"usage": "lan", "aggregated": True}}
        assert _expand_port_config_ranges(pc) == pc

    def test_deep_copy_independence(self):
        pc = {"ge-0/0/2-3": {"usage": "lan", "networks": ["corp"]}}
        result = _expand_port_config_ranges(pc)
        result["ge-0/0/2"]["networks"].append("guest")
        assert result["ge-0/0/3"]["networks"] == ["corp"]

    def test_empty_input(self):
        assert _expand_port_config_ranges({}) == {}

    def test_mixed_keys(self):
        pc = {
            "ge-0/0/0": {"usage": "wan"},
            "ge-0/0/2-4": {"usage": "lan"},
            "ae2": {"aggregated": True},
        }
        result = _expand_port_config_ranges(pc)
        assert set(result.keys()) == {
            "ge-0/0/0", "ge-0/0/2", "ge-0/0/3", "ge-0/0/4", "ae2",
        }


# ---------------------------------------------------------------------------
# Cross-layer range expansion in merge
# ---------------------------------------------------------------------------

class TestCrossLayerRangeExpansion:

    def test_device_single_overrides_template_range(self):
        """Device-level 'ge-0/0/5' merges with template 'ge-0/0/2-7'."""
        template = {
            "port_config": {
                "ge-0/0/2-7": {
                    "usage": "lan",
                    "aggregated": True,
                    "ae_idx": "2",
                    "networks": ["corp", "byod"],
                    "port_network": "mgmt",
                }
            }
        }
        device = {
            "port_config": {
                "ge-0/0/5": {
                    "usage": "lan",
                    "networks": ["special"],
                    "port_network": "secure",
                }
            }
        }
        result = _merge_port_configs(template, {}, device)
        # ge-0/0/5 has device overrides merged with template base
        assert result["ge-0/0/5"]["aggregated"] is True
        assert result["ge-0/0/5"]["ae_idx"] == "2"
        assert result["ge-0/0/5"]["networks"] == ["special"]
        assert result["ge-0/0/5"]["port_network"] == "secure"
        # Other ports from the range keep template config
        for p in (2, 3, 4, 6, 7):
            key = f"ge-0/0/{p}"
            assert key in result
            assert result[key]["aggregated"] is True
            assert result[key]["networks"] == ["corp", "byod"]
            assert result[key]["port_network"] == "mgmt"

    def test_device_comma_key_overrides_template_range(self):
        """Device comma key partially overlaps template range."""
        template = {"port_config": {"ge-0/0/0-3": {"usage": "lan", "port_network": "mgmt"}}}
        device = {"port_config": {"ge-0/0/1,ge-0/0/3": {"port_network": "secure"}}}
        result = _merge_port_configs(template, {}, device)
        assert result["ge-0/0/0"]["port_network"] == "mgmt"
        assert result["ge-0/0/1"]["port_network"] == "secure"
        assert result["ge-0/0/2"]["port_network"] == "mgmt"
        assert result["ge-0/0/3"]["port_network"] == "secure"
