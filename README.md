# netgear-tool

A Python SDK and interactive CLI for **Netgear Smart Managed Plus switches** —
no browser required.

These switches have no REST API, no SSH, and no serial console.  This project
reverse-engineers the HTTP web UI shared by the Netgear Smart Managed Plus
switch family to provide a clean Python interface and a Cisco IOS-inspired
shell.

Developed and tested on:
- Hardware: GS105Ev2 (5-port gigabit)
- Firmware: V1.6.0.24

A full test suite passes against this hardware.  Other Netgear Smart Managed
Plus switches that share the same web UI (GS108Ev3, GS116Ev2, etc.) are likely
compatible.

## Files

| File | Purpose |
|---|---|
| `netgear_switch.py` | Python SDK (`Switch` class, all read/write operations) |
| `cli.py` | Cisco IOS-inspired interactive CLI |
| `test_read.py` | Smoke test: all read operations against live switch |
| `test_writes.py` | Write verification tests |
| `docs/sdk.md` | SDK programmer reference |
| `docs/cli.md` | CLI user guide |

## Requirements

```bash
pip install requests
```

No other dependencies.  HTML parsing is done with regex against the known
page structure.

## Quick start — SDK

```python
from netgear_switch import Switch, RateLimit

with Switch('192.168.0.1', password='secret') as sw:
    # Read system info
    print(sw.get_system_info())

    # Port status
    for port in sw.get_port_settings():
        print(port)

    # Configure a port
    from netgear_switch import PortSpeed
    sw.set_port(1, speed=PortSpeed.AUTO, fc_enabled=True)

    # Rate limiting
    sw.set_rate_limit(3, RateLimit.M64, RateLimit.M128)

    # 802.1Q VLANs
    sw.set_dot1q_enabled(True)
    sw.add_vlan(10)
    sw.set_vlan_membership(10, '10001')   # ports 1 and 5 untagged
    sw.set_port_pvid(1, 10)
```

See [docs/sdk.md](docs/sdk.md) for the full API reference.

## Quick start — CLI

```bash
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
