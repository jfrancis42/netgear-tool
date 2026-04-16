# Netgear GS105Ev2 Python SDK Reference

`netgear_switch.py` is a pure-Python library for reading and configuring
Netgear Smart Managed Plus switches.  It requires only the `requests` package.
Developed and verified against the GS105Ev2 (firmware V1.6.0.24); other models
that share the same web UI (GS108Ev3, GS116Ev2, etc.) are likely compatible.

## Contents

- [Quick start](#quick-start)
- [Session management](#session-management)
- [Data types](#data-types)
- [API reference](#api-reference)
  - [System](#system)
  - [Ports](#ports)
  - [Port statistics](#port-statistics)
  - [Rate limiting](#rate-limiting)
  - [Port mirroring](#port-mirroring)
  - [IGMP snooping](#igmp-snooping)
  - [Loop detection](#loop-detection)
  - [Broadcast filtering](#broadcast-filtering)
  - [QoS mode](#qos-mode)
  - [Power saving](#power-saving)
  - [802.1Q VLAN](#8021q-vlan)
  - [Port PVID](#port-pvid)
  - [Cable diagnostics](#cable-diagnostics)
  - [Maintenance](#maintenance)
- [Error handling](#error-handling)

---

## Quick start

```python
from netgear_switch import Switch, PortSpeed, RateLimit

with Switch('192.168.0.1', password='secret') as sw:
    # Read system info
    info = sw.get_system_info()
    print(info.firmware)          # "V1.6.0.24"

    # Read all port states
    for port in sw.get_port_settings():
        print(port)

    # Configure a port
    sw.set_port(1, speed=PortSpeed.AUTO, fc_enabled=True)

    # Set rate limits
    sw.set_rate_limit(3, RateLimit.M64, RateLimit.M128)

    # Enable loop detection
    sw.set_loop_detection(True)

    # Configure VLANs
    sw.set_dot1q_enabled(True)
    sw.add_vlan(10)
    sw.set_vlan_membership(10, '10001')  # ports 1 and 5 untagged
    sw.set_port_pvid(1, 10)
```

---

## Session management

The switch enforces a **single-session limit** — only one login can be active
at any time.  The SDK always calls `logout()` on exit to free the slot.

- **Context manager** — recommended.  Logs in on entry, logs out on exit.
- **Manual** — call `login()` and `logout()` yourself.

```python
# Context manager (recommended)
with Switch('192.168.0.1', password='secret') as sw:
    sw.set_loop_detection(True)

# Manual
sw = Switch('192.168.0.1', password='secret')
sw.login()
sw.set_loop_detection(True)
sw.logout()

# Custom timeout (seconds)
sw = Switch('192.168.0.1', password='secret', timeout=30.0)
```

**Session timeout:** the switch expires idle sessions after approximately
5 minutes.  Long scripts should call `sw._keepalive()` periodically (a `GET
/getstatus.cgi` with no side effects) or simply re-enter the context manager.

---

## Data types

### Enumerations

```python
class PortSpeed(IntEnum):
    NONE    = 0   # not applicable
    AUTO    = 1   # auto-negotiate (up to 1 Gbit/s)
    DISABLE = 2   # port administratively disabled
    M10H    = 3   # 10 Mbps half-duplex
    M10F    = 4   # 10 Mbps full-duplex
    M100H   = 5   # 100 Mbps half-duplex
    M100F   = 6   # 100 Mbps full-duplex

class RateLimit(IntEnum):
    NONE     = 0   # placeholder (not sent to switch)
    NO_LIMIT = 1   # no rate limit
    K512     = 2   # 512 Kbit/s
    M1       = 3   # 1 Mbit/s
    M2       = 4
    M4       = 5
    M8       = 6
    M16      = 7
    M32      = 8
    M64      = 9
    M128     = 10
    M256     = 11
    M512     = 12  # 512 Mbit/s
```

### Dataclasses

| Class | Key fields |
|---|---|
| `SystemInfo` | `mac`, `ip`, `netmask`, `gateway`, `firmware` |
| `SwitchConfig` | `model`, `name`, `serial`, `mac`, `firmware`, `dhcp`, `ip`, `netmask`, `gateway` |
| `PortInfo` | `port`, `enabled`, `speed_cfg: PortSpeed`, `speed_act: str`, `fc_enabled`, `max_mtu` |
| `PortStats` | `port`, `bytes_rx`, `bytes_tx`, `crc_errors` |
| `PortRateLimit` | `port`, `ingress: RateLimit`, `egress: RateLimit` |
| `MirrorConfig` | `enabled`, `dest_port`, `source_ports: List[int]` |
| `IGMPConfig` | `enabled`, `vlan_id`, `validate_ip_header`, `block_unknown_multicast`, `static_router_port` |

---

## API reference

### System

#### `get_system_info() → SystemInfo`

Read-only snapshot from `info.cgi`.

```python
info = sw.get_system_info()
print(info.mac)       # "6C:B0:CE:29:96:82"
print(info.firmware)  # "V1.6.0.24"
```

#### `get_switch_config() → SwitchConfig`

Full configuration from `switch_info.cgi`, including the writable fields.

```python
cfg = sw.get_switch_config()
print(cfg.model)   # "GS105Ev2"
print(cfg.name)    # user-settable switch name
print(cfg.dhcp)    # True/False
```

#### `set_switch_name(name: str)`

```python
sw.set_switch_name('lab-gs105-1')
```

#### `set_ip_settings(ip=None, netmask=None, gateway=None, dhcp=None)`

Any `None` argument is re-read from the switch and left unchanged.

```python
# Static IP
sw.set_ip_settings(ip='192.168.0.1', netmask='255.255.255.0',
                   gateway='192.168.0.254', dhcp=False)

# Enable DHCP
sw.set_ip_settings(dhcp=True)
```

> **Note:** Changing the IP address or enabling DHCP disconnects you from
> the current address.

#### `change_password(old_password: str, new_password: str)`

```python
sw.change_password('oldpass', 'newpass')
```

---

### Ports

#### `get_port_settings() → List[PortInfo]`

Returns one `PortInfo` per physical port.

```python
for p in sw.get_port_settings():
    state = 'up' if p.enabled else 'down'
    print(f'Port {p.port}: {state}  link={p.speed_act}  cfg={p.speed_cfg}')
```

`p.enabled` is `True` when the port has a link (`status == 'Up'`).
`p.speed_cfg == PortSpeed.DISABLE` means the port is administratively
disabled.

#### `set_port(port: int, speed=None, fc_enabled=None, description=None)`

Configure a single port.  Any `None` argument is left unchanged (the
current value is re-read first).

```python
sw.set_port(1, speed=PortSpeed.AUTO)
sw.set_port(2, speed=PortSpeed.DISABLE)   # administratively disable
sw.set_port(3, speed=PortSpeed.M100F, fc_enabled=True)
sw.set_port(4, fc_enabled=False)
```

> **No bulk-set:** the Netgear SDK sets one port per call.  To configure
> multiple ports, call `set_port()` in a loop.

---

### Port statistics

#### `get_port_stats() → List[PortStats]`

64-bit byte counters (two 32-bit hidden inputs combined by the SDK).

```python
for s in sw.get_port_stats():
    print(f'Port {s.port}: RX={s.bytes_rx:,} B  TX={s.bytes_tx:,} B  CRC={s.crc_errors}')
```

#### `clear_port_stats()`

Reset all port counters to zero.

```python
sw.clear_port_stats()
```

---

### Rate limiting

#### `get_rate_limits() → List[PortRateLimit]`

```python
for r in sw.get_rate_limits():
    print(f'Port {r.port}: ingress={r.ingress}  egress={r.egress}')
```

#### `set_rate_limit(port: int, ingress: RateLimit, egress: RateLimit)`

```python
sw.set_rate_limit(3, RateLimit.M64, RateLimit.M128)
sw.set_rate_limit(3, RateLimit.NO_LIMIT, RateLimit.NO_LIMIT)  # remove limits
```

---

### Port mirroring

#### `get_mirror_config() → MirrorConfig`

```python
m = sw.get_mirror_config()
if m.enabled:
    print(f'Destination: {m.dest_port}')
    print(f'Sources: {m.source_ports}')
```

#### `set_mirror_config(enabled: bool, dest_port: int, source_ports: List[int])`

> **Hardware limitation:** The GS105Ev2 mirrors all traffic (rx+tx) on
> the selected source ports.  Ingress-only or egress-only mirroring is not
> supported.

```python
# Mirror ports 1-3 to port 5
sw.set_mirror_config(enabled=True, dest_port=5, source_ports=[1, 2, 3])

# Disable mirroring
sw.set_mirror_config(enabled=False, dest_port=1, source_ports=[])
```

---

### IGMP snooping

#### `get_igmp_config() → IGMPConfig`

```python
igmp = sw.get_igmp_config()
print(igmp.enabled)
print(igmp.vlan_id)               # '' = all VLANs
print(igmp.static_router_port)   # '0' = none, '1'-'5' = specific port
```

#### `set_igmp_config(enabled, vlan_id='', validate_ip_header=False, block_unknown_multicast=False, static_router_port='0')`

```python
sw.set_igmp_config(enabled=True)
sw.set_igmp_config(enabled=True, vlan_id='10', static_router_port='5',
                   validate_ip_header=True, block_unknown_multicast=True)
sw.set_igmp_config(enabled=False)
```

---

### Loop detection

#### `get_loop_detection() → bool`

```python
print('Loop detection:', sw.get_loop_detection())
```

#### `set_loop_detection(enabled: bool)`

```python
sw.set_loop_detection(True)
sw.set_loop_detection(False)
```

---

### Broadcast filtering

#### `get_broadcast_filter() → bool`

```python
print('Broadcast filter:', sw.get_broadcast_filter())
```

#### `set_broadcast_filter(enabled: bool)`

```python
sw.set_broadcast_filter(True)
sw.set_broadcast_filter(False)
```

---

### QoS mode

#### `get_qos_mode() → str`

Returns `'port-based'` or `'802.1p/dscp'`.

```python
print('QoS mode:', sw.get_qos_mode())
```

#### `set_qos_mode(mode: str)`

```python
sw.set_qos_mode('port-based')
sw.set_qos_mode('802.1p/dscp')
```

---

### Power saving

#### `get_power_saving() → bool`

```python
print('Power saving:', sw.get_power_saving())
```

#### `set_power_saving(enabled: bool)`

> **Firmware limitation (GS105Ev2 V1.6.0.24):** The POST succeeds but the
> switch ignores the request.  A subsequent GET always returns the
> hardware-controlled state.  This method is provided for completeness.

```python
sw.set_power_saving(False)
```

---

### 802.1Q VLAN

#### `get_dot1q_enabled() → bool`

```python
print('802.1Q enabled:', sw.get_dot1q_enabled())
```

#### `set_dot1q_enabled(enabled: bool)`

```python
sw.set_dot1q_enabled(True)
sw.set_dot1q_enabled(False)
```

#### `get_vlan_ids() → List[int]`

```python
vids = sw.get_vlan_ids()
print('VLANs:', vids)   # [1, 10, 20]
```

#### `get_vlan_membership(vid: int) → str`

Returns a string of length `port_count`, one character per port:
`'0'` = not member, `'1'` = untagged member, `'2'` = tagged member.

```python
mem = sw.get_vlan_membership(10)
print(mem)   # '10001' — ports 1 and 5 are untagged members
for i, c in enumerate(mem, start=1):
    role = {'0': 'not member', '1': 'untagged', '2': 'tagged'}.get(c, '?')
    print(f'  Port {i}: {role}')
```

#### `set_vlan_membership(vid: int, membership: str)`

`membership` is one character per port in port order.

```python
# VLAN 10: port 1 untagged, port 5 tagged, others not members
sw.set_vlan_membership(10, '10002')
```

#### `add_vlan(vid: int)`

Create a new VLAN (802.1Q mode must be enabled first).

```python
sw.set_dot1q_enabled(True)
sw.add_vlan(10)
sw.add_vlan(20)
```

#### `delete_vlan(vid: int)`

```python
sw.delete_vlan(10)
```

---

### Port PVID

#### `get_port_pvids() → Dict[int, int]`

Returns `{port_number: pvid}` for all ports.

```python
pvids = sw.get_port_pvids()
for port, pvid in sorted(pvids.items()):
    print(f'Port {port}: PVID={pvid}')
```

#### `set_port_pvid(port: int, pvid: int)`

```python
sw.set_port_pvid(1, 10)   # set port 1 PVID to 10
sw.set_port_pvid(5, 1)    # set port 5 PVID back to 1
```

---

### Cable diagnostics

#### `test_cable(ports: List[int]) → str`

Runs TDR diagnostics on the specified ports.  Returns the raw HTML response.
Traffic on tested ports is briefly disrupted.

```python
html = sw.test_cable([1, 2, 3])
# Parse the result table yourself, or use the CLI's 'test cable-diagnostics'
```

---

### Maintenance

#### `reboot()`

```python
sw.reboot()   # switch reboots; current session becomes invalid
```

#### `factory_reset()`

> **WARNING:** Erases all configuration and resets the IP to the factory
> default (`192.168.0.1`, password `password`).

```python
sw.factory_reset()
```

---

## Error handling

Authentication failures raise `RuntimeError` with a descriptive message.

Network errors propagate as `requests.exceptions.RequestException` subclasses.

```python
from requests.exceptions import ConnectionError, Timeout

try:
    with Switch('192.168.0.1', password='wrong') as sw:
        sw.get_system_info()
except RuntimeError as e:
    print('Auth error:', e)
    # Common messages:
    # "Login failed: The maximum number of sessions has been reached."
    # "Login failed: Wrong password."
except ConnectionError:
    print('Switch unreachable')
except Timeout:
    print('Request timed out')
```

The switch allows only one active session.  If `Login failed: The maximum
number of sessions has been reached.` is raised, a previous session is still
active (e.g. a browser tab or a script that did not call `logout()`).  Wait
~5 minutes for the idle timeout, or reboot the switch to clear it.
