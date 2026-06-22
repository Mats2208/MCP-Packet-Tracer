# Cable Types

**15 cable types** are supported. Pass one as the last argument of `pt_add_link`
(or in a plan link's `cable`), or omit it to let the server infer it from the
device categories.

| Cable | When to use |
|-------|-------------|
| `straight` | Different device types (router↔switch, switch↔PC). |
| `cross` | Same-type / end-to-end (router↔router, switch↔switch, router↔PC). |
| `serial` | Serial WAN links (needs serial modules — see [Modules](modules.md)). |
| `fiber` | Fiber ports. |
| `console` | Console connections. |
| `roll` | Rollover (console) cable. |
| `phone` | Analog phone lines. |
| `coaxial` | Coax (cable modem / splitter). |
| `auto` | Let PT auto-select. |
| `usb` | USB connections. |
| `cable` | Generic copper. |
| `wireless` | Wireless association. |
| `octal` | Octal serial. |
| `cellular` | 3G/4G cellular. |
| `custom_io` | Custom I/O (IoT). |

!!! tip "Aliases & inference"
    `pt_add_link` accepts `crossover` (→ `cross`) and `rollover` (→ `roll`). If you
    omit the cable type, it infers from device categories — verified on PT 9.0.0
    (e.g. router↔router → `cross`, switch↔server → `straight`).

!!! warning "Don't use `crossover` as a raw value"
    For direct `addLink` calls the valid value is `cross`, not `crossover`. The
    `pt_add_link` tool accepts the alias and normalizes it for you.
