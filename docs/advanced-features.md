# Switching, IPv6 & Wireless

Beyond basic routing, the MCP can build and configure VLANs, dual-stack IPv6, wireless
clients, and apply layer-2 / device hardening — all verified live against Packet Tracer.

## VLANs & inter-VLAN routing (router-on-a-stick)

Build a one-armed router that routes between VLANs using `.1q` subinterfaces:

```text
"Build a router-on-a-stick with 3 VLANs and 6 PCs, DHCP."
```

The LLM calls:

```python
pt_full_build(template="router_on_a_stick", vlans=3, pcs_per_lan=6, dhcp=True)
```

What you get:

- **N VLANs** spread across the PCs (ids `10, 20, 30, …`), each its own `/24` + DHCP pool.
- The switch uplink becomes a **trunk**; PC ports become **access** ports in their VLAN.
- The router gets one **subinterface per VLAN** (`GigabitEthernet0/0.10`, `…0.20`, …) with
  `encapsulation dot1Q <id>` and the VLAN gateway IP — so inter-VLAN routing just works.

!!! note "2960 vs 3560 trunks"
    On a `2960-24TT` (dot1q-only) the generator omits `switchport trunk encapsulation`;
    on a `3560-24PS` (multi-encap) it emits `switchport trunk encapsulation dot1q`.

To add VLANs to an **already-deployed** topology, use [`pt_apply_vlan`](tools.md):

```python
pt_apply_vlan(
  switch="SW1", router="R1",
  vlans=[{"vlan_id":10,"name":"SALES"},{"vlan_id":20,"name":"ENG"}],
  access_ports=[{"switch":"SW1","port":"FastEthernet0/1","vlan_id":10},
                {"switch":"SW1","port":"FastEthernet0/2","vlan_id":20}],
  trunks=[{"switch":"SW1","port":"GigabitEthernet0/1"}],
  subinterfaces=[{"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":10,"ip_cidr":"192.168.10.1/24"},
                 {"router":"R1","parent_port":"GigabitEthernet0/0","vlan_id":20,"ip_cidr":"192.168.20.1/24"}],
  dry_run=True)   # preview the CLI before applying
```

## IPv6 dual-stack

Add IPv6 alongside IPv4 with one flag:

```python
pt_full_build(routers=2, dual_stack=True)
```

- **Routers** get `ipv6 unicast-routing` + `ipv6 address <prefix>::1/64` per interface (via CLI).
- **Hosts** use **SLAAC** — they auto-configure from the router's Router Advertisements
  (`configurePcIpv6` enables IPv6 + address auto-config).

!!! warning "Static host IPv6 is not available"
    Packet Tracer's Script Engine rejects `addIpv6Address` on host ports, so end devices use
    SLAAC rather than a hardcoded address. Routers carry the explicit `ipv6 address`.

## Wireless laptops

Connect Laptop-PTs over WiFi instead of a cable:

```python
pt_full_build(laptops_per_lan=2, wireless_laptops=True)
```

- Each laptop's wired NIC is swapped for a **wireless card** (`PT-LAPTOP-NM-1W` → `Wireless0`).
- An **Access Point** is added and wired to the switch; laptops **auto-associate** on the
  default SSID (PT's logical view has global RF range, so one AP serves all wireless clients).
- Wireless hosts pull a DHCP lease over the air, landing on the same LAN as the wired PCs.

!!! note "AP SSID / WPA2 is GUI-only"
    Packet Tracer does not expose Access-Point SSID / security through its Script Engine, so
    custom SSID/WPA2 must be set in the AP's GUI. The default-SSID association works out of the box.

## Layer-2 security & device hardening

All of these are config-driven and accept `dry_run=True` to preview the CLI:

| Tool | Example |
|------|---------|
| [`pt_apply_stp`](tools.md) | `pt_apply_stp(switch="SW1", root_primary_vlans=[10], portfast_ports=["FastEthernet0/1"])` |
| [`pt_apply_port_security`](tools.md) | `pt_apply_port_security(switch="SW1", port="FastEthernet0/1", max_mac=2)` |
| [`pt_apply_hardening`](tools.md) | `pt_apply_hardening(device="R1", enable_secret="cisco", users=[{"username":"admin","secret":"pass","privilege":15}], ssh={"domain":"lab.local"})` |
| [`pt_apply_interface_tuning`](tools.md) | `pt_apply_interface_tuning(router="R1", interface="Serial0/0/0", clock_rate=64000)` |

## Verifying a deployment

After a deploy, reconcile what the plan intended against what PT actually has:

```python
pt_diff(plan_json=...)   # missing/extra devices, IP mismatches
pt_health_check()        # down links, cabled-without-IP, duplicate IPs
```

`pt_live_deploy` also **auto-reconciles** — if PT silently drops a device (a known quirk with
Laptop-PT), it re-adds the missing devices/links and re-verifies in the same call.
