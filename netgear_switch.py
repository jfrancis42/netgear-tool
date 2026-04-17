"""
Backward-compatibility shim.  The SDK now lives in the netgear_tool package.

Install:

    pip install netgear-tool

Then use:

    from netgear_tool import Switch, PortSpeed, RateLimit

This module re-exports everything for code written against the old name.
"""

from netgear_tool import *  # noqa: F401, F403
from netgear_tool import (  # noqa: F401
    Switch,
    PortSpeed,
    RateLimit,
    SystemInfo,
    SwitchConfig,
    PortInfo,
    PortStats,
    PortRateLimit,
    MirrorConfig,
    IGMPConfig,
    Dot1QVlan,
)
