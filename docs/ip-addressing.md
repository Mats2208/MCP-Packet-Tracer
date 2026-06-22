# IP Addressing

The planner assigns addresses automatically and consistently.

## LAN subnets

- Each LAN gets a sequential **/24**: `192.168.0.0/24`, `192.168.1.0/24`, …
- The router interface is the **gateway** at `.1`.
- Hosts start at `.2` (`.2`, `.3`, …).

```text
LAN 1:  192.168.0.0/24  →  R1 Gig0/1: .1 (gw)   PC1: .2   PC2: .3
LAN 2:  192.168.1.0/24  →  R2 Gig0/1: .1 (gw)   PC3: .2   PC4: .3
```

## Router-to-router (WAN) links

- Point-to-point links get a **/30** from `10.0.0.0/30` onward (`10.0.0.0/30`,
  `10.0.0.4/30`, …) — only 2 usable hosts, ideal for serial/Ethernet WAN links.

```text
Link:  10.0.0.0/30  →  R1 Gig0/0: 10.0.0.1   R2 Gig0/0: 10.0.0.2
```

## DHCP

- With DHCP enabled, one **pool per LAN** is generated on the LAN's router.
- The gateway (`.1`) is excluded from the pool.
- DHCP clients (PCs/servers) receive their IP/mask/gateway from the router pool —
  the MCP does not set those fields directly in that mode.

## Static hosts

For static PCs the MCP sets the interface directly via `configurePcIp` /
`setIpSubnetMask(ip, mask)`. Verified on PT 9.0.0: the IP lands in the **IPv4
Address** field and the mask in the **Subnet Mask** field (correct order).
