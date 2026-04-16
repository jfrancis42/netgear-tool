#!/usr/bin/env python3
"""
Smoke test: exercise all read operations against the live switch.
"""
import sys
sys.path.insert(0, '/home/jfrancis/netgear')

from netgear_switch import Switch

HOST     = '192.168.0.1'
PASSWORD = 'secret'

def section(title):
    print(f'\n{"="*60}')
    print(f'  {title}')
    print(f'{"="*60}')

with Switch(HOST, password=PASSWORD) as sw:
    section('System Info (info.cgi)')
    info = sw.get_system_info()
    print(info)

    section('Switch Config (switch_info.cgi)')
    cfg = sw.get_switch_config()
    print(cfg)

    section('Port Settings (status.cgi)')
    for p in sw.get_port_settings():
        print(p)

    section('Port Stats (portStatistics.cgi)')
    for s in sw.get_port_stats():
        print(s)

    section('Rate Limits (rateLimit.cgi)')
    for r in sw.get_rate_limits():
        print(r)

    section('Mirror Config (mirror.cgi)')
    print(sw.get_mirror_config())

    section('IGMP Config (igmp.cgi)')
    print(sw.get_igmp_config())

    section('Loop Detection (loop_detection.cgi)')
    print('Loop detection enabled:', sw.get_loop_detection())

    section('Broadcast Filter (broadCastFilter.cgi)')
    print('Broadcast filter enabled:', sw.get_broadcast_filter())

    section('QoS Mode (qos.cgi)')
    print('QoS mode:', sw.get_qos_mode())

    section('Power Saving (green_ethernet.cgi)')
    print('Power saving enabled:', sw.get_power_saving())

    section('802.1Q VLAN (8021qCf.cgi + 8021qMembe.cgi)')
    print('802.1Q enabled:', sw.get_dot1q_enabled())
    vids = sw.get_vlan_ids()
    print('VLANs:', vids)
    for vid in vids:
        mem = sw.get_vlan_membership(vid)
        print(f'  VLAN {vid}: membership={mem!r}')

    section('Port PVIDs (portPVID.cgi)')
    pvids = sw.get_port_pvids()
    for port, pvid in sorted(pvids.items()):
        print(f'  Port {port}: PVID={pvid}')

print('\nAll reads completed successfully.')
