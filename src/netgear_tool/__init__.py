"""
Python SDK for Netgear Smart Managed Plus switches.

Developed against the GS105Ev2 (5-port gigabit, firmware V1.6.0.24).
May be compatible with other Netgear Smart Managed Plus switches that share
the same web UI (GS108Ev3, GS116Ev2, etc.).

Protocol summary
----------------
The switch has no REST API or CLI.  It is configured entirely through an
HTTP web UI.  All pages are at /<name>.cgi.

Authentication
  GET  /login.cgi          → HTML with rand value in a hidden field
                             AND in a custom 'rand:' HTTP response header
  POST /login.cgi          → Authenticate.
       password = md5(merge(pw, rand))
       where merge() interleaves pw and rand character by character.
  Success: Set-Cookie: SID=<value>; PATH=/; HttpOnly
  NOTE: The SID value contains RFC 6265-invalid characters ('[', '\\', '`')
        that Python's http.cookiejar silently drops.  This SDK manually
        extracts the SID from the raw Set-Cookie header and injects it.

Session
  - Single-session limit: the switch allows exactly one active session.
  - Session expires after approximately 5 minutes of inactivity.
  - Keep-alive: GET /getstatus.cgi returns "disable" (plain text, 8 bytes).
    Calling it keeps the session alive without side effects.
  - Logout: GET /logout.cgi (clears the session server-side).

Reading state
  GET /<name>.cgi  → HTML page with current configuration embedded in
                     HTML form elements.  Data is in <input type="hidden">
                     fields and visible <td> cells.  Unlike TP-Link firmware,
                     there are NO JavaScript variable assignments.

Writing configuration
  POST /<name>.cgi → Same URL as the read page.
       Must include: hash=<CSRF_token> (extracted from the read page)
       Plus all relevant form fields.

CSRF token
  Every page includes: <input type=hidden name='hash' id='hash' value="NNNNN">
  This value must be echoed back in all POST requests.

Usage
-----
    from netgear_tool import Switch

    with Switch('192.168.0.1', password='secret') as sw:
        info = sw.get_system_info()
        print(info)

        for p in sw.get_port_settings():
            print(p)
"""

from __future__ import annotations

import hashlib
import re
import time
import warnings
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

import requests


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PortSpeed(IntEnum):
    NONE    = 0   # not used / not applicable
    AUTO    = 1
    DISABLE = 2
    M10H    = 3   # 10 Mbps half-duplex
    M10F    = 4   # 10 Mbps full-duplex
    M100H   = 5   # 100 Mbps half-duplex
    M100F   = 6   # 100 Mbps full-duplex

    def __str__(self):
        labels = {
            0: 'None', 1: 'Auto', 2: 'Disable',
            3: '10M-Half', 4: '10M-Full',
            5: '100M-Half', 6: '100M-Full',
        }
        return labels.get(self.value, str(self.value))


class RateLimit(IntEnum):
    """Ingress/egress rate limit values for rateLimit.cgi."""
    NONE     = 0   # not applicable (select placeholder)
    NO_LIMIT = 1
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
    M512     = 12

    def __str__(self):
        labels = {
            0: '-', 1: 'No Limit',
            2: '512 Kbit/s', 3: '1 Mbit/s', 4: '2 Mbit/s',
            5: '4 Mbit/s', 6: '8 Mbit/s', 7: '16 Mbit/s',
            8: '32 Mbit/s', 9: '64 Mbit/s', 10: '128 Mbit/s',
            11: '256 Mbit/s', 12: '512 Mbit/s',
        }
        return labels.get(self.value, str(self.value))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SystemInfo:
    """Parsed from info.cgi (read-only snapshot)."""
    mac: str
    ip: str
    netmask: str
    gateway: str
    firmware: str

    def __str__(self):
        return (f"SystemInfo(MAC={self.mac}, IP={self.ip}/{self.netmask}, "
                f"GW={self.gateway}, FW={self.firmware})")


@dataclass
class SwitchConfig:
    """Parsed from switch_info.cgi (includes writable fields)."""
    model: str            # e.g. 'GS105Ev2'
    name: str             # user-settable switch name
    serial: str
    mac: str
    firmware: str
    dhcp: bool
    ip: str
    netmask: str
    gateway: str

    def __str__(self):
        return (f"SwitchConfig(model={self.model}, name={self.name!r}, "
                f"serial={self.serial}, MAC={self.mac}, FW={self.firmware}, "
                f"DHCP={self.dhcp}, IP={self.ip}/{self.netmask}, GW={self.gateway})")


@dataclass
class PortInfo:
    """Parsed from status.cgi."""
    port: int          # 1-based
    description: str   # user-settable (raw bytes; 0xFF = unset)
    enabled: bool      # True = Up, False = Down
    speed_cfg: PortSpeed   # configured speed
    speed_act: str     # actual link speed string, e.g. '1000M', 'No Speed'
    fc_enabled: bool   # flow control enabled
    max_mtu: int

    def __str__(self):
        s = f"Port {self.port:2d}: {'UP  ' if self.enabled else 'DOWN'}"
        s += f"  link={self.speed_act:<9s}"
        s += f"  cfg={self.speed_cfg}"
        if self.fc_enabled:
            s += '  FC=on'
        return s


@dataclass
class PortStats:
    """Parsed from portStatistics.cgi."""
    port: int
    bytes_rx: int
    bytes_tx: int
    crc_errors: int

    def __str__(self):
        return (f"Port {self.port:2d}: "
                f"RX={self.bytes_rx:>12d}B  "
                f"TX={self.bytes_tx:>12d}B  "
                f"CRC={self.crc_errors}")


@dataclass
class PortRateLimit:
    """Parsed from rateLimit.cgi."""
    port: int
    ingress: RateLimit
    egress: RateLimit

    def __str__(self):
        return (f"Port {self.port:2d}: "
                f"ingress={self.ingress}  egress={self.egress}")


@dataclass
class MirrorConfig:
    """Parsed from mirror.cgi."""
    enabled: bool
    dest_port: int         # 1-based; 0 = not set
    source_ports: List[int]  # 1-based port numbers

    def __str__(self):
        return (f"Mirror(enabled={self.enabled}, dest={self.dest_port}, "
                f"src={self.source_ports})")


@dataclass
class IGMPConfig:
    """Parsed from igmp.cgi."""
    enabled: bool
    vlan_id: str           # VLAN ID for IGMP snooping ('' = all VLANs)
    validate_ip_header: bool
    block_unknown_multicast: bool
    static_router_port: str   # '1'..'5' or 'any' or '' (none)

    def __str__(self):
        return (f"IGMP(enabled={self.enabled}, vlan={self.vlan_id!r}, "
                f"router_port={self.static_router_port!r})")


@dataclass
class Dot1QVlan:
    """One 802.1Q VLAN entry parsed from 8021qMembe.cgi."""
    vid: int
    # Port membership: list of (port, tagged) tuples.  Populated separately.
    members: List[Tuple[int, bool]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

def _get_hash(html: str) -> str:
    """Extract CSRF hash token from page HTML."""
    m = re.search(r"name='hash'\s+id='hash'\s+value=['\"](\w+)['\"]", html)
    return m.group(1) if m else ''


def _hidden_values(row_html: str) -> List[str]:
    """Return all <input type="hidden" value="..."> values from a chunk of HTML."""
    return re.findall(r'<input\s[^>]*type=["\']?hidden["\']?[^>]*\bvalue=["\']([^"\']*)["\']',
                      row_html, re.IGNORECASE)


def _td_texts(row_html: str) -> List[str]:
    """Return visible text content of all <td> cells in a chunk of HTML."""
    cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row_html, re.DOTALL | re.IGNORECASE)
    return [re.sub(r'<[^>]+>', '', c).strip() for c in cells]


def _selected_option(html: str, select_name: str) -> str:
    """Return the value of the selected <option> in a named <select>."""
    m = re.search(
        rf'<select\b[^>]*\bname=["\']?{re.escape(select_name)}["\']?[^>]*>(.*?)</select>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return ''
    sel_html = m.group(1)
    # Find the <option> tag that contains 'selected' (attribute order doesn't matter)
    for opt_m in re.finditer(r'<option\b([^>]*)>', sel_html, re.IGNORECASE):
        attrs = opt_m.group(1)
        if re.search(r'\bselected\b', attrs, re.IGNORECASE):
            val = re.search(r'\bvalue=["\']([^"\']*)["\']', attrs, re.IGNORECASE)
            if val:
                return val.group(1)
    # Fallback: first option
    first = re.search(r'<option\b[^>]*\bvalue=["\']([^"\']*)["\']', sel_html, re.IGNORECASE)
    return first.group(1) if first else ''


def _checked_radio(html: str, name: str) -> str:
    """Return the value of the checked radio button with the given name."""
    m = re.search(
        rf'<input\b[^>]*\btype=["\']?radio["\']?[^>]*\bname=["\']?{re.escape(name)}["\']?[^>]*\bchecked\b[^>]*\bvalue=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        # Try alternate attribute order: value before checked
        m = re.search(
            rf'<input\b[^>]*\btype=["\']?radio["\']?[^>]*\bvalue=["\']([^"\']*)["\']?[^>]*\bname=["\']?{re.escape(name)}["\']?[^>]*\bchecked\b',
            html, re.IGNORECASE,
        )
    return m.group(1) if m else ''


def _port_rows(html: str) -> List[str]:
    """Split table HTML into per-port row chunks (rows with class 'portID')."""
    return re.findall(r'<tr\b[^>]*\bclass=["\']portID["\'][^>]*>(.*?)(?=<tr\b|$)',
                      html, re.DOTALL | re.IGNORECASE)


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------

def _merge(s1: str, s2: str) -> str:
    """Interleave two strings character by character (Netgear's merge() JS)."""
    result = []
    i1, i2 = 0, 0
    while i1 < len(s1) or i2 < len(s2):
        if i1 < len(s1):
            result.append(s1[i1]); i1 += 1
        if i2 < len(s2):
            result.append(s2[i2]); i2 += 1
    return ''.join(result)


def _hash_password(password: str, rand: str) -> str:
    """Return MD5(merge(password, rand)) as a lowercase hex string."""
    return hashlib.md5(_merge(password, rand).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Main Switch class
# ---------------------------------------------------------------------------

class Switch:
    """
    Represents a Netgear GS105Ev2 (or compatible) managed switch.

    Use as a context manager for automatic login/logout::

        with Switch('192.168.0.1', password='secret') as sw:
            print(sw.get_system_info())

    Or manage the session manually::

        sw = Switch('192.168.0.1', password='secret')
        sw.login()
        ...
        sw.logout()

    NOTE: The switch enforces a single-session limit.  Always call logout()
    (or use the context manager) to avoid locking out subsequent logins.
    """

    def __init__(
        self,
        host: str,
        password: str = 'password',
        timeout: float = 10.0,
    ):
        self.host = host
        self.password = password
        self.timeout = timeout
        self._session = requests.Session()
        # GS305E and newer models check the Referer header to distinguish
        # in-application requests from direct browser navigation.  Setting it
        # permanently on the session satisfies both old (GS105Ev2) and new
        # (GS305E) firmware, and also ensures logout.cgi works correctly.
        self._session.headers.update({'Referer': self._url('index.cgi')})
        self._logged_in = False
        self._login_time: float = 0.0
        # Re-auth 30s before the ~5-minute inactivity timeout
        self._session_ttl: float = 270.0
        self._port_count: int = 5   # updated at login
        self._model: str = ''       # set by make_switch()
        self._firmware: str = ''    # set by make_switch()

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f'http://{self.host}/{path.lstrip("/")}'

    @staticmethod
    def _is_login_page(text: str) -> bool:
        """Return True if the response is the login page (session expired/reset)."""
        return 'RedirectToLoginPage' in text

    def _get(self, path: str, **kwargs) -> requests.Response:
        """GET a page, re-authenticating if the session was reset."""
        self._ensure_session()
        r = self._session.get(self._url(path), timeout=self.timeout, **kwargs)
        r.raise_for_status()
        if self._is_login_page(r.text):
            self._logged_in = False
            self.login()
            r = self._session.get(self._url(path), timeout=self.timeout, **kwargs)
            r.raise_for_status()
        return r

    def _post(self, path: str, data, **kwargs) -> requests.Response:
        """POST to a page.  Handles session expiry and re-auth."""
        self._ensure_session()
        try:
            r = self._session.post(self._url(path), data=data, timeout=self.timeout, **kwargs)
        except requests.exceptions.ConnectionError:
            # Some writes cause the switch to drop the TCP connection.
            self._logged_in = False
            return None
        r.raise_for_status()
        if self._is_login_page(r.text):
            self._logged_in = False
            self.login()
            r = self._session.post(self._url(path), data=data, timeout=self.timeout, **kwargs)
            r.raise_for_status()
        return r

    def _ensure_session(self):
        if not self._logged_in or (time.time() - self._login_time > self._session_ttl):
            self.login()

    def _page(self, name: str) -> str:
        """Fetch <name>.cgi and return HTML."""
        return self._get(f'{name}.cgi').text

    def _page_and_hash(self, name: str) -> Tuple[str, str]:
        """Fetch <name>.cgi and return (html, csrf_hash)."""
        html = self._page(name)
        return html, _get_hash(html)

    def _keepalive(self):
        """Ping getstatus.cgi to keep the session alive without reading a full page."""
        try:
            self._session.get(self._url('getstatus.cgi'), timeout=self.timeout)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def login(self):
        """Authenticate with the switch."""
        r = self._session.get(self._url('login.cgi'), timeout=self.timeout)
        r.raise_for_status()

        # rand is in a custom 'rand:' HTTP response header AND a hidden form field
        rand = r.headers.get('rand') or ''
        if not rand:
            m = re.search(r"id='rand'\s+value='(\d+)'", r.text)
            if not m:
                raise RuntimeError('Could not find rand value on login page')
            rand = m.group(1)

        hashed = _hash_password(self.password, rand)
        r2 = self._session.post(
            self._url('login.cgi'),
            data={'password': hashed},
            timeout=self.timeout,
        )
        r2.raise_for_status()

        # Detect login failure.
        # GS105Ev2: failed POST returns a redirect page containing 'RedirectToLoginPage'.
        # GS305E: failed POST returns the login form page which has 'login.css'.
        # A successful POST for either model returns a short redirect-to-index script.
        if self._is_login_page(r2.text) or 'login.css' in r2.text:
            err_m = re.search(r'id="pwdErr"[^>]*>(.*?)</div>', r2.text, re.DOTALL)
            msg = err_m.group(1).strip() if err_m else 'Unknown error'
            raise RuntimeError(f'Login failed: {msg}')

        # The SID cookie value contains RFC 6265-invalid characters ('[', '\', '`')
        # that Python's http.cookiejar silently drops.  Extract from raw header.
        set_cookie = r2.headers.get('Set-Cookie', '')
        sid_m = re.match(r'SID=([^;]+)', set_cookie)
        if sid_m:
            self._session.cookies.set('SID', sid_m.group(1),
                                      domain=self.host, path='/')

        self._logged_in = True
        self._login_time = time.time()

        # Cache port count from the port status page
        try:
            html = self._session.get(self._url('status.cgi'), timeout=self.timeout).text
            rows = _port_rows(html)
            if rows:
                self._port_count = len(rows)
        except Exception:
            pass

    def logout(self):
        """Log out of the switch (frees the single session slot)."""
        if self._logged_in:
            try:
                self._session.get(self._url('logout.cgi'), timeout=self.timeout)
            except Exception:
                pass
        self._session.cookies.clear()
        self._logged_in = False

    def __enter__(self) -> 'Switch':
        if not self._logged_in:
            self.login()
        return self

    def __exit__(self, *_):
        self.logout()

    @property
    def model(self) -> str:
        """Model string (e.g. 'GS305E'), or '' if not yet detected."""
        return self._model

    @property
    def firmware(self) -> str:
        """Firmware version string (e.g. 'V2.6.0.48'), or '' if not yet detected."""
        return self._firmware

    # ==================================================================
    # System information
    # ==================================================================

    def get_system_info(self) -> SystemInfo:
        """Return read-only system snapshot from info.cgi."""
        html = self._page('info')
        # Data is in <td> cells: MAC, IP, Netmask, Gateway, Firmware Version
        cells = re.findall(r'class="font11[^"]*"[^>]*>\s*([^<]+?)\s*</td>', html)
        # Expected pairs: label, value, label, value, ...
        # Row order: MAC Address, IP Address, Netmask, Gateway, Firmware Version
        pairs: Dict[str, str] = {}
        for i in range(0, len(cells) - 1, 2):
            pairs[cells[i].strip()] = cells[i + 1].strip()
        return SystemInfo(
            mac=pairs.get('MAC Address', ''),
            ip=pairs.get('IP Address', ''),
            netmask=pairs.get('Netmask', ''),
            gateway=pairs.get('Gateway', ''),
            firmware=pairs.get('Firmware Version', ''),
        )

    def get_switch_config(self) -> SwitchConfig:
        """Return full switch configuration (name, IP, DHCP) from switch_info.cgi."""
        html = self._page('switch_info')

        def _val(name: str) -> str:
            m = re.search(rf'<input\b[^>]*\bname=["\']?{re.escape(name)}["\']?[^>]*\bvalue=["\']([^"\']*)["\']',
                          html, re.IGNORECASE)
            return m.group(1) if m else ''

        def _td_after(label: str) -> str:
            m = re.search(rf'<td[^>]*>{re.escape(label)}</td>\s*<td[^>]*nowrap>\s*([^<\s][^<]*?)\s*</td>',
                          html, re.IGNORECASE)
            return m.group(1).strip() if m else ''

        dhcp_selected = _selected_option(html, 'dhcpMode')
        return SwitchConfig(
            model=_td_after('Product Name'),
            name=_val('switch_name'),
            serial=_td_after('Serial Number'),
            mac=_td_after('MAC Address'),
            firmware=_td_after('Firmware Version'),
            dhcp=(dhcp_selected == '1'),
            ip=_val('ip_address'),
            netmask=_val('subnet_mask'),
            gateway=_val('gateway_address'),
        )

    def set_switch_name(self, name: str):
        """Set the switch's user-visible name (up to 20 chars)."""
        html, h = self._page_and_hash('switch_info')
        cfg = self.get_switch_config()
        self._post('switch_info.cgi', {
            'switch_name':    name,
            'dhcpMode':       '1' if cfg.dhcp else '0',
            'ip_address':     cfg.ip,
            'subnet_mask':    cfg.netmask,
            'gateway_address': cfg.gateway,
            'hash':           h,
        })

    def set_ip_settings(
        self,
        ip: Optional[str] = None,
        netmask: Optional[str] = None,
        gateway: Optional[str] = None,
        dhcp: Optional[bool] = None,
    ):
        """Change IP configuration.  Unspecified parameters retain current values."""
        html, h = self._page_and_hash('switch_info')
        cfg = self.get_switch_config()
        use_dhcp = dhcp if dhcp is not None else cfg.dhcp
        self._post('switch_info.cgi', {
            'switch_name':    cfg.name,
            'dhcpMode':       '1' if use_dhcp else '0',
            'ip_address':     ip      or cfg.ip,
            'subnet_mask':    netmask or cfg.netmask,
            'gateway_address': gateway or cfg.gateway,
            'hash':           h,
        })

    def change_password(self, old_password: str, new_password: str):
        """Change the admin password."""
        _, h = self._page_and_hash('user')
        self._post('user.cgi', {
            'oldPassword':  old_password,
            'newPassword':  new_password,
            'reNewPassword': new_password,
            'hash':         h,
        })

    # ==================================================================
    # Port status and settings
    # ==================================================================

    def get_port_settings(self) -> List[PortInfo]:
        """Return per-port settings from status.cgi."""
        html = self._page('status')
        rows = _port_rows(html)
        result = []
        for i, row in enumerate(rows, start=1):
            # Hidden inputs per port:
            #   [0] port number, [1] port status (Up/Down), [2] speed_cfg value,
            #   [3] linked speed string, [4] flow control value, [5] MTU
            hidden = _hidden_values(row)
            # Visible text cells (after stripping tags):
            cells = _td_texts(row)
            # cells: [port_num, description, status_text, speed_cfg_text,
            #         linked_speed, fc_text, mtu_text]

            def _hval(idx: int, default='') -> str:
                return hidden[idx] if idx < len(hidden) else default

            # Port status: hidden contains 'Up' or 'Down'
            status_str = _hval(1, 'Down')
            enabled = (status_str.strip().lower() == 'up')

            # Speed config: hidden value is 1-6
            try:
                speed_cfg = PortSpeed(int(_hval(2, '1')))
            except (ValueError, KeyError):
                speed_cfg = PortSpeed.AUTO

            # Linked speed: text string e.g. '1000M', 'No Speed'
            speed_act = _hval(3, 'No Speed')

            # Flow control: 1=Enable, 2=Disable
            fc_val = _hval(4, '2')
            fc_enabled = (fc_val == '1')

            # MTU
            try:
                mtu = int(_hval(5, '0'))
            except ValueError:
                mtu = 0

            # Description: visible cell text, may be garbled if unset
            desc = cells[2] if len(cells) > 2 else ''

            result.append(PortInfo(
                port=i,
                description=desc,
                enabled=enabled,
                speed_cfg=speed_cfg,
                speed_act=speed_act,
                fc_enabled=fc_enabled,
                max_mtu=mtu,
            ))
        return result

    def set_port(
        self,
        port: int,
        speed: Optional[PortSpeed] = None,
        fc_enabled: Optional[bool] = None,
        description: Optional[str] = None,
    ):
        """
        Set configuration for one port.

        Fetches current settings first; only the specified parameters are changed.
        """
        html, h = self._page_and_hash('status')
        current = self.get_port_settings()
        p = current[port - 1]

        use_speed = speed if speed is not None else p.speed_cfg
        use_fc    = fc_enabled if fc_enabled is not None else p.fc_enabled
        use_desc  = description if description is not None else ''

        # Build per-port POST data.  Only port 'port' is modified.
        data: List[Tuple[str, str]] = []
        for pi in current:
            if pi.port == port:
                data.append((f'port{pi.port}', 'checked'))
                data.append(('DESCRIPTION', use_desc))
                data.append(('SPEED', str(int(use_speed))))
                data.append(('FLOW_CONTROL', '1' if use_fc else '2'))
        data.append(('hash', h))
        self._post('status.cgi', data)

    # ==================================================================
    # Port statistics
    # ==================================================================

    def get_port_stats(self) -> List[PortStats]:
        """Return per-port byte and error counters from portStatistics.cgi."""
        html = self._page('portStatistics')
        rows = _port_rows(html)
        result = []
        for i, row in enumerate(rows, start=1):
            # Each counter is encoded as two hidden inputs (high word, low word).
            # Order: bytes_rx_high, bytes_rx_low, bytes_tx_high, bytes_tx_low,
            #        crc_high, crc_low
            hidden = _hidden_values(row)

            def _64bit(high_idx: int) -> int:
                try:
                    hi = int(hidden[high_idx])
                    lo = int(hidden[high_idx + 1])
                    return (hi << 32) | lo
                except (IndexError, ValueError):
                    return 0

            result.append(PortStats(
                port=i,
                bytes_rx=_64bit(0),
                bytes_tx=_64bit(2),
                crc_errors=_64bit(4),
            ))
        return result

    def clear_port_stats(self):
        """Reset all port counters to zero."""
        _, h = self._page_and_hash('portStatistics')
        self._post('portStatistics.cgi', {'clearCounters': '1', 'hash': h})

    # ==================================================================
    # Rate limiting
    # ==================================================================

    def get_rate_limits(self) -> List[PortRateLimit]:
        """Return ingress/egress rate limits for each port."""
        html = self._page('rateLimit')
        rows = _port_rows(html)
        result = []
        for i, row in enumerate(rows, start=1):
            hidden = _hidden_values(row)
            # Hidden: [port_num, ingress_rate_value, egress_rate_value]
            try:
                ingress = RateLimit(int(hidden[1])) if len(hidden) > 1 else RateLimit.NO_LIMIT
                egress  = RateLimit(int(hidden[2])) if len(hidden) > 2 else RateLimit.NO_LIMIT
            except (ValueError, KeyError):
                ingress = egress = RateLimit.NO_LIMIT
            result.append(PortRateLimit(port=i, ingress=ingress, egress=egress))
        return result

    def set_rate_limit(self, port: int, ingress: RateLimit, egress: RateLimit):
        """Set ingress and egress rate limits for one port."""
        html, h = self._page_and_hash('rateLimit')
        data: List[Tuple[str, str]] = [
            (f'port{port}', 'checked'),
            ('IngressRate', str(int(ingress))),
            ('EgressRate',  str(int(egress))),
            ('hash', h),
        ]
        self._post('rateLimit.cgi', data)

    # ==================================================================
    # Port mirroring
    # ==================================================================

    def get_mirror_config(self) -> MirrorConfig:
        """Return port mirroring configuration."""
        html = self._page('mirror')
        enabled_val = _selected_option(html, 'mirroring')
        # mirroring: 0=Enable, 1=Disable (reversed from what you'd expect)
        enabled = (enabled_val == '0')
        dest_val = _selected_option(html, 'select')
        try:
            dest_port = int(dest_val)
        except (ValueError, TypeError):
            dest_port = 0

        # Source ports are encoded in hiddenMem: one char per port
        # '0' = not mirrored, '1' = mirrored
        m = re.search(r'id="hiddenMem"[^>]*value="([^"]*)"', html, re.IGNORECASE)
        hidden_mem = m.group(1) if m else '00000'
        source_ports = [i + 1 for i, c in enumerate(hidden_mem) if c == '1']

        return MirrorConfig(enabled=enabled, dest_port=dest_port, source_ports=source_ports)

    def set_mirror_config(
        self,
        enabled: bool,
        dest_port: int,
        source_ports: List[int],
    ):
        """Configure port mirroring."""
        _, h = self._page_and_hash('mirror')
        mem = ['0'] * self._port_count
        for p in source_ports:
            if 1 <= p <= self._port_count:
                mem[p - 1] = '1'
        self._post('mirror.cgi', {
            'mirroring': '0' if enabled else '1',
            'select':    str(dest_port),
            'hiddenMem': ''.join(mem),
            'hash':      h,
        })

    # ==================================================================
    # IGMP snooping
    # ==================================================================

    def get_igmp_config(self) -> IGMPConfig:
        """Return IGMP snooping configuration."""
        html = self._page('igmp')
        # status: 0=Disable, 1=Enable (radio buttons)
        status_val = _checked_radio(html, 'status')
        enabled = (status_val == '1')

        # VLAN ID enabled (text input)
        m = re.search(r'name="VLAN_ID_ENABLED"[^>]*value="([^"]*)"', html, re.IGNORECASE)
        vlan_id = m.group(1) if m else ''

        ip_header_val = _checked_radio(html, 'IP_HEADER')
        block_mc_val  = _checked_radio(html, 'BLOCK_UN_MUL_ADDR')
        router_port   = _selected_option(html, 'ROUTER_PORT')

        return IGMPConfig(
            enabled=enabled,
            vlan_id=vlan_id,
            validate_ip_header=(ip_header_val == '1'),
            block_unknown_multicast=(block_mc_val == '1'),
            static_router_port=router_port,
        )

    def set_igmp_config(
        self,
        enabled: bool,
        vlan_id: str = '',
        validate_ip_header: bool = False,
        block_unknown_multicast: bool = False,
        static_router_port: str = '0',
    ):
        """Configure IGMP snooping."""
        _, h = self._page_and_hash('igmp')
        self._post('igmp.cgi', {
            'status':             '1' if enabled else '0',
            'VLAN_ID_ENABLED':    vlan_id,
            'IP_HEADER':          '1' if validate_ip_header else '0',
            'BLOCK_UN_MUL_ADDR':  '1' if block_unknown_multicast else '0',
            'ROUTER_PORT':        static_router_port,
            'hash':               h,
        })

    # ==================================================================
    # Loop detection
    # ==================================================================

    def get_loop_detection(self) -> bool:
        """Return True if loop detection is enabled."""
        html = self._page('loop_detection')
        val = _checked_radio(html, 'loopDetection')
        return val == '1'

    def set_loop_detection(self, enabled: bool):
        """Enable or disable loop detection."""
        _, h = self._page_and_hash('loop_detection')
        self._post('loop_detection.cgi', {
            'loopDetection': '1' if enabled else '0',
            'hash': h,
        })

    # ==================================================================
    # Broadcast filtering
    # ==================================================================

    def get_broadcast_filter(self) -> bool:
        """Return True if broadcast filtering is enabled."""
        html = self._page('broadCastFilter')
        val = _checked_radio(html, 'status')
        return val == 'Enable'

    def set_broadcast_filter(self, enabled: bool):
        """Enable or disable broadcast filtering."""
        _, h = self._page_and_hash('broadCastFilter')
        self._post('broadCastFilter.cgi', {
            'status': 'Enable' if enabled else 'Disable',
            'hash': h,
        })

    # ==================================================================
    # QoS mode
    # ==================================================================

    def get_qos_mode(self) -> str:
        """Return 'port-based' or '802.1p/dscp'."""
        html = self._page('qos')
        val = _checked_radio(html, 'status')
        # 'Disable' = Port-Based, 'Enable' = 802.1p/DSCP
        return '802.1p/dscp' if val == 'Enable' else 'port-based'

    def set_qos_mode(self, mode: str):
        """Set QoS mode: 'port-based' or '802.1p/dscp'."""
        _, h = self._page_and_hash('qos')
        self._post('qos.cgi', {
            'status': 'Enable' if mode == '802.1p/dscp' else 'Disable',
            'hash': h,
        })

    # ==================================================================
    # Power saving (Green Ethernet)
    # ==================================================================

    def get_power_saving(self) -> bool:
        """Return True if power saving mode (Green Ethernet) is enabled."""
        html = self._page('green_ethernet')
        val = _checked_radio(html, 'powerSavingMode')
        return val == '1'

    def set_power_saving(self, enabled: bool):
        """
        Enable or disable power saving mode.

        NOTE: On the GS105Ev2 (fw V1.6.0.24) this write does not persist —
        the switch accepts the POST and echoes the new value in the response,
        but a subsequent GET always returns the original hardware-controlled
        state.  The method is provided for completeness and in case other
        firmware versions behave differently.
        """
        _, h = self._page_and_hash('green_ethernet')
        self._post('green_ethernet.cgi', {
            'powerSavingMode': '1' if enabled else '0',
            'hash': h,
        })

    # ==================================================================
    # 802.1Q VLAN
    # ==================================================================

    def get_dot1q_enabled(self) -> bool:
        """Return True if Advanced 802.1Q VLAN mode is enabled."""
        html = self._page('8021qCf')
        val = _checked_radio(html, 'status')
        return val == 'Enable'

    def set_dot1q_enabled(self, enabled: bool):
        """Enable or disable Advanced 802.1Q VLAN mode."""
        _, h = self._page_and_hash('8021qCf')
        self._post('8021qCf.cgi', {
            'status': 'Enable' if enabled else 'Disable',
            'ACTION': '',
            'hash': h,
        })

    def get_vlan_ids(self) -> List[int]:
        """Return list of configured 802.1Q VLAN IDs from 8021qMembe.cgi."""
        html = self._page('8021qMembe')
        m = re.search(r'<select\b[^>]*\bname=["\']?VLAN_ID["\']?[^>]*>(.*?)</select>',
                      html, re.DOTALL | re.IGNORECASE)
        if not m:
            return []
        options = re.findall(r'<option\b[^>]*\bvalue=["\']?(\d+)["\']?', m.group(1), re.IGNORECASE)
        return [int(v) for v in options]

    def get_vlan_membership(self, vid: int) -> str:
        """
        Return the port membership string for VLAN <vid>.

        The returned string is one character per port:
          '1' = untagged member, '2' = tagged member, '3' = not a member.
        Example: '11322' — ports 1,2 untagged; port 3 not a member; ports 4,5 tagged.

        NOTE: The server ignores the VLAN_ID query parameter on GET requests.
        Membership is loaded by POSTing just the VLAN_ID, which causes the server
        to return the page with that VLAN's hiddenMem populated.
        """
        # POST with VLAN_ID (simulating the VLAN dropdown change in the browser).
        # The browser's pageSubmitForm() does form1.submit() which includes all enabled
        # form fields including the current hiddenMem; the server uses VLAN_ID to look
        # up the requested VLAN and returns its membership in the response's hiddenMem.
        # A plain GET to this CGI always returns VLAN 1's data regardless of VLAN_ID.
        page_html, h = self._page_and_hash('8021qMembe')
        cur_mem_m = re.search(r'id="hiddenMem"[^>]*value="([^"]*)"', page_html, re.IGNORECASE)
        cur_mem = cur_mem_m.group(1) if cur_mem_m else '3' * self._port_count
        r = self._session.post(
            self._url('8021qMembe.cgi'),
            data={'VLAN_ID': str(vid), 'hiddenMem': cur_mem, 'hash': h},
            timeout=self.timeout,
        )
        html = r.text if r else ''
        m = re.search(r'id="hiddenMem"[^>]*value="([^"]*)"', html, re.IGNORECASE)
        return m.group(1) if m else ''

    def set_vlan_membership(self, vid: int, membership: str):
        """
        Set port membership for VLAN <vid>.

        <membership> is one char per port:
          '1' = untagged member, '2' = tagged member, '3' = not a member.
        Example for 5 ports: '11322' — ports 1,2 untagged; port 3 excluded; ports 4,5 tagged.

        The firmware requires a two-step POST sequence:
          1. POST with the target VLAN_ID (simulating the dropdown selection) — the
             server responds with the current membership for that VLAN.
          2. POST with the same VLAN_ID and the desired hiddenMem — the server applies
             the new membership.
        Skipping step 1 causes the server to ignore the membership change.
        """
        # Normalise any '0' to '3' (the firmware's encoding for not-a-member)
        membership = membership.replace('0', '3')

        # Step 1: GET to obtain the current page state and hash
        page_html, h = self._page_and_hash('8021qMembe')
        cur_m = re.search(r'id="hiddenMem"[^>]*value="([^"]*)"', page_html, re.IGNORECASE)
        cur_mem = cur_m.group(1) if cur_m else '3' * self._port_count

        # Step 2: POST to "select" the target VLAN (mirroring the dropdown onchange)
        r_sel = self._session.post(
            self._url('8021qMembe.cgi'),
            data={'VLAN_ID': str(vid), 'hiddenMem': cur_mem, 'hash': h},
            timeout=self.timeout,
        )
        h_sel_m = re.search(r"name='hash'[^>]*value=['\"](\d+)['\"]",
                             r_sel.text if r_sel else '', re.IGNORECASE)
        h2 = h_sel_m.group(1) if h_sel_m else h

        # Step 3: POST the new membership (mirroring the Apply button click)
        self._post('8021qMembe.cgi', {
            'VLAN_ID':   str(vid),
            'hiddenMem': membership,
            'hash':      h2,
        })

    def add_vlan(self, vid: int):
        """Create a new 802.1Q VLAN (must enable 802.1Q mode first)."""
        html, h = self._page_and_hash('8021qCf')
        # Include the full form payload — the firmware ignores partial POSTs.
        # ACTION value must match the JS submitForm("Add") call exactly (capital A).
        mgmt_m = re.search(r'name="MANAGEMENT_VLAN_ID"[^>]*value="(\d+)"', html)
        mgmt_vid = mgmt_m.group(1) if mgmt_m else '1'
        vnum_m = re.search(r"name='vlanNum'\s+value='(\d+)'", html)
        vlan_num = vnum_m.group(1) if vnum_m else '1'
        self._post('8021qCf.cgi', {
            'status':             'Enable',
            'ADD_VLANID':         str(vid),
            'vlanNum':            vlan_num,
            'MANAGEMENT_VLAN_ID': mgmt_vid,
            'ACTION':             'Add',
            'hash':               h,
        })

    def delete_vlan(self, vid: int):
        """Delete an 802.1Q VLAN."""
        html, h = self._page_and_hash('8021qCf')
        # Find the checkbox index for this VID in the vlanck<n>=<vid> list.
        # ACTION value must match the JS submitForm("Delete") call (capital D).
        pairs = re.findall(r'name="(vlanck\d+)"[^>]*value="(\d+)"', html, re.IGNORECASE)
        ck_name = next((name for name, val in pairs if int(val) == vid), None)
        if ck_name is None:
            return  # VLAN not found — nothing to delete
        mgmt_m = re.search(r'name="MANAGEMENT_VLAN_ID"[^>]*value="(\d+)"', html)
        mgmt_vid = mgmt_m.group(1) if mgmt_m else '1'
        vnum_m = re.search(r"name='vlanNum'\s+value='(\d+)'", html)
        vlan_num = vnum_m.group(1) if vnum_m else '1'
        self._post('8021qCf.cgi', {
            'status':             'Enable',
            ck_name:              str(vid),
            'vlanNum':            vlan_num,
            'MANAGEMENT_VLAN_ID': mgmt_vid,
            'ACTION':             'Delete',
            'hash':               h,
        })

    # ==================================================================
    # Port PVID
    # ==================================================================

    def get_port_pvids(self) -> Dict[int, int]:
        """Return {port: pvid} mapping from portPVID.cgi."""
        html = self._page('portPVID')
        rows = _port_rows(html)
        result = {}
        for i, row in enumerate(rows, start=1):
            hidden = _hidden_values(row)
            cells = _td_texts(row)
            # cells[0] = empty (checkbox), cells[1] = port number, cells[2] = pvid value
            try:
                pvid = int(cells[2]) if len(cells) > 2 else 1
            except ValueError:
                pvid = 1
            result[i] = pvid
        return result

    def set_port_pvid(self, port: int, pvid: int):
        """Set the PVID for one port (1–4094)."""
        _, h = self._page_and_hash('portPVID')
        data = [
            (f'port{port - 1}', 'checked'),   # 0-indexed checkbox names in portPVID.cgi
            ('pvid', str(pvid)),
            ('hash', h),
        ]
        self._post('portPVID.cgi', data)

    # ==================================================================
    # Cable tester
    # ==================================================================

    def test_cable(self, ports: List[int]) -> str:
        """
        Run cable diagnostics on the specified ports.

        Returns the raw HTML response — the results table needs to be parsed
        for test result and fault distance per port.
        NOTE: Cable testing disrupts traffic briefly on tested ports.
        """
        _, h = self._page_and_hash('cableTester')
        data = [(f'port{p - 1}', 'checked') for p in ports]
        data.append(('hash', h))
        r = self._post('cableTester.cgi', data)
        return r.text if r else ''

    # ==================================================================
    # Reboot / factory reset
    # ==================================================================

    def reboot(self):
        """Reboot the switch.  The session will be invalidated."""
        self._ensure_session()
        try:
            self._session.post(
                self._url('device_reboot.cgi'),
                data={'reboot': '1'},
                timeout=self.timeout,
            )
        except Exception:
            pass
        self._logged_in = False

    def factory_reset(self):
        """
        Reset to factory defaults.
        WARNING: Erases all configuration and resets IP to default.
        """
        self._ensure_session()
        try:
            self._session.post(
                self._url('factory_default.cgi'),
                data={'reset': '1'},
                timeout=self.timeout,
            )
        except Exception:
            pass
        self._logged_in = False


# ===========================================================================
# Model registry and auto-detection factory
# ===========================================================================

# Maps model-name prefixes (case-insensitive) to port count.
# Add entries as new models are confirmed.
_MODEL_PORT_COUNT: Dict[str, int] = {
    'GS305E':   5,
    'GS305EP':  5,
    'GS308E':   8,
    'GS308EP':  8,
    'GS105E':   5,   # covers GS105Ev2
    'GS108E':   8,   # covers GS108Ev3
    'GS108PE':  8,
    'GS116E':   16,  # covers GS116Ev2
    'GS110MX':  10,
    'GS752TP':  52,
}


def _port_count_from_model(model: Optional[str]) -> Optional[int]:
    """Return port count for *model*, or None if the model is not in the registry."""
    if not model:
        return None
    m = model.upper()
    for prefix, count in _MODEL_PORT_COUNT.items():
        if m.startswith(prefix.upper()):
            return count
    return None


def make_switch(
    host: str,
    password: str = 'password',
    timeout: float = 10.0,
) -> Switch:
    """
    Connect to a Netgear Smart Managed Plus switch and return a connected Switch.

    Logs in, then reads ``switch_info.cgi`` to detect the model, firmware
    version, and port count.  The returned object is already logged in::

        with make_switch('192.168.0.1', password='secret') as sw:
            print(sw.model, sw.firmware)
            for p in sw.get_port_settings():
                print(p)

    If the model is not in the known registry a RuntimeWarning is emitted, but
    the Switch is still returned — unknown models are often compatible.

    Raises RuntimeError if the host is unreachable or authentication fails.
    """
    sw = Switch(host, password=password, timeout=timeout)
    sw.login()
    try:
        cfg = sw.get_switch_config()
        sw._model = cfg.model
        sw._firmware = cfg.firmware
        pc = _port_count_from_model(cfg.model)
        if pc is not None:
            sw._port_count = pc
        if cfg.model and not any(
            cfg.model.upper().startswith(k.upper()) for k in _MODEL_PORT_COUNT
        ):
            warnings.warn(
                f'Unknown Netgear switch model {cfg.model!r}. '
                'Behaviour may be incorrect for unsupported models. '
                'Please report at https://github.com/jfrancis42/ansible-netgear/issues.',
                RuntimeWarning,
                stacklevel=2,
            )
    except Exception:
        pass
    return sw
