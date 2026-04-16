#!/usr/bin/env python3
"""
Write verification tests against the live switch.
Tests safe operations: loop detection, power saving, rate limits.
Skips anything that would affect port 1 (our management connection).
"""
import sys, time
sys.path.insert(0, '/home/jfrancis/netgear')

from netgear_switch import Switch, RateLimit

HOST     = '192.168.0.1'
PASSWORD = 'secret'

def test(desc, fn):
    print(f'  {desc}...', end='', flush=True)
    fn()
    print(' OK')

with Switch(HOST, password=PASSWORD) as sw:
    # ----------------------------------------------------------------
    # Loop detection toggle
    # ----------------------------------------------------------------
    print('\n--- Loop Detection ---')
    orig = sw.get_loop_detection()
    print(f'  Original: {orig}')

    test('Enable', lambda: sw.set_loop_detection(True))
    assert sw.get_loop_detection() == True, 'expected True'

    test('Disable', lambda: sw.set_loop_detection(False))
    assert sw.get_loop_detection() == False, 'expected False'

    # Restore
    sw.set_loop_detection(orig)
    print(f'  Restored: {sw.get_loop_detection()}')

    # ----------------------------------------------------------------
    # Power saving toggle
    # ----------------------------------------------------------------
    print('\n--- Power Saving (Green Ethernet) ---')
    orig = sw.get_power_saving()
    print(f'  Original: {orig}')

    test('Disable', lambda: sw.set_power_saving(False))
    assert sw.get_power_saving() == False, 'expected False'

    test('Enable', lambda: sw.set_power_saving(True))
    assert sw.get_power_saving() == True, 'expected True'

    sw.set_power_saving(orig)
    print(f'  Restored: {sw.get_power_saving()}')

    # ----------------------------------------------------------------
    # Broadcast filter toggle
    # ----------------------------------------------------------------
    print('\n--- Broadcast Filter ---')
    orig = sw.get_broadcast_filter()
    print(f'  Original: {orig}')

    test('Enable', lambda: sw.set_broadcast_filter(True))
    assert sw.get_broadcast_filter() == True, 'expected True'

    test('Disable', lambda: sw.set_broadcast_filter(False))
    assert sw.get_broadcast_filter() == False, 'expected False'

    sw.set_broadcast_filter(orig)
    print(f'  Restored: {sw.get_broadcast_filter()}')

    # ----------------------------------------------------------------
    # Rate limit on port 3 (not our connection port)
    # ----------------------------------------------------------------
    print('\n--- Rate Limit (port 3 only) ---')
    orig_limits = sw.get_rate_limits()
    orig_p3 = orig_limits[2]  # port 3, 0-indexed
    print(f'  Original port 3: {orig_p3}')

    test('Set 64Mbit/s ingress, 128Mbit/s egress on port 3',
         lambda: sw.set_rate_limit(3, RateLimit.M64, RateLimit.M128))

    current = sw.get_rate_limits()
    p3 = current[2]
    assert p3.ingress == RateLimit.M64,  f'expected M64, got {p3.ingress}'
    assert p3.egress  == RateLimit.M128, f'expected M128, got {p3.egress}'
    print(f'  Verified: {p3}')

    test('Restore port 3', lambda: sw.set_rate_limit(3, orig_p3.ingress, orig_p3.egress))
    p3_back = sw.get_rate_limits()[2]
    assert p3_back.ingress == orig_p3.ingress
    assert p3_back.egress  == orig_p3.egress
    print(f'  Restored: {p3_back}')

    # ----------------------------------------------------------------
    # Switch name
    # ----------------------------------------------------------------
    print('\n--- Switch Name ---')
    cfg = sw.get_switch_config()
    orig_name = cfg.name
    print(f'  Original: {orig_name!r}')

    test('Set name to "test-ng"', lambda: sw.set_switch_name('test-ng'))
    assert sw.get_switch_config().name == 'test-ng', 'name not updated'

    test(f'Restore name to {orig_name!r}',
         lambda: sw.set_switch_name(orig_name))
    assert sw.get_switch_config().name == orig_name

    print(f'  Restored: {sw.get_switch_config().name!r}')

    # ----------------------------------------------------------------
    # Port stats clear (port 1 stats will reset, but that's fine)
    # ----------------------------------------------------------------
    print('\n--- Port Stats Clear ---')
    before = sw.get_port_stats()
    print(f'  Port 1 before: RX={before[0].bytes_rx}B TX={before[0].bytes_tx}B')

    test('Clear counters', sw.clear_port_stats)
    after = sw.get_port_stats()
    print(f'  Port 1 after:  RX={after[0].bytes_rx}B TX={after[0].bytes_tx}B')

print('\nAll write tests passed.')
