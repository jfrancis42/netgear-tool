# netgear-tool

A Python SDK and interactive CLI for **Netgear Smart Managed Plus switches** —
no browser required.

These switches have no REST API, no SSH, and no serial console.  This project
reverse-engineers the HTTP web UI shared by the Netgear Smart Managed Plus
switch family to provide a clean Python interface and a Cisco IOS-inspired
shell.

## Verified hardware

The following hardware has been confirmed working end-to-end with all read and
write operations:

| Model | Hardware version | Firmware | Ports | Notes |
|-------|-----------------|----------|-------|-------|
| GS105Ev2 | v2 | V1.6.0.24 | 5 | Verified |
| GS305E | v1 | V1.0.0.16 | 5 | Verified |

Other Netgear Smart Managed Plus switches that share the same web UI
(GS108Ev3, GS116Ev2, etc.) are expected to be compatible.  Please open an
issue if something doesn't work on your hardware.

## Installation

```bash
pip install netgear-tool
```

After installation, the `netgear` CLI command is available and the
`netgear_tool` Python package is importable:

```python
from netgear_tool import Switch, PortSpeed, RateLimit
```

The legacy module name `netgear_switch` is still importable as a
backward-compatibility shim.

## Files

| File | Purpose |
|---|---|
| `src/netgear_tool/__init__.py` | Python SDK (`Switch` class, all read/write operations) |
| `src/netgear_tool/_cli.py` | CLI entry point (installed as `netgear` command) |
| `cli.py` | Standalone CLI script (same as `netgear` command, for direct use) |
| `netgear_switch.py` | Backward-compatibility shim (imports from `netgear_tool`) |
| `docs/sdk.md` | SDK programmer reference |
| `docs/cli.md` | CLI user guide |

## Requirements

```bash
pip install netgear-tool
```

No other dependencies beyond `requests`.  HTML parsing is done with regex
against the known page structure.

## Quick start — SDK

Use `make_switch()` to auto-detect the model and firmware version:

```python
from netgear_tool import make_switch, PortSpeed, RateLimit

with make_switch('192.168.0.1', password='secret') as sw:
    print(sw.model, sw.firmware)   # e.g. 'GS305E', 'V2.6.0.48'

    # Read system info
    print(sw.get_system_info())

    # Port status
    for port in sw.get_port_settings():
        print(port)

    # Configure a port (use Switch() directly if make_switch isn't needed)
    sw.set_port(1, speed=PortSpeed.AUTO, fc_enabled=True)

    # Rate limiting
    sw.set_rate_limit(3, RateLimit.M64, RateLimit.M128)

    # 802.1Q VLANs
    sw.set_dot1q_enabled(True)
    sw.add_vlan(10)
    sw.set_vlan_membership(10, '11112')   # ports 1-4 untagged, port 5 tagged
    sw.set_port_pvid(1, 10)
```

See [docs/sdk.md](docs/sdk.md) for the full API reference.

## Quick start — CLI

```bash
netgear 192.168.0.1
# or directly:
python3 cli.py 192.168.0.1
```

```
GS105Ev2# show interfaces
GS105Ev2# configure terminal
GS105Ev2(config)# loop-detection
GS105Ev2(config)# vlan 10
GS105Ev2(config-vlan-10)# exit
GS105Ev2(config)# interface gi1
GS105Ev2(config-if-gi1)# switchport access vlan 10
GS105Ev2(config-if-gi1)# exit
GS105Ev2(config)# end
GS105Ev2# show vlan
GS105Ev2# show running-config
```

Commands can be abbreviated to their shortest unambiguous prefix (`conf t`,
`sho int`, `sw acc vl 10`, etc.).

See [docs/cli.md](docs/cli.md) for the full command reference.

## What is supported

### Read operations
- System info (firmware, MAC, IP, model, serial)
- Switch configuration (name, IP/DHCP settings)
- Port settings (speed, duplex, flow control)
- Port statistics (TX/RX byte counters, CRC errors)
- Rate limits (ingress/egress per port)
- Port mirroring
- IGMP snooping
- Loop detection
- Broadcast filtering
- QoS mode
- Power saving (Green Ethernet) — read-only; see firmware note below
- 802.1Q VLAN (IDs, membership, PVIDs)
- Cable diagnostics (TDR)

### Write operations
Everything listed above except power saving, plus:
- Factory reset
- Reboot
- Password change

## Known firmware issues

### Power saving (Green Ethernet) — GS105Ev2 firmware V1.6.0.24

`set_power_saving()` is implemented and the POST succeeds, but the switch
ignores the request — a subsequent GET always returns the hardware-controlled
state.  This appears to be a firmware limitation on the GS105Ev2: the web UI
shows the toggle but the hardware drives the actual state.  The method is
provided for completeness; it may work on other models or firmware versions.

### Ports cannot be fully removed from VLAN 1

The firmware silently rejects any attempt to set a port's VLAN 1 membership to
`'3'` (not-a-member).  Ports that are access members of another VLAN remain as
tagged members of VLAN 1 in the firmware — they simply have their PVID set to
the access VLAN so untagged ingress traffic is steered correctly.  The CLI's
`show running-config` hides this detail and displays such ports as access ports
on their PVID VLAN.

## Protocol notes

The switch uses an HTTP web UI on port 80.  All pages are at `/<name>.cgi`.

- **Reads**: `GET /<name>.cgi` with a valid `SID` cookie — state is embedded
  in HTML `<input type="hidden">` form elements (not JavaScript variables).
- **Writes**: `POST /<name>.cgi` — every POST must include a session-specific
  CSRF `hash` value extracted from the page's GET response.
- **Authentication**: `MD5(merge(password, rand))` where `rand` is a nonce
  provided in a custom HTTP response header and hidden form field.
- **Session cookie**: The `SID` value contains RFC 6265-invalid characters
  that Python's `http.cookiejar` silently drops.  The SDK works around this
  by extracting the SID from the raw `Set-Cookie` header.
- **Single-session limit**: the switch allows only one active session at a
  time.  The SDK always calls `logout()` (via the context manager) to free
  the slot.

## License

[GNU General Public License v3.0](LICENSE)
