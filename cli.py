#!/usr/bin/env python3
"""
Netgear GS105Ev2 Switch CLI

A Cisco IOS-inspired shell for Netgear Smart Managed Plus switches.
Modal design: exec → configure → interface / vlan sub-modes.

Usage:
    python3 cli.py <host> [--password PASSWORD]
    python3 cli.py 192.168.0.1 --password secret
"""

import cmd
import sys
import os
import re
import argparse
import getpass
import requests

sys.path.insert(0, os.path.dirname(__file__))
from netgear_switch import Switch, PortSpeed, RateLimit

# ---------------------------------------------------------------------------
# Terminal colours
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty()

def _c(code, s):
    return f'\033[{code}m{s}\033[0m' if _USE_COLOR else s

def green(s):  return _c('32', s)
def red(s):    return _c('31', s)
def yellow(s): return _c('33', s)
def cyan(s):   return _c('36', s)
def bold(s):   return _c('1',  s)
def dim(s):    return _c('2',  s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PORT_COUNT = 5

def _parse_ports(spec):
    """
    Parse a port specification into a sorted list of 1-based port numbers.
    Accepts: '1', '1,3,5', '1-4', '1-3,5', 'gi1', 'gi1-3'.
    Returns [] on parse error.
    """
    spec = spec.strip().lower().lstrip('gi').lstrip('port').strip()
    ports = set()
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            try:
                lo, hi = part.split('-', 1)
                ports.update(range(int(lo), int(hi) + 1))
            except ValueError:
                return []
        elif part.isdigit():
            ports.add(int(part))
        elif part:
            return []
    return sorted(ports)


def _port_range_str(ports):
    """Compress [1,2,3,5,6] → 'gi1-3,gi5-6'."""
    if not ports:
        return '-'
    out, start, prev = [], ports[0], ports[0]
    for p in ports[1:]:
        if p == prev + 1:
            prev = p
        else:
            out.append(f'gi{start}' if start == prev else f'gi{start}-{prev}')
            start = prev = p
    out.append(f'gi{start}' if start == prev else f'gi{start}-{prev}')
    return ','.join(out)


# Rate-limit label map (RateLimit → user-facing string and back)
_RATE_LABELS = {
    RateLimit.NO_LIMIT: 'no-limit',
    RateLimit.K512:     '512k',
    RateLimit.M1:       '1m',
    RateLimit.M2:       '2m',
    RateLimit.M4:       '4m',
    RateLimit.M8:       '8m',
    RateLimit.M16:      '16m',
    RateLimit.M32:      '32m',
    RateLimit.M64:      '64m',
    RateLimit.M128:     '128m',
    RateLimit.M256:     '256m',
    RateLimit.M512:     '512m',
}
_RATE_BY_LABEL = {v: k for k, v in _RATE_LABELS.items()}


def _parse_rate(s):
    """
    Parse a rate-limit string → RateLimit enum value.
    Accepts label ('no-limit', '512k', '1m' … '512m') or integer index 1-12.
    Returns None on error.
    """
    s = s.lower().strip()
    if s in _RATE_BY_LABEL:
        return _RATE_BY_LABEL[s]
    try:
        return RateLimit(int(s))
    except (ValueError, KeyError):
        return None


def _rate_str(r):
    return _RATE_LABELS.get(r, str(r))


# ---------------------------------------------------------------------------
# Main CLI class
# ---------------------------------------------------------------------------

class SwitchCLI(cmd.Cmd):
    intro  = ''
    ruler  = '-'

    def __init__(self, sw: Switch, hostname: str):
        super().__init__()
        self.sw        = sw
        self._name     = hostname
        self._mode     = 'exec'
        self._if_ports = []   # list of port numbers being configured
        self._vlan_id  = None
        self._update_prompt()

    # ------------------------------------------------------------------
    # Mode / prompt management
    # ------------------------------------------------------------------

    def _update_prompt(self):
        n = self._name
        if self._mode == 'exec':
            self.prompt = bold(f'{n}# ')
        elif self._mode == 'config':
            self.prompt = bold(f'{n}(config)# ')
        elif self._mode == 'config-if':
            ps = _port_range_str(self._if_ports)
            self.prompt = bold(f'{n}(config-if-{ps})# ')
        elif self._mode == 'config-vlan':
            self.prompt = bold(f'{n}(config-vlan-{self._vlan_id})# ')

    def _enter(self, mode, **kw):
        self._mode = mode
        for k, v in kw.items():
            setattr(self, k, v)
        self._update_prompt()

    def _require(self, *modes):
        if self._mode not in modes:
            print(f'  % Command not available in {self._mode} mode')
            return False
        return True

    # ------------------------------------------------------------------
    # Abbreviation + 'no' / 'do' dispatch
    # ------------------------------------------------------------------

    def onecmd(self, line):
        line = line.strip()
        if not line:
            return
        try:
            if re.match(r'^no\b', line, re.IGNORECASE):
                return self._do_no(line[2:].strip())
            if re.match(r'^do\b', line, re.IGNORECASE):
                saved = self._mode
                self._mode = 'exec'
                result = self.onecmd(line[2:].strip())
                self._mode = saved
                self._update_prompt()
                return result
            return super().onecmd(line)
        except requests.exceptions.ConnectionError:
            print(f'\n  % Connection to {self.sw.host} lost.')
            return True
        except requests.exceptions.Timeout:
            print(f'\n  % Connection to {self.sw.host} timed out.')
            return True
        except requests.exceptions.HTTPError as e:
            print(f'\n  % HTTP error: {e}')
            return True
        except RuntimeError as e:
            print(f'\n  % Switch error: {e}')

    def default(self, line):
        cmd_word, args, _ = self.parseline(line)
        if not cmd_word:
            return
        names = sorted(n[3:] for n in self.get_names() if n.startswith('do_'))
        matches = [n for n in names if n.startswith(cmd_word)]
        if len(matches) == 1:
            return getattr(self, f'do_{matches[0]}')(args)
        if cmd_word in matches:
            return getattr(self, f'do_{cmd_word}')(args)
        if matches:
            print(f'  % Ambiguous: {", ".join(matches)}')
        else:
            print(f'  % Unknown command: {cmd_word!r}  (type ? for help)')

    def _do_no(self, args):
        """Dispatch 'no <command> [args]'."""
        cmd_word, rest, _ = self.parseline(args)
        if not cmd_word:
            print('  % Incomplete command')
            return
        handler = getattr(self, f'_no_{cmd_word}', None)
        if handler:
            return handler(rest)
        candidates = [n[4:] for n in dir(self) if n.startswith('_no_')]
        matches = [c for c in candidates if c.startswith(cmd_word)]
        if len(matches) == 1:
            return getattr(self, f'_no_{matches[0]}')(rest)
        if matches:
            print(f'  % Ambiguous no-form: {", ".join(matches)}')
        else:
            print(f'  % "no {cmd_word}" not supported')

    # ------------------------------------------------------------------
    # exit / end / quit
    # ------------------------------------------------------------------

    def do_exit(self, _):
        """Exit current mode (or disconnect if in exec)."""
        if self._mode in ('config-if', 'config-vlan'):
            self._enter('config')
        elif self._mode == 'config':
            self._enter('exec')
        else:
            return self._disconnect()

    def do_end(self, _):
        """Return directly to exec mode from any configuration mode."""
        self._enter('exec')

    def do_quit(self, _):
        """Disconnect from the switch."""
        return self._disconnect()

    def _disconnect(self):
        print('Bye.')
        return True

    def do_EOF(self, _):
        print()
        return self._disconnect()

    # ------------------------------------------------------------------
    # configure terminal
    # ------------------------------------------------------------------

    def do_configure(self, args):
        """configure terminal  — enter global configuration mode"""
        if not self._require('exec'):
            return
        sub = (args.split() or [''])[0]
        if not sub or 'terminal'.startswith(sub):
            self._enter('config')
        else:
            print('  Usage: configure terminal')

    def complete_configure(self, text, *_):
        return [s for s in ('terminal',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # interface
    # ------------------------------------------------------------------

    def do_interface(self, args):
        """interface gi<N>  |  interface range gi<N>-<M>  — configure port(s)"""
        if not self._require('config'):
            return
        args = args.strip()
        args = re.sub(r'^range\s+', '', args, flags=re.IGNORECASE)
        args = re.sub(r'^(port|gi)\s*', '', args, flags=re.IGNORECASE)
        ports = _parse_ports(args)
        if not ports:
            print('  Usage: interface gi<N>  or  interface range gi<N>-<M>')
            return
        invalid = [p for p in ports if p < 1 or p > _PORT_COUNT]
        if invalid:
            print(f'  % Invalid port(s): {invalid}  (valid: 1-{_PORT_COUNT})')
            return
        self._enter('config-if', _if_ports=ports)

    def complete_interface(self, text, *_):
        return [s for s in ('gi', 'range') if s.startswith(text)]

    # ------------------------------------------------------------------
    # vlan (config mode — create/enter VLAN sub-mode)
    # ------------------------------------------------------------------

    def do_vlan(self, args):
        """vlan <id>  — create/enter 802.1Q VLAN configuration sub-mode"""
        if not self._require('config'):
            return
        try:
            vid = int(args.strip())
            assert 1 <= vid <= 4094
        except (ValueError, AssertionError):
            print('  Usage: vlan <1-4094>')
            return
        if not self.sw.get_dot1q_enabled():
            print('  Enabling 802.1Q VLAN mode...')
            self.sw.set_dot1q_enabled(True)
        if vid not in self.sw.get_vlan_ids():
            self.sw.add_vlan(vid)
            print(f'  Created VLAN {vid}')
        self._enter('config-vlan', _vlan_id=vid)

    def _no_vlan(self, args):
        """no vlan <id>  — delete an 802.1Q VLAN"""
        if not self._require('config'):
            return
        try:
            vid = int(args.strip())
        except ValueError:
            print('  Usage: no vlan <id>')
            return
        self.sw.delete_vlan(vid)
        print(f'  Deleted VLAN {vid}')

    # ------------------------------------------------------------------
    # shutdown / no shutdown  (interface mode)
    # Note: Netgear disables a port by setting its speed to 'Disable'
    # ------------------------------------------------------------------

    def do_shutdown(self, _):
        """shutdown  — disable port(s)"""
        if not self._require('config-if'):
            return
        for port in self._if_ports:
            self.sw.set_port(port, speed=PortSpeed.DISABLE)
        print(f'  Port(s) {_port_range_str(self._if_ports)} disabled')

    def _no_shutdown(self, _):
        if not self._require('config-if'):
            return
        for port in self._if_ports:
            self.sw.set_port(port, speed=PortSpeed.AUTO)
        print(f'  Port(s) {_port_range_str(self._if_ports)} enabled (speed: auto)')

    # ------------------------------------------------------------------
    # speed  (interface mode)
    # GS105Ev2 supports Auto (negotiates 10/100/1000), 10H, 10F, 100H, 100F
    # ------------------------------------------------------------------

    _SPEED_MAP = {
        ('auto', ''):    PortSpeed.AUTO,
        ('10',   ''):    PortSpeed.M10F,
        ('10',   'full'):PortSpeed.M10F,
        ('10',   'half'):PortSpeed.M10H,
        ('100',  ''):    PortSpeed.M100F,
        ('100',  'full'):PortSpeed.M100F,
        ('100',  'half'):PortSpeed.M100H,
    }

    def do_speed(self, args):
        """speed {auto|10|100} [half|full]  — set port speed/duplex"""
        if not self._require('config-if'):
            return
        parts = args.lower().split()
        spd = parts[0] if parts else ''
        dup = parts[1] if len(parts) > 1 else ''
        ps = self._SPEED_MAP.get((spd, dup)) or self._SPEED_MAP.get((spd, ''))
        if ps is None:
            print('  Usage: speed {auto|10|100} [half|full]')
            print('  Note: auto negotiates up to 1 Gbit/s')
            return
        for port in self._if_ports:
            self.sw.set_port(port, speed=ps)
        print(f'  Speed set on {_port_range_str(self._if_ports)}: {ps}')

    def complete_speed(self, text, *_):
        return [s for s in ('auto', '10', '100') if s.startswith(text)]

    # ------------------------------------------------------------------
    # flowcontrol / no flowcontrol  (interface mode)
    # ------------------------------------------------------------------

    def do_flowcontrol(self, _):
        """flowcontrol  — enable flow control on port(s)"""
        if not self._require('config-if'):
            return
        for port in self._if_ports:
            self.sw.set_port(port, fc_enabled=True)
        print(f'  Flow control enabled on {_port_range_str(self._if_ports)}')

    def _no_flowcontrol(self, _):
        if not self._require('config-if'):
            return
        for port in self._if_ports:
            self.sw.set_port(port, fc_enabled=False)
        print(f'  Flow control disabled on {_port_range_str(self._if_ports)}')

    # ------------------------------------------------------------------
    # switchport  (interface mode)
    # ------------------------------------------------------------------

    def do_switchport(self, args):
        """
        switchport access vlan <id>               — untagged member + set PVID
        switchport trunk allowed vlan add <id>    — add as tagged member
        switchport trunk allowed vlan remove <id> — remove from VLAN
        switchport pvid <id>                      — set port PVID only
        """
        if not self._require('config-if'):
            return
        parts = args.split()
        if not parts:
            print('  Usage: switchport {access|trunk|pvid} ...')
            return
        sub = parts[0].lower()
        if sub == 'pvid':
            self._sw_pvid(parts[1:])
        elif sub == 'access':
            self._sw_access(parts[1:])
        elif sub == 'trunk':
            self._sw_trunk(parts[1:])
        else:
            print(f'  % Unknown switchport sub-command: {sub}')

    def complete_switchport(self, text, *_):
        return [s for s in ('access', 'trunk', 'pvid') if s.startswith(text)]

    def _ensure_dot1q(self):
        if not self.sw.get_dot1q_enabled():
            print('  Enabling 802.1Q VLAN mode...')
            self.sw.set_dot1q_enabled(True)

    def _vlan_mem_list(self, vid):
        """Return mutable list of membership chars for <vid>, padded to _PORT_COUNT."""
        mem = self.sw.get_vlan_membership(vid)
        return list(mem.ljust(_PORT_COUNT, '0'))

    def _sw_pvid(self, parts):
        if not parts or not parts[0].isdigit():
            print('  Usage: switchport pvid <vlan-id>')
            return
        vid = int(parts[0])
        for p in self._if_ports:
            self.sw.set_port_pvid(p, vid)
        print(f'  PVID → {vid} on {_port_range_str(self._if_ports)}')

    def _sw_access(self, parts):
        """switchport access vlan <id> — set port as untagged, update PVID."""
        if len(parts) < 2 or parts[0].lower() != 'vlan' or not parts[1].isdigit():
            print('  Usage: switchport access vlan <id>')
            return
        vid = int(parts[1])
        self._ensure_dot1q()
        if vid not in self.sw.get_vlan_ids():
            self.sw.add_vlan(vid)
        ml = self._vlan_mem_list(vid)
        for port in self._if_ports:
            if 1 <= port <= _PORT_COUNT:
                ml[port - 1] = '1'  # untagged
        self.sw.set_vlan_membership(vid, ''.join(ml))
        for port in self._if_ports:
            self.sw.set_port_pvid(port, vid)
        print(f'  {_port_range_str(self._if_ports)} → untagged on VLAN {vid}, PVID set')

    def _sw_trunk(self, parts):
        """switchport trunk allowed vlan {add|remove} <id>"""
        while parts and parts[0].lower() in ('allowed', 'vlan'):
            parts = parts[1:]
        if len(parts) < 2 or not parts[1].isdigit():
            print('  Usage: switchport trunk allowed vlan {add|remove} <id>')
            return
        action = parts[0].lower()
        vid    = int(parts[1])
        if action not in ('add', 'remove'):
            print('  Usage: ... allowed vlan {add|remove} <id>')
            return
        self._ensure_dot1q()
        if action == 'add':
            if vid not in self.sw.get_vlan_ids():
                self.sw.add_vlan(vid)
            ml = self._vlan_mem_list(vid)
            for port in self._if_ports:
                if 1 <= port <= _PORT_COUNT:
                    ml[port - 1] = '2'  # tagged
            self.sw.set_vlan_membership(vid, ''.join(ml))
            print(f'  {_port_range_str(self._if_ports)} → tagged on VLAN {vid}')
        else:
            ml = self._vlan_mem_list(vid)
            for port in self._if_ports:
                if 1 <= port <= _PORT_COUNT:
                    ml[port - 1] = '0'  # not member
            self.sw.set_vlan_membership(vid, ''.join(ml))
            print(f'  {_port_range_str(self._if_ports)} removed from VLAN {vid}')

    # ------------------------------------------------------------------
    # bandwidth  (interface mode)
    # Uses RateLimit index values, not raw kbps
    # ------------------------------------------------------------------

    def do_bandwidth(self, args):
        """
        bandwidth ingress {no-limit|512k|1m|2m|4m|8m|16m|32m|64m|128m|256m|512m}
        bandwidth egress  {no-limit|512k|1m|...}
        """
        if not self._require('config-if'):
            return
        parts = args.lower().split()
        if len(parts) < 2:
            print('  Usage: bandwidth {ingress|egress} '
                  '{no-limit|512k|1m|2m|4m|8m|16m|32m|64m|128m|256m|512m}')
            return
        direction, val = parts[0], parts[1]
        rate = _parse_rate(val)
        if rate is None:
            print(f'  % Invalid rate: {val!r}')
            print('  Valid: no-limit, 512k, 1m, 2m, 4m, 8m, 16m, 32m, 64m, 128m, 256m, 512m')
            return
        limits = {r.port: r for r in self.sw.get_rate_limits()}
        for port in self._if_ports:
            cur = limits.get(port)
            ingress = cur.ingress if cur else RateLimit.NO_LIMIT
            egress  = cur.egress  if cur else RateLimit.NO_LIMIT
            if direction.startswith('in'):
                ingress = rate
            elif direction.startswith('eg'):
                egress = rate
            else:
                print('  % Direction must be ingress or egress')
                return
            self.sw.set_rate_limit(port, ingress, egress)
        dir_str = 'ingress' if direction.startswith('in') else 'egress'
        print(f'  {dir_str.capitalize()} rate on {_port_range_str(self._if_ports)}: '
              f'{_rate_str(rate)}')

    def _no_bandwidth(self, args):
        """no bandwidth [ingress|egress]  — remove rate limit(s)"""
        if not self._require('config-if'):
            return
        parts = args.lower().split()
        limits = {r.port: r for r in self.sw.get_rate_limits()}
        for port in self._if_ports:
            cur = limits.get(port)
            ingress = cur.ingress if cur else RateLimit.NO_LIMIT
            egress  = cur.egress  if cur else RateLimit.NO_LIMIT
            if not parts:
                ingress = egress = RateLimit.NO_LIMIT
            elif parts[0].startswith('in'):
                ingress = RateLimit.NO_LIMIT
            elif parts[0].startswith('eg'):
                egress = RateLimit.NO_LIMIT
            self.sw.set_rate_limit(port, ingress, egress)
        print(f'  Rate limits cleared on {_port_range_str(self._if_ports)}')

    def complete_bandwidth(self, text, *_):
        return [s for s in ('ingress', 'egress') if s.startswith(text)]

    # ------------------------------------------------------------------
    # System config commands (config mode)
    # ------------------------------------------------------------------

    def do_hostname(self, args):
        """hostname <name>  — set switch name (up to 20 chars)"""
        if not self._require('config'):
            return
        name = args.strip()
        if not name:
            print('  Usage: hostname <name>')
            return
        self.sw.set_switch_name(name)
        self._name = name
        self._update_prompt()

    def do_ip(self, args):
        """
        ip address <A.B.C.D> <mask> [<gateway>]  — set static IP
        ip address dhcp                           — enable DHCP
        """
        if not self._require('config'):
            return
        parts = args.split()
        if parts and parts[0].lower() == 'address':
            parts = parts[1:]
        if not parts:
            print('  Usage: ip address {<ip> <mask> [<gw>] | dhcp}')
            return
        if parts[0].lower() == 'dhcp':
            self.sw.set_ip_settings(dhcp=True)
            print('  DHCP enabled')
        elif len(parts) >= 2:
            gw = parts[2] if len(parts) >= 3 else None
            self.sw.set_ip_settings(ip=parts[0], netmask=parts[1],
                                    gateway=gw, dhcp=False)
            suffix = f' gw {gw}' if gw else ''
            print(f'  IP set to {parts[0]} / {parts[1]}{suffix}')
        else:
            print('  Usage: ip address <ip> <mask> [<gw>]  or  ip address dhcp')

    def _no_ip(self, args):
        """no ip address dhcp  — disable DHCP (retain current static params)"""
        if 'dhcp' in args.lower():
            cfg = self.sw.get_switch_config()
            self.sw.set_ip_settings(ip=cfg.ip, netmask=cfg.netmask,
                                    gateway=cfg.gateway, dhcp=False)
            print('  DHCP disabled')
        else:
            print('  Usage: no ip address dhcp')

    # ------------------------------------------------------------------
    # loop-detection  (config mode)
    # ------------------------------------------------------------------

    def do_loop_detection(self, _):
        """loop-detection  — enable loop detection"""
        if not self._require('config'):
            return
        self.sw.set_loop_detection(True)
        print('  Loop detection enabled')

    def _no_loop_detection(self, _):
        if not self._require('config'):
            return
        self.sw.set_loop_detection(False)
        print('  Loop detection disabled')

    # ------------------------------------------------------------------
    # broadcast-filter  (config mode)
    # ------------------------------------------------------------------

    def do_broadcast_filter(self, _):
        """broadcast-filter  — enable broadcast storm filtering"""
        if not self._require('config'):
            return
        self.sw.set_broadcast_filter(True)
        print('  Broadcast filter enabled')

    def _no_broadcast_filter(self, _):
        if not self._require('config'):
            return
        self.sw.set_broadcast_filter(False)
        print('  Broadcast filter disabled')

    # ------------------------------------------------------------------
    # igmp snooping  (config mode)
    # ------------------------------------------------------------------

    def do_igmp(self, args):
        """
        igmp snooping                       — enable IGMP snooping
        igmp snooping vlan <id>             — restrict to VLAN
        igmp snooping router-port <1-5>     — static router port
        igmp snooping validate-ip           — validate IP header
        igmp snooping block-unknown         — block unknown multicast
        """
        if not self._require('config'):
            return
        parts = args.lower().split()
        if not parts or parts[0] != 'snooping':
            print('  Usage: igmp snooping [vlan <id>] [router-port <N>] '
                  '[validate-ip] [block-unknown]')
            return
        cur = self.sw.get_igmp_config()
        vlan_id       = cur.vlan_id
        router_port   = cur.static_router_port
        validate_ip   = cur.validate_ip_header
        block_unknown = cur.block_unknown_multicast

        opts = parts[1:]
        i = 0
        while i < len(opts):
            o = opts[i]
            if o == 'vlan' and i + 1 < len(opts):
                vlan_id = opts[i + 1]; i += 2
            elif o in ('router-port', 'router') and i + 1 < len(opts):
                router_port = opts[i + 1]; i += 2
            elif o in ('validate-ip', 'validate'):
                validate_ip = True; i += 1
            elif o in ('block-unknown', 'block'):
                block_unknown = True; i += 1
            else:
                i += 1

        self.sw.set_igmp_config(
            enabled=True,
            vlan_id=vlan_id,
            validate_ip_header=validate_ip,
            block_unknown_multicast=block_unknown,
            static_router_port=router_port,
        )
        print('  IGMP snooping enabled')

    def _no_igmp(self, args):
        if not self._require('config'):
            return
        self.sw.set_igmp_config(enabled=False)
        print('  IGMP snooping disabled')

    # ------------------------------------------------------------------
    # qos mode  (config mode)
    # ------------------------------------------------------------------

    def do_qos(self, args):
        """qos mode {port-based|802.1p}  — set global QoS mode"""
        if not self._require('config'):
            return
        parts = args.lower().split()
        if not parts or parts[0] != 'mode' or len(parts) < 2:
            print('  Usage: qos mode {port-based|802.1p}')
            return
        m = parts[1]
        if m in ('port-based', 'port'):
            self.sw.set_qos_mode('port-based')
            print('  QoS mode: port-based')
        elif m in ('802.1p', 'dot1p', '802.1p/dscp', 'dscp'):
            self.sw.set_qos_mode('802.1p/dscp')
            print('  QoS mode: 802.1p/DSCP')
        else:
            print('  % Unknown QoS mode; use port-based or 802.1p')

    def complete_qos(self, text, *_):
        return [s for s in ('mode',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # monitor session  (config mode — port mirroring)
    # GS105Ev2 has a single source bitmask (rx and tx are not distinguished)
    # ------------------------------------------------------------------

    def do_monitor(self, args):
        """
        monitor session 1 destination interface gi<N>
        monitor session 1 source interface gi<N>[,<M>]
        no monitor session 1  — disable mirroring
        """
        if not self._require('config'):
            return
        parts = args.lower().split()
        if len(parts) >= 2 and parts[0] == 'session':
            parts = parts[2:]   # discard 'session N'
        if not parts:
            print('  Usage: monitor session 1 {source|destination} interface gi<N>')
            return
        sub = parts[0]

        if sub.startswith('dest'):
            iface_parts = [p for p in parts[1:] if p != 'interface']
            ports = _parse_ports(' '.join(iface_parts))
            if len(ports) != 1:
                print('  Usage: monitor session 1 destination interface gi<N>')
                return
            m = self.sw.get_mirror_config()
            self.sw.set_mirror_config(
                enabled=True,
                dest_port=ports[0],
                source_ports=m.source_ports,
            )
            print(f'  Mirror destination: gi{ports[0]}')

        elif sub.startswith('src') or sub.startswith('sou'):
            # Strip 'interface' keyword and direction hints (not supported by hw)
            iface_parts = [p for p in parts[1:]
                           if p not in ('interface', 'rx', 'tx', 'both')]
            ports = _parse_ports(','.join(iface_parts))
            if not ports:
                print('  Usage: monitor session 1 source interface gi<N>[,<M>]')
                return
            m = self.sw.get_mirror_config()
            src = sorted(set(m.source_ports) | set(ports))
            self.sw.set_mirror_config(
                enabled=True,
                dest_port=m.dest_port or 1,
                source_ports=src,
            )
            print(f'  Mirror source(s) {_port_range_str(ports)}: set')
            print(f'  Note: GS105Ev2 mirrors all traffic (rx+tx) — direction is ignored')
        else:
            print(f'  % Unknown monitor sub-command: {sub}')

    def _no_monitor(self, args):
        """no monitor session 1  — disable port mirroring"""
        if not self._require('config'):
            return
        self.sw.set_mirror_config(enabled=False, dest_port=1, source_ports=[])
        print('  Port mirroring disabled')

    def complete_monitor(self, text, *_):
        return [s for s in ('session',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # username  (config mode)
    # ------------------------------------------------------------------

    def do_username(self, args):
        """username admin password <old-pw> <new-pw>  — change admin password"""
        if not self._require('config'):
            return
        parts = args.split()
        if parts and parts[0].lower() == 'admin':
            parts = parts[1:]
        if parts and parts[0].lower() == 'password':
            parts = parts[1:]
        if len(parts) < 2:
            print('  Usage: username admin password <old-password> <new-password>')
            return
        self.sw.change_password(parts[0], parts[1])
        print('  Password changed.')

    # ------------------------------------------------------------------
    # reload
    # ------------------------------------------------------------------

    def do_reload(self, _):
        """reload  — reboot the switch"""
        if not self._require('exec'):
            return
        ans = input('  Proceed with reload? [y/N] ').strip().lower()
        if ans == 'y':
            self.sw.reboot()
            print('  Reloading...')
            return True
        print('  Reload cancelled')

    # ------------------------------------------------------------------
    # clear counters  (exec mode)
    # ------------------------------------------------------------------

    def do_clear(self, args):
        """clear counters  — reset all port statistics"""
        if not self._require('exec'):
            return
        parts = args.lower().split()
        if not parts or not parts[0].startswith('count'):
            print('  Usage: clear counters')
            return
        self.sw.clear_port_stats()
        print('  All port counters cleared')

    def complete_clear(self, text, *_):
        return [s for s in ('counters',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # test cable-diagnostics  (exec mode)
    # ------------------------------------------------------------------

    def do_test(self, args):
        """test cable-diagnostics [interface gi<N>[,<M>]]  — run cable test"""
        if not self._require('exec'):
            return
        parts = args.lower().split()
        iface_parts = [p for p in parts
                       if p not in ('cable-diagnostics', 'cable-diag',
                                    'tdr', 'interface', 'cable')]
        ports = _parse_ports(','.join(iface_parts)) if iface_parts else list(range(1, _PORT_COUNT + 1))
        if not ports:
            ports = list(range(1, _PORT_COUNT + 1))
        print('  Running cable diagnostics...')
        html = self.sw.test_cable(ports)
        _show_cable_results(html)

    def complete_test(self, text, *_):
        return [s for s in ('cable-diagnostics',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # write erase  (exec mode — factory reset)
    # ------------------------------------------------------------------

    def do_write(self, args):
        """write erase  — factory reset the switch"""
        if not self._require('exec'):
            return
        if not args.strip().lower().startswith('er'):
            print('  Usage: write erase')
            return
        ans = input('  Factory reset? ALL configuration will be lost. [y/N] ').strip().lower()
        if ans == 'y':
            self.sw.factory_reset()
            print('  Factory reset initiated. Switch is rebooting...')
            return True
        print('  Cancelled')

    def complete_write(self, text, *_):
        return [s for s in ('erase',) if s.startswith(text)]

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------

    def do_show(self, args):
        """
        show version
        show interfaces [brief | gi<N> | counters]
        show vlan
        show ip
        show running-config
        show qos
        show loop-detection
        show broadcast-filter
        show igmp
        show port-mirror
        """
        parts = args.split() if args else []
        if not parts:
            print('  Usage: show <subcommand>  (type "show ?" for list)')
            return
        sub = parts[0].lower()
        SUBS = {
            'version':          lambda: self._show_version(),
            'interfaces':       lambda: self._show_interfaces(parts[1:]),
            'vlan':             lambda: self._show_vlan(),
            'ip':               lambda: self._show_ip(),
            'running-config':   lambda: self._show_running_config(),
            'qos':              lambda: self._show_qos(),
            'loop-detection':   lambda: self._show_loop_detection(),
            'broadcast-filter': lambda: self._show_broadcast_filter(),
            'igmp':             lambda: self._show_igmp(),
            'port-mirror':      lambda: self._show_port_mirror(),
        }
        matches = [k for k in SUBS if k.startswith(sub)]
        if len(matches) == 1:
            SUBS[matches[0]]()
        elif sub in SUBS:
            SUBS[sub]()
        elif matches:
            print(f'  % Ambiguous: {", ".join(sorted(matches))}')
        elif sub == '?':
            print('  Available: ' + ', '.join(sorted(SUBS)))
        else:
            print(f'  % Unknown: show {sub}')

    def complete_show(self, text, *_):
        subs = ['version', 'interfaces', 'vlan', 'ip', 'running-config',
                'qos', 'loop-detection', 'broadcast-filter', 'igmp', 'port-mirror']
        return [s for s in subs if s.startswith(text)]

    # ---- show version ----

    def _show_version(self):
        info = self.sw.get_system_info()
        cfg  = self.sw.get_switch_config()
        print(f'\n  {bold(cfg.name or cfg.model)}')
        print(f'  Model    : {cfg.model}')
        print(f'  Serial   : {cfg.serial}')
        print(f'  Firmware : {cfg.firmware}')
        print(f'  MAC      : {cfg.mac}')
        dhcp_str = 'DHCP' if cfg.dhcp else 'static'
        print(f'  IP       : {cfg.ip} / {cfg.netmask}  ({dhcp_str})')
        print(f'  Gateway  : {cfg.gateway}\n')

    # ---- show interfaces ----

    def _show_interfaces(self, args):
        sub   = args[0].lower() if args else 'brief'
        ports = self.sw.get_port_settings()

        if sub == 'counters':
            stats = self.sw.get_port_stats()
            smap  = {s.port: s for s in stats}
            print(f'\n  {"Port":<6}  {"RX Bytes":>16}  {"TX Bytes":>16}  {"CRC Errors":>12}')
            print(f'  {"------":<6}  {"-"*16:>16}  {"-"*16:>16}  {"-"*12:>12}')
            for p in ports:
                s = smap.get(p.port)
                print(f'  gi{p.port:<4}  {(s.bytes_rx if s else 0):>16,}  '
                      f'{(s.bytes_tx if s else 0):>16,}  '
                      f'{(s.crc_errors if s else 0):>12,}')
            print()
            return

        # Filter to a specific port if requested
        if sub not in ('brief',):
            pn = _parse_ports(sub)
            if pn:
                ports = [p for p in ports if p.port in pn]

        print(f'\n  {"Port":<6}  {"Status":<12}  {"Link":<12}  {"Config":<12}  FC')
        print('  ' + '-' * 52)
        for p in ports:
            if p.speed_cfg == PortSpeed.DISABLE:
                status = red('disabled  ')
            elif p.enabled and p.speed_act and p.speed_act.lower() not in ('no speed', ''):
                status = green('up        ')
            else:
                status = dim('down      ')
            link = p.speed_act if p.speed_act and p.speed_act.lower() != 'no speed' else dim('--')
            cfg  = str(p.speed_cfg)
            fc   = 'on' if p.fc_enabled else 'off'
            print(f'  gi{p.port:<4}  {status:<12}  {link:<12}  {cfg:<12}  {fc}')
        print()

    # ---- show vlan ----

    def _show_vlan(self):
        enabled = self.sw.get_dot1q_enabled()
        if not enabled:
            print(f'\n  802.1Q VLAN: {red("disabled")}\n')
            return
        vids  = self.sw.get_vlan_ids()
        pvids = self.sw.get_port_pvids()
        print(f'\n  802.1Q VLAN: {green("enabled")}\n')
        print(f'  {"VLAN":<6}  {"Untagged Ports":<22}  Tagged Ports')
        print(f'  {"----":<6}  {"----------------------":<22}  ------------')
        for vid in vids:
            mem = self.sw.get_vlan_membership(vid).ljust(_PORT_COUNT, '0')
            untagged = [i + 1 for i, c in enumerate(mem) if c == '1']
            tagged   = [i + 1 for i, c in enumerate(mem) if c == '2']
            print(f'  {vid:<6}  {_port_range_str(untagged):<22}  {_port_range_str(tagged)}')
        print()
        pvid_row = '  '.join(f'gi{p}:{v}' for p, v in sorted(pvids.items()))
        print(f'  Port PVIDs: {pvid_row}\n')

    # ---- show ip ----

    def _show_ip(self):
        cfg = self.sw.get_switch_config()
        print(f'\n  IP Address : {cfg.ip}')
        print(f'  Subnet Mask: {cfg.netmask}')
        print(f'  Gateway    : {cfg.gateway}')
        print(f'  DHCP       : {"enabled" if cfg.dhcp else "disabled"}\n')

    # ---- show running-config ----

    def _show_running_config(self):
        cfg   = self.sw.get_switch_config()
        ports = self.sw.get_port_settings()
        loop  = self.sw.get_loop_detection()
        bcast = self.sw.get_broadcast_filter()
        igmp  = self.sw.get_igmp_config()
        qos   = self.sw.get_qos_mode()
        q_en  = self.sw.get_dot1q_enabled()
        rates = {r.port: r for r in self.sw.get_rate_limits()}
        pvids = self.sw.get_port_pvids() if q_en else {}

        # Build per-port tagged/untagged VLAN membership maps
        port_tagged   = {}   # port -> sorted list of tagged VIDs
        port_untagged = {}   # port -> sorted list of untagged VIDs
        vlan_ids = []
        if q_en:
            vlan_ids = self.sw.get_vlan_ids()
            for vid in vlan_ids:
                mem = self.sw.get_vlan_membership(vid)
                for i, c in enumerate(mem):
                    pnum = i + 1
                    if c == '1':
                        port_untagged.setdefault(pnum, []).append(vid)
                    elif c == '2':
                        port_tagged.setdefault(pnum, []).append(vid)

        def L(s=''):
            print(s)

        L('!')
        L(f'hostname {cfg.name}')
        L('!')
        if cfg.dhcp:
            L('ip address dhcp')
        else:
            L(f'ip address {cfg.ip} {cfg.netmask} {cfg.gateway}')
        L('!')
        if loop:   L('loop-detection')
        if bcast:  L('broadcast-filter')
        if igmp.enabled:
            s = 'igmp snooping'
            if igmp.vlan_id:                                    s += f' vlan {igmp.vlan_id}'
            if igmp.static_router_port and igmp.static_router_port != '0':
                s += f' router-port {igmp.static_router_port}'
            if igmp.validate_ip_header:                         s += ' validate-ip'
            if igmp.block_unknown_multicast:                    s += ' block-unknown'
            L(s)
        L(f'qos mode {qos}')
        L('!')
        if q_en:
            for vid in vlan_ids:
                L(f'vlan {vid}')
            L('!')
        for p in ports:
            L(f'interface gi{p.port}')
            if p.speed_cfg == PortSpeed.DISABLE:
                L(' shutdown')
            elif p.speed_cfg != PortSpeed.AUTO:
                L(f' speed {p.speed_cfg}')
            if p.fc_enabled:
                L(' flowcontrol')
            r = rates.get(p.port)
            if r and r.ingress != RateLimit.NO_LIMIT:
                L(f' bandwidth ingress {_rate_str(r.ingress)}')
            if r and r.egress != RateLimit.NO_LIMIT:
                L(f' bandwidth egress {_rate_str(r.egress)}')
            if q_en:
                tagged   = sorted(port_tagged.get(p.port, []))
                untagged = sorted(port_untagged.get(p.port, []))
                if untagged:
                    # Access port: has an untagged VLAN.  Ignore tagged memberships —
                    # the firmware forces every port to remain in VLAN 1 as tagged,
                    # so tagged entries here are a firmware artefact, not user intent.
                    L(' switchport mode access')
                    L(f' switchport access vlan {untagged[0]}')
                elif tagged:
                    # Trunk port: no untagged VLAN, only tagged VLANs
                    L(' switchport mode trunk')
                    L(f' switchport trunk allowed vlan {",".join(str(v) for v in tagged)}')
                    L(f' switchport trunk native vlan {pvids.get(p.port, 1)}')
            L('!')
        L('end')

    # ---- show qos ----

    def _show_qos(self):
        mode  = self.sw.get_qos_mode()
        rates = self.sw.get_rate_limits()
        print(f'\n  QoS mode: {bold(mode)}\n')
        print(f'  {"Port":<6}  {"Ingress":<14}  Egress')
        print(f'  {"------":<6}  {"-"*14:<14}  {"-"*14}')
        for r in rates:
            print(f'  gi{r.port:<4}  {_rate_str(r.ingress):<14}  {_rate_str(r.egress)}')
        print()

    # ---- show loop-detection ----

    def _show_loop_detection(self):
        en = self.sw.get_loop_detection()
        s  = green('enabled') if en else red('disabled')
        print(f'\n  Loop detection: {s}\n')

    # ---- show broadcast-filter ----

    def _show_broadcast_filter(self):
        en = self.sw.get_broadcast_filter()
        s  = green('enabled') if en else red('disabled')
        print(f'\n  Broadcast filter: {s}\n')

    # ---- show igmp ----

    def _show_igmp(self):
        c = self.sw.get_igmp_config()
        print(f'\n  IGMP snooping    : {green("enabled") if c.enabled else red("disabled")}')
        if c.enabled:
            print(f'  VLAN             : {c.vlan_id or "(all)"}')
            rp = c.static_router_port
            print(f'  Router port      : {f"gi{rp}" if rp and rp != "0" else "(none)"}')
            print(f'  Validate IP hdr  : {"yes" if c.validate_ip_header else "no"}')
            print(f'  Block unknown MC : {"yes" if c.block_unknown_multicast else "no"}')
        print()

    # ---- show port-mirror ----

    def _show_port_mirror(self):
        m = self.sw.get_mirror_config()
        s = green('enabled') if m.enabled else red('disabled')
        print(f'\n  Port mirroring: {s}')
        if m.enabled:
            print(f'  Destination   : gi{m.dest_port}')
            print(f'  Source ports  : {_port_range_str(m.source_ports)}')
            print(f'  Direction     : all (rx+tx, hardware limitation)')
        print()

    # ------------------------------------------------------------------
    # help
    # ------------------------------------------------------------------

    def do_help(self, arg):
        MODE_HELP = {
            'exec': [
                ('show version',                          'System info and firmware'),
                ('show interfaces [brief|gi<N>|counters]','Port status / byte counters'),
                ('show vlan',                             '802.1Q VLAN status'),
                ('show ip',                               'IP address configuration'),
                ('show running-config',                   'Full configuration listing'),
                ('show qos',                              'QoS mode and rate limits'),
                ('show loop-detection',                   'Loop detection state'),
                ('show broadcast-filter',                 'Broadcast filter state'),
                ('show igmp',                             'IGMP snooping configuration'),
                ('show port-mirror',                      'Port mirroring configuration'),
                ('clear counters',                        'Reset port statistics'),
                ('test cable-diagnostics [gi<N>]',        'Run cable diagnostics'),
                ('configure terminal',                    'Enter configuration mode'),
                ('reload',                                'Reboot the switch'),
                ('write erase',                           'Factory reset'),
                ('exit / quit',                           'Disconnect'),
            ],
            'config': [
                ('interface gi<N>',                       'Configure a port'),
                ('interface range gi<N>-<M>',             'Configure multiple ports'),
                ('vlan <id>',                             'Create/enter 802.1Q VLAN'),
                ('no vlan <id>',                          'Delete an 802.1Q VLAN'),
                ('hostname <name>',                       'Set switch name'),
                ('ip address <ip> <mask> [<gw>]',        'Set static IP'),
                ('ip address dhcp',                       'Enable DHCP'),
                ('no ip address dhcp',                    'Disable DHCP'),
                ('[no] loop-detection',                   'Loop detection on/off'),
                ('[no] broadcast-filter',                 'Broadcast filter on/off'),
                ('[no] igmp snooping',                    'IGMP snooping on/off'),
                ('igmp snooping vlan <id>',               'Restrict IGMP to VLAN'),
                ('igmp snooping router-port <N>',         'Static router port'),
                ('igmp snooping validate-ip',             'Validate IP header'),
                ('igmp snooping block-unknown',           'Block unknown multicast'),
                ('qos mode {port-based|802.1p}',         'Set global QoS mode'),
                ('monitor session 1 destination gi<N>',  'Set mirror destination'),
                ('monitor session 1 source gi<N>',       'Set mirror source(s)'),
                ('no monitor session 1',                  'Disable port mirroring'),
                ('username admin password <old> <new>',  'Change admin password'),
                ('do show ...',                           'Run a show command'),
                ('end',                                   'Return to exec mode'),
            ],
            'config-if': [
                ('[no] shutdown',                         'Enable / disable port'),
                ('speed {auto|10|100} [half|full]',      'Speed and duplex'),
                ('[no] flowcontrol',                      'Flow control on/off'),
                ('switchport access vlan <id>',           'Set untagged VLAN + PVID'),
                ('switchport trunk allowed vlan add <id>','Add tagged VLAN'),
                ('switchport trunk allowed vlan remove <id>', 'Remove from VLAN'),
                ('switchport pvid <id>',                  'Port VLAN ID'),
                ('bandwidth ingress <rate>',              'Ingress rate limit'),
                ('bandwidth egress <rate>',               'Egress rate limit'),
                ('no bandwidth [ingress|egress]',         'Remove rate limit(s)'),
                ('do show ...',                           'Run a show command'),
                ('exit',                                  'Back to config mode'),
                ('end',                                   'Back to exec mode'),
            ],
            'config-vlan': [
                ('do show vlan',  'Show VLAN table'),
                ('exit',          'Back to config mode'),
                ('end',           'Back to exec mode'),
            ],
        }
        cmds = MODE_HELP.get(self._mode, [])
        w = max(len(c) for c, _ in cmds) + 2
        print(f'\n  Commands available in {bold(self._mode)} mode:\n')
        for cmd_str, desc in cmds:
            print(f'    {cmd_str:<{w}}  {dim(desc)}')
        print()
        print('  Rate values: no-limit  512k  1m  2m  4m  8m  16m  32m  64m  128m  256m  512m')
        print('  Abbreviations are supported (e.g. "conf t", "sh int", "sh ver").')
        print()


# ---------------------------------------------------------------------------
# Cable test result parser (module-level, used by do_test)
# ---------------------------------------------------------------------------

def _show_cable_results(html):
    rows = re.findall(
        r'<tr\b[^>]*class=["\']portID["\'][^>]*>(.*?)</tr>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not rows:
        print('  (No cable test results parsed from response)')
        return
    print(f'\n  {"Port":<6}  {"Status":<22}  Length')
    print(f'  {"------":<6}  {"----------------------":<22}  ------')
    for row in rows:
        cells = re.findall(r'<td[^>]*>\s*([^<]*?)\s*</td>', row)
        port   = cells[0].strip() if len(cells) > 0 else '?'
        status = cells[1].strip() if len(cells) > 1 else '?'
        length = cells[2].strip() if len(cells) > 2 else '--'
        print(f'  gi{port:<4}  {status:<22}  {length}')
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='Netgear Smart Managed Plus Switch CLI')
    ap.add_argument('host',                     help='Switch IP address')
    ap.add_argument('-p', '--password', default=None, help='Password (prompted if omitted)')
    args = ap.parse_args()

    password = args.password or getpass.getpass(f'Password for {args.host}: ')

    print(f'Connecting to {args.host}...', end=' ', flush=True)
    try:
        sw = Switch(args.host, password=password)
        sw.login()
        cfg = sw.get_switch_config()
        hostname = re.sub(r'[^A-Za-z0-9_\-]', '-', cfg.name).strip('-') if cfg.name else ''
        hostname = hostname or cfg.model or 'switch'
        print(green('OK'))
        print(f'  {cfg.model}  |  FW: {cfg.firmware}  |  IP: {cfg.ip}')
        print()
    except Exception as e:
        print(red('FAILED'))
        print(f'  {e}')
        sys.exit(1)

    cli = SwitchCLI(sw, hostname)
    print("Type ? for help.  Type 'exit' to disconnect.\n")

    try:
        cli.cmdloop()
    except KeyboardInterrupt:
        print()
    finally:
        try:
            sw.logout()
        except Exception:
            pass


if __name__ == '__main__':
    main()
