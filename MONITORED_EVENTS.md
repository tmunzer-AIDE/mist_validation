# Monitored Device Events

The validation report tracks device events from the last 24 hours. Events are correlated into trigger/clear pairs: if a trigger event has a matching clear event, the issue is marked as "cleared"; otherwise it remains "triggered" (open).

Events are fetched via the Mist API `searchSiteDeviceEvents` endpoint with a 24-hour lookback window.

## How Correlation Works

Each event type maps to a **category** and a **role** (trigger or clear). Events within the same category are paired together. Some categories also track a **sub-identifier** (e.g., a specific port or neighbor) so that events on different ports are tracked independently.

For example, `SW_PORT_DOWN` on port `ge-0/0/1` is only cleared by `SW_PORT_UP` on the same port `ge-0/0/1`, not by a port-up event on a different port.

---

## Access Point Events

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| AP Config Failed | `AP_CONFIG_FAILED` | `AP_CONFIGURED`, `AP_RECONFIGURED` | -- |
| AP Disconnected | `AP_DISCONNECTED` | `AP_CONNECTED` | -- |
| AP Port Down | `AP_PORT_DOWN` | `AP_PORT_UP` | port_id |
| AP RADSEC Failure | `AP_RADSEC_FAILURE` | `AP_RADSEC_RECOVERY` | -- |
| AP Upgrade Failed | `AP_UPGRADE_FAILED` | `AP_UPGRADED` | -- |

## Switch Events

### Connectivity & Status

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| SW Disconnected | `SW_DISCONNECTED` | `SW_CONNECTED` | -- |
| SW Config Failed | `SW_CONFIG_FAILED`, `SW_CONFIG_LOCK_FAILED`, `SW_CONFIG_ERROR_ADDTL_COMMAND` | `SW_CONFIGURED`, `SW_RECONFIGURED` | -- |
| SW Upgrade Failed | `SW_UPGRADE_FAILED` | `SW_UPGRADED` | -- |
| SW ZTP Failed | `SW_ZTP_FAILED` | `SW_ZTP_FINISHED` | -- |
| SW Recovery Snapshot Failed | `SW_RECOVERY_SNAPSHOT_FAILED` | `SW_RECOVERY_SNAPSHOT_SUCCEEDED`, `SW_RECOVERY_SNAPSHOT_NOTNEEDED` | -- |

### Ports & Interfaces

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| SW Port Down | `SW_PORT_DOWN` | `SW_PORT_UP` | port_id |
| SW Port BPDU Blocked | `SW_PORT_BPDU_BLOCKED` | `SW_PORT_BPDU_ERROR_CLEARED` | port_id |
| SW VC Port Down | `SW_VC_PORT_DOWN` | `SW_VC_PORT_UP` | port_id |
| SW LACP Timeout | `SW_LACPD_TIMEOUT` | `SW_LACPD_TIMEOUT_CLEARED` | port_id |
| SW Loop Detected | `SW_LOOP_DETECTED` | `SW_LOOP_CLEARED` | -- |

### Routing

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| SW BGP Neighbor Down | `SW_BGP_NEIGHBOR_DOWN` | `SW_BGP_NEIGHBOR_UP` | text:neighbor |
| SW OSPF Neighbor Down | `SW_OSPF_NEIGHBOR_DOWN` | `SW_OSPF_NEIGHBOR_UP` | text:neighbor |
| SW BFD Session Down | `SW_BFD_SESSION_DISCONNECTED` | `SW_BFD_SESSION_ESTABLISHED` | -- |
| SW EVPN Core Isolated | `SW_EVPN_CORE_ISOLATED` | `SW_EVPN_CORE_ISOLATION_CLEARED` | -- |

### Chassis Alarms

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| SW Chassis Fan Alarm | `SW_ALARM_CHASSIS_FAN` | `SW_ALARM_CHASSIS_FAN_CLEAR` | -- |
| SW Chassis Hot Alarm | `SW_ALARM_CHASSIS_HOT` | `SW_ALARM_CHASSIS_HOT_CLEAR` | -- |
| SW Chassis Humidity Alarm | `SW_ALARM_CHASSIS_HUMIDITY` | `SW_ALARM_CHASSIS_HUMIDITY_CLEAR` | -- |
| SW Chassis Mgmt Link Down | `SW_ALARM_CHASSIS_MGMT_LINK_DOWN` | `SW_ALARM_CHASSIS_MGMT_LINK_DOWN_CLEAR` | -- |
| SW Chassis Partition Alarm | `SW_ALARM_CHASSIS_PARTITION` | `SW_ALARM_CHASSIS_PARTITION_CLEAR` | -- |
| SW Chassis PEM Alarm | `SW_ALARM_CHASSIS_PEM` | `SW_ALARM_CHASSIS_PEM_CLEAR` | -- |
| SW Chassis PoE Alarm | `SW_ALARM_CHASSIS_POE` | `SW_ALARM_CHASSIS_POE_CLEAR` | -- |
| SW Chassis PSU Alarm | `SW_ALARM_CHASSIS_PSU` | `SW_ALARM_CHASSIS_PSU_CLEAR` | -- |

### Other

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| SW IoT Alarm | `SW_ALARM_IOT_SET` | `SW_ALARM_IOT_CLEAR` | -- |
| SW VC Version Mismatch | `SW_ALARM_VIRTUAL_CHASSIS_VERSION_MISMATCH` | `SW_ALARM_VIRTUAL_CHASSIS_VERSION_MISMATCH_CLEAR` | -- |
| SW VC In Transition | `SW_VC_IN_TRANSITION` | `SW_VC_STABLE` | -- |
| SW DDoS Protocol Violation | `SW_DDOS_PROTOCOL_VIOLATION_SET` | `SW_DDOS_PROTOCOL_VIOLATION_CLEAR` | protocol_name |
| SW FPC Power Off | `SW_FPC_POWER_OFF` | `SW_FPC_POWER_ON` | fru_slot |
| SW MAC Learning Stopped | `SW_MAC_LEARNING_STOPPED` | `SW_MAC_LEARNING_RESUMED` | -- |
| SW MAC Limit Exceeded | `SW_MAC_LIMIT_EXCEEDED` | `SW_MAC_LIMIT_RESET` | port_id |

## Gateway Events

### Connectivity & Status

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW Disconnected | `GW_DISCONNECTED` | `GW_CONNECTED` | -- |
| GW Config Failed | `GW_CONFIG_FAILED`, `GW_CONFIG_LOCK_FAILED`, `GW_CONFIG_ERROR_ADDTL_COMMAND` | `GW_CONFIGURED`, `GW_RECONFIGURED` | -- |
| GW Upgrade Failed | `GW_UPGRADE_FAILED` | `GW_UPGRADED` | -- |
| GW ZTP Failed | `GW_ZTP_FAILED` | `GW_ZTP_FINISHED` | -- |
| GW Recovery Snapshot Failed | `GW_RECOVERY_SNAPSHOT_FAILED` | `GW_RECOVERY_SNAPSHOT_SUCCEEDED`, `GW_RECOVERY_SNAPSHOT_NOTNEEDED` | -- |

### Ports & Network

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW Port Down | `GW_PORT_DOWN` | `GW_PORT_UP` | port_id |
| GW ARP Unresolved | `GW_ARP_UNRESOLVED` | `GW_ARP_RESOLVED` | port_id |
| GW DHCP Unresolved | `GW_DHCP_UNRESOLVED` | `GW_DHCP_RESOLVED` | -- |
| GW Tunnel Down | `GW_TUNNEL_DOWN` | `GW_TUNNEL_UP` | text:Tunnel |

### Routing

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW BGP Neighbor Down | `GW_BGP_NEIGHBOR_DOWN` | `GW_BGP_NEIGHBOR_UP` | text:neighbor |
| GW OSPF Neighbor Down | `GW_OSPF_NEIGHBOR_DOWN` | `GW_OSPF_NEIGHBOR_UP` | text:neighbor |
| GW Conductor Disconnected | `GW_CONDUCTOR_DISCONNECTED` | `GW_CONDUCTOR_CONNECTED` | -- |

### VPN

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW VPN Path Down | `GW_VPN_PATH_DOWN` | `GW_VPN_PATH_UP` | text:path |
| GW VPN Peer Down | `GW_VPN_PEER_DOWN` | `GW_VPN_PEER_UP` | text:peer |

### High Availability

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW HA Control Link Down | `GW_HA_CONTROL_LINK_DOWN` | `GW_HA_CONTROL_LINK_UP` | -- |
| GW HA Health Weight Low | `GW_HA_HEALTH_WEIGHT_LOW` | `GW_HA_HEALTH_WEIGHT_RECOVERY` | text:Detected |

### Performance Thresholds

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW FIB Count Exceeded | `GW_FIB_COUNT_THRESHOLD_EXCEEDED` | `GW_FIB_COUNT_RETURNED_TO_NORMAL` | -- |
| GW Flow Count Exceeded | `GW_FLOW_COUNT_THRESHOLD_EXCEEDED` | `GW_FLOW_COUNT_RETURNED_TO_NORMAL` | -- |

### Chassis Alarms

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW Chassis Fan Alarm | `GW_ALARM_CHASSIS_FAN` | `GW_ALARM_CHASSIS_FAN_CLEAR` | -- |
| GW Chassis Hot Alarm | `GW_ALARM_CHASSIS_HOT` | `GW_ALARM_CHASSIS_HOT_CLEAR` | -- |
| GW Chassis Humidity Alarm | `GW_ALARM_CHASSIS_HUMIDITY` | `GW_ALARM_CHASSIS_HUMIDITY_CLEAR` | -- |
| GW Chassis Mgmt Link Down | `GW_ALARM_CHASSIS_MGMT_LINK_DOWN` | `GW_ALARM_CHASSIS_MGMT_LINK_DOWN_CLEAR` | -- |
| GW Chassis Partition Alarm | `GW_ALARM_CHASSIS_PARTITION` | `GW_ALARM_CHASSIS_PARTITION_CLEAR` | -- |
| GW Chassis PEM Alarm | `GW_ALARM_CHASSIS_PEM` | `GW_ALARM_CHASSIS_PEM_CLEAR` | -- |
| GW Chassis PoE Alarm | `GW_ALARM_CHASSIS_POE` | `GW_ALARM_CHASSIS_POE_CLEAR` | -- |
| GW Chassis PSU Alarm | `GW_ALARM_CHASSIS_PSU` | `GW_ALARM_CHASSIS_PSU_CLEAR` | -- |
| GW Chassis Warm Alarm | `GW_ALARM_CHASSIS_WARM` | `GW_ALARM_CHASSIS_WARM_CLEAR` | -- |

### Other

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| GW AppID Install Failed | `GW_APPID_INSTALL_FAILED` | `GW_APPID_INSTALLED` | -- |
| GW IDP Install Failed | `GW_IDP_INSTALL_FAILED` | `GW_IDP_INSTALLED` | -- |

## MX Edge Events

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| MX Edge Disconnected | `ME_DISCONNECTED` | `ME_CONNECTED` | -- |
| MX Edge Fan Unplugged | `ME_FAN_UNPLUGGED` | `ME_FAN_PLUGGED` | component |
| MX Edge Power Input Disconnected | `ME_POWERINPUT_DISCONNECTED` | `ME_POWERINPUT_CONNECTED` | component |
| MX Edge PSU Unplugged | `ME_PSU_UNPLUGGED` | `ME_PSU_PLUGGED` | component |
| MX Edge Service Failed | `ME_SERVICE_CRASHED`, `ME_SERVICE_FAILED` | `ME_SERVICE_STARTED` | service |

## ESL Events

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| ESL Hung | `ESL_HUNG` | `ESL_RECOVERED` | -- |

## Tunnel Terminator Events

| Display Name | Trigger Event(s) | Clear Event(s) | Sub-ID |
|---|---|---|---|
| TT Monitored Resource Failed | `TT_MONITORED_RESOURCE_FAILED` | `TT_MONITORED_RESOURCE_RECOVERED` | resource |
| TT Port Blocked | `TT_PORT_BLOCKED` | `TT_PORT_RECOVERY` | port |
| TT Port Dropped from LACP | `TT_PORT_DROPPED_FROM_LACP`, `TT_PORT_LAST_DROPPED_FROM_LACP` | `TT_PORT_JOINED_LACP`, `TT_PORT_FIRST_JOIN_LACP` | port |
| TT Port Link Down | `TT_PORT_LINK_DOWN` | `TT_PORT_LINK_RECOVERY` | port |
| TT Tunnels Lost | `TT_TUNNELS_LOST` | `TT_TUNNELS_UP` | -- |

---

## Sub-ID Types

| Sub-ID Field | Description | Example |
|---|---|---|
| -- | No sub-identifier; events are correlated per device | -- |
| `port_id` | Physical port identifier | `ge-0/0/1` |
| `component` | Hardware component name | `fan-0` |
| `service` | Software service name | `mxagent` |
| `resource` | Monitored resource name | -- |
| `port` | TunTerm port identifier | -- |
| `fru_slot` | FPC slot number | `0` |
| `protocol_name` | DDoS protocol name | -- |
| `text:keyword` | Parsed from event text after keyword | BGP neighbor IP |

## Event Statuses in Report

| Status | Meaning |
|---|---|
| **triggered** | The most recent event in this category is a trigger event (issue is open) |
| **cleared** | A clear event was received after the last trigger (issue is resolved) |

By default, the report UI only shows **triggered** (open) events. Use the "Show cleared" toggle in the device detail dialog to see all events.
