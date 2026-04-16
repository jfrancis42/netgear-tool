# Netgear GS105Ev2 CLI User Guide

`cli.py` is an interactive command-line interface for Netgear Smart Managed
Plus switches, modelled after Cisco IOS.  It lets you read and configure the
switch without opening a browser.

## Contents

- [Starting the CLI](#starting-the-cli)
- [Modal structure](#modal-structure)
- [Abbreviations](#abbreviations)
- [Exec mode](#exec-mode)
- [Config mode](#config-mode)
- [Interface mode](#interface-mode)
- [VLAN mode](#vlan-mode)
- [Common workflows](#common-workflows)

---

## Starting the CLI

```bash
python3 cli.py <host> [--password PASSWORD]
```

```bash
# Password prompted interactively (recommended)
python3 cli.py 192.168.0.1

# Password on command line
python3 cli.py 192.168.0.1 --password secret
```

On success the prompt appears with the switch name as the hostname:

```
Connecting to 192.168.0.1... OK
  GS105Ev2  |  FW: V1.6.0.24  |  IP: 192.168.0.1

Type ? for help.  Type 'exit' to disconnect.

GS105Ev2#
```

---

## Modal structure

```
exec                          ← login lands here
 └─ configure terminal
     └─ config                ← global configuration
         ├─ interface gi N
         │   └─ config-if     ← per-port configuration
         └─ vlan N
             └─ config-vlan   ← VLAN sub-mode
```

| Command | Effect |
|---|---|
| `configure terminal` | Enter config mode |
| `interface gi<N>` | Enter interface mode for port N |
| `interface range gi<N>-<M>` | Enter interface mode for a port range |
| `vlan <id>` | Create VLAN and enter VLAN config mode |
| `exit` | Go up one level |
| `end` | Return directly to exec mode |
| `do <command>` | Run any exec-mode command from config/if/vlan mode |

---

## Abbreviations

Any command can be abbreviated to the shortest unambiguous prefix.

```
sho ver         → show version
conf t          → configure terminal
int gi3         → interface gi3
sw acc vl 10    → switchport access vlan 10
band ing 64m    → bandwidth ingress 64m
```

If an abbreviation is ambiguous, the CLI lists the candidates:

```
GS105Ev2# sho b
  % Ambiguous: broadcast-filter
```

---

## Exec mode

### show version

```
GS105Ev2# show version

  GS105Ev2
  Model    : GS105Ev2
  Serial   : 3MX14951002F0
  Firmware : V1.6.0.24
  MAC      : 6C:B0:CE:29:96:82
  IP       : 192.168.0.1 / 255.255.255.0  (static)
  Gateway  : 192.168.0.254
```

### show interfaces

```
GS105Ev2# show interfaces

  Port    Status        Link          Config        FC
  ----------------------------------------------------
  gi1     up            1000M         Auto          on
  gi2     down          --            Auto          on
  gi3     down          --            Auto          on
  gi4     down          --            Auto          on
  gi5     down          --            Auto          on
```

```
GS105Ev2# show interfaces gi1        ← single port
GS105Ev2# show interfaces counters   ← RX/TX byte counters and CRC errors
```

### show vlan

```
GS105Ev2# show vlan

  802.1Q VLAN: enabled

  VLAN    Untagged Ports          Tagged Ports
  ----    ----------------------  ------------
  1       gi1-5                   -
  10      gi1                     gi5
  20      gi2,gi3                 gi5

  Port PVIDs: gi1:10  gi2:20  gi3:20  gi4:1  gi5:1
```

### show ip

```
GS105Ev2# show ip

  IP Address : 192.168.0.1
  Subnet Mask: 255.255.255.0
  Gateway    : 192.168.0.254
  DHCP       : disabled
```

### show qos

```
GS105Ev2# show qos

  QoS mode: 802.1p/dscp

  Port    Ingress         Egress
  ------  --------------  --------------
  gi1     no-limit        no-limit
  gi2     no-limit        no-limit
  gi3     64m             128m
  gi4     no-limit        no-limit
  gi5     no-limit        no-limit
```

### show loop-detection

```
GS105Ev2# show loop-detection

  Loop detection: enabled
```

### show broadcast-filter

```
GS105Ev2# show broadcast-filter

  Broadcast filter: disabled
```

### show igmp

```
GS105Ev2# show igmp

  IGMP snooping    : enabled
  VLAN             : (all)
  Router port      : (none)
  Validate IP hdr  : no
  Block unknown MC : no
```

### show port-mirror

```
GS105Ev2# show port-mirror

  Port mirroring: enabled
  Destination   : gi5
  Source ports  : gi1-3
  Direction     : all (rx+tx, hardware limitation)
```

### show running-config

Prints the full switch configuration in CLI syntax.

```
GS105Ev2# show running-config
!
hostname GS105Ev2
!
ip address 192.168.0.1 255.255.255.0 192.168.0.254
!
loop-detection
igmp snooping
qos mode 802.1p/dscp
!
vlan 10
vlan 20
!
interface gi1
 flowcontrol
 switchport pvid 10
!
interface gi2
 flowcontrol
 switchport pvid 20
!
...
end
```

### clear counters

```
GS105Ev2# clear counters
```

### test cable-diagnostics

```
GS105Ev2# test cable-diagnostics interface gi1
  Running cable diagnostics...

  Port    Status                  Length
  ------  ----------------------  ------
  gi1     Normal                  --
```

```
GS105Ev2# test cable-diagnostics          ← all ports
```

### reload

```
GS105Ev2# reload
  Proceed with reload? [y/N] y
  Reloading...
```

### write erase — factory reset

```
GS105Ev2# write erase
  Factory reset? ALL configuration will be lost. [y/N] y
  Factory reset initiated. Switch is rebooting...
```

---

## Config mode

Enter with `configure terminal` from exec mode.

### hostname

```
GS105Ev2(config)# hostname lab-sw-1
lab-sw-1(config)#
```

### ip address

```
GS105Ev2(config)# ip address 192.168.0.1 255.255.255.0 192.168.0.254
GS105Ev2(config)# ip address dhcp
GS105Ev2(config)# no ip address dhcp      ← disable DHCP, keep existing IP
```

### loop-detection

```
GS105Ev2(config)# loop-detection
GS105Ev2(config)# no loop-detection
```

### broadcast-filter

```
GS105Ev2(config)# broadcast-filter
GS105Ev2(config)# no broadcast-filter
```

### igmp snooping

```
GS105Ev2(config)# igmp snooping
GS105Ev2(config)# igmp snooping vlan 10
GS105Ev2(config)# igmp snooping router-port 5
GS105Ev2(config)# igmp snooping validate-ip
GS105Ev2(config)# igmp snooping block-unknown
GS105Ev2(config)# no igmp snooping
```

Options can be combined in one command:

```
GS105Ev2(config)# igmp snooping vlan 10 router-port 5 validate-ip
```

### qos mode

```
GS105Ev2(config)# qos mode port-based
GS105Ev2(config)# qos mode 802.1p
```

### monitor session — port mirroring

> **Note:** The GS105Ev2 mirrors all traffic (rx+tx) on the selected source
> ports.  Ingress-only or egress-only mirroring is not supported by the
> hardware; a direction keyword is accepted for syntax compatibility but
> ignored.

```
GS105Ev2(config)# monitor session 1 destination interface gi5
GS105Ev2(config)# monitor session 1 source interface gi1
GS105Ev2(config)# monitor session 1 source interface gi1,gi2,gi3
GS105Ev2(config)# monitor session 1 source interface gi1-3
GS105Ev2(config)# no monitor session 1
```

### 802.1Q VLAN

```
GS105Ev2(config)# vlan 10
GS105Ev2(config-vlan-10)# exit

GS105Ev2(config)# no vlan 10
```

The `vlan <id>` command enables 802.1Q mode automatically if it is not
already on, and creates the VLAN if it does not exist.

### username — change password

```
GS105Ev2(config)# username admin password oldpassword newpassword
  Password changed.
```

---

## Interface mode

Enter with `interface gi<N>` or `interface range gi<N>-<M>` from config mode.

```
GS105Ev2(config)# interface gi3
GS105Ev2(config-if-gi3)#

GS105Ev2(config)# interface range gi1-4
GS105Ev2(config-if-gi1-4)#
```

### shutdown / no shutdown

On the GS105Ev2, disabling a port sets its configured speed to `Disable`.
`no shutdown` restores the speed to `Auto`.

```
GS105Ev2(config-if-gi3)# shutdown
GS105Ev2(config-if-gi3)# no shutdown
```

### speed

The switch supports auto-negotiation (up to 1 Gbit/s) and forced 10/100M
half or full-duplex.

```
GS105Ev2(config-if-gi1)# speed auto
GS105Ev2(config-if-gi1)# speed 100 full
GS105Ev2(config-if-gi1)# speed 100 half
GS105Ev2(config-if-gi1)# speed 10 full
GS105Ev2(config-if-gi1)# speed 10 half
```

### flowcontrol

```
GS105Ev2(config-if-gi1)# flowcontrol
GS105Ev2(config-if-gi1)# no flowcontrol
```

### switchport

```
# Access port (untagged on VLAN, PVID updated automatically)
GS105Ev2(config-if-gi1)# switchport access vlan 10

# Trunk port (add/remove tagged VLANs)
GS105Ev2(config-if-gi5)# switchport trunk allowed vlan add 10
GS105Ev2(config-if-gi5)# switchport trunk allowed vlan remove 10

# Set PVID explicitly
GS105Ev2(config-if-gi1)# switchport pvid 10
```

### bandwidth

Rate values: `no-limit`, `512k`, `1m`, `2m`, `4m`, `8m`, `16m`, `32m`,
`64m`, `128m`, `256m`, `512m`.

```
GS105Ev2(config-if-gi3)# bandwidth ingress 64m
GS105Ev2(config-if-gi3)# bandwidth egress 128m
GS105Ev2(config-if-gi3)# no bandwidth            ← remove both limits
GS105Ev2(config-if-gi3)# no bandwidth ingress    ← ingress limit only
```

---

## VLAN mode

```
GS105Ev2(config)# vlan 10
GS105Ev2(config-vlan-10)# do show vlan
GS105Ev2(config-vlan-10)# exit
```

Port membership is managed with `switchport` commands in interface mode.
There are no VLAN-level sub-commands beyond navigation.

---

## Common workflows

### Configure a trunk and access ports

Goal: gi5 = trunk carrying VLANs 10 and 20; gi1 = access on VLAN 10;
gi2, gi3 = access on VLAN 20.

```
conf t

vlan 10
 exit
vlan 20
 exit

interface gi1
 switchport access vlan 10
 exit
interface gi2
 switchport access vlan 20
 exit
interface gi3
 switchport access vlan 20
 exit
interface gi5
 switchport trunk allowed vlan add 10
 switchport trunk allowed vlan add 20
 exit
end

show vlan
```

### Rate-limit a port

```
conf t
interface gi3
 bandwidth ingress 64m
 bandwidth egress 128m
end
show qos
```

### Mirror a port for packet capture

```
conf t
monitor session 1 destination interface gi5
monitor session 1 source interface gi1
end
show port-mirror
```

### Harden L2 settings

```
conf t
loop-detection
broadcast-filter
igmp snooping validate-ip block-unknown
end
show loop-detection
show broadcast-filter
show igmp
```

### Initial provisioning from factory defaults

The switch resets to `192.168.0.1` with password `password` after a factory
reset.

```bash
python3 cli.py 192.168.0.1 --password password
```

```
switch(config)# hostname lab-gs105-1
lab-gs105-1(config)# ip address 192.168.0.1 255.255.255.0 192.168.0.254
lab-gs105-1(config)# username admin password password newsecret
lab-gs105-1(config)# loop-detection
lab-gs105-1(config)# igmp snooping
lab-gs105-1(config)# end
```

### Factory reset and reconfigure

```
GS105Ev2# write erase
```

After the switch reboots, reconnect at its factory default IP (`192.168.0.1`,
password `password`) and reconfigure.
