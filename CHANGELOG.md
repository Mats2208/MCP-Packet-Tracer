# Changelog

## Unreleased

### Fixed

- **Un salto de línea en un campo de texto ya no se cuela como comando IOS.**
  `configureIosDevice()` parte el payload por `\n` y manda cada trozo al
  dispositivo, así que un `\n` en un nombre se convertía en configuración que
  nadie pidió (por ejemplo, un `username ... privilege 15`). Ahora se rechazan en
  la validación: nombre de VLAN, remark de ACL, hostname de dispositivo y pool
  DHCP (#20, gracias @daniel-baf), más el nombre de una ACL nombrada, el pool y
  el `acl_number` de NAT, y todos los campos de `pt_remove_acl` / `pt_remove_nat`,
  que antes mandaban al bridge sin validar nada.
- **Un solo chequeo para todos.** Las seis copias del helper pasan a
  `domain/rules/text_rules.py`, que además rechaza U+2028/U+2029 (terminan una
  línea en JS igual que `\n`; antes solo lo hacía NetFlow).

## 0.9.0

Varias pasadas manejando el MCP contra Packet Tracer 9.0.1, sobre topologías de
36 y 47 dispositivos. Lo que volvió no fueron crashes: fueron **falsos OK**. Una
topología partida en islas que `pt_validate_plan` aprobaba con `error_count: 0`.
Un bridge que entregaba el resultado de la operación anterior — datos reales, del
dispositivo de al lado. Un "CONECTIVIDAD OK" con 75% de pérdida. Un cable cruzado
en todo router↔switch, en un simulador que existe justamente para enseñar cuál
va. Ninguno se veía desde afuera, y esa es la clase de defecto que esta versión
fue a buscar.

También es la primera que se instala con `pip install packet-tracer-mcp`, y la
primera que dice su propia versión cuando un cliente se la pregunta.

**61 tools · 349 → 473 tests.** Verificado contra Packet Tracer 9.0.1.

### Empaquetado: el servidor entra a PyPI

**El servidor se puede instalar con `pip install packet-tracer-mcp`.**

#### Added

- **Metadata de publicacion en `pyproject.toml`.** Hasta ahora el paquete se
  construia, pero no se podia publicar de forma decente: sin `readme`, la pagina
  de PyPI sale en blanco; sin `classifiers` ni `keywords`, no aparece en ninguna
  busqueda; sin `project.urls`, no hay como volver al repo desde el paquete. Se
  agrego todo eso mas `license = "MIT"` como expresion SPDX (PEP 639), con el
  piso `hatchling>=1.27` en `build-system` porque es donde esa forma empieza a
  existir. `twine check` pasa ahora sin un solo warning.

- **`release.yml`: publicar sin guardar un token.** Trusted Publishing, o sea
  que PyPI confia en la identidad OIDC del workflow en vez de en un secret. Un
  token filtrado publica cualquier version desde cualquier lado; la identidad
  solo vale para este repo, este workflow y el environment `pypi`.

  Dispara con un release **publicado**, no con un push de tag: un tag se empuja
  sin querer, publicar un release lleva un boton de por medio. Antes de subir
  nada compara el tag contra la version de `pyproject.toml` y falla si no
  coinciden — si el release se llama v0.9.0 y el archivo quedo en 0.8.0, PyPI se
  queda con 0.8.0 para siempre y ese numero ya no se puede volver a usar.
  `workflow_dispatch` construye y valida, pero no publica.

#### Changed

- **El sdist pesaba 10.2 MB para 219 KB de codigo.** El resto eran los GIFs y
  PNGs de `demo/`, que hatchling se llevaba por no estar en `.gitignore`. Nadie
  que haga `pip install` necesita el banner del README. Con `demo/`, `docs/`,
  `data/`, `mkdocs.yml` y `.github/` fuera del sdist, el tarball queda en 312 KB
  — 33 veces mas chico.

- **Los links del README son absolutos.** PyPI renderiza el README fuera del
  repo, asi que `src="demo/banner.png"` le queda como una imagen rota, y lo
  mismo las seis referencias a `CHANGELOG.md`, `LICENSE`, `SECURITY.md` y
  companiia. Apuntan a `raw.githubusercontent.com` y a `blob/main`, que se ven
  igual en GitHub y ademas se ven en PyPI.

### La version que el servidor anunciaba de si mismo

**469 -> 473 tests.**

#### Fixed

- **El servidor se presentaba con la version de la libreria `mcp`.** Un
  handshake real por stdio contra PT 9.0.1 devolvia:

      {"name": "Packet Tracer MCP", "version": "1.28.1"}

  1.28.1 es el SDK; el servidor estaba en 0.8.0. El numero sale de
  `create_initialization_options()`, que lo resuelve con
  `self.version if self.version else pkg_version("mcp")`, y FastMCP no expone
  `version` en su `__init__` ni una propiedad para llegar al server lowlevel,
  asi que el fallback ganaba siempre.

  No es cosmetico: ese es el numero que muestran Claude Desktop, Cursor y
  PacketSmith en su panel de servidores, y el que alguien copia en un issue.
  Peor todavia, cambiaba solo al actualizar la dependencia — dos usuarios con
  el mismo codigo podian reportar versiones distintas, y ninguna era la del
  codigo. Ahora se fija sobre `_mcp_server`, con guardia por si el SDK lo
  renombra: en ese caso se vuelve al comportamiento anterior en vez de romper
  el arranque.

#### Added

- **`__version__` en el paquete**, leido de la metadata instalada con
  `importlib.metadata`. La version se sigue declarando una sola vez en
  `pyproject.toml`; copiarla como literal es la forma clasica de que un release
  salga anunciando el numero anterior. Sin instalar, cae a `0.0.0+source`, que
  es un marcador honesto en vez de una mentira plausible.

- **`tests/test_server_version.py`** — 4 tests que fijan lo de arriba: que el
  handshake diga nuestra version, que NO diga la del SDK (el guard de la
  regresion), que el nombre del servidor no se haya movido, y que
  `__version__` coincida con `pyproject.toml`.

### El token del bridge, y sus primeros tests

**449 -> 469 tests.**

#### Fixed

- **`PT_MCP_BRIDGE_TOKEN` saltaba la validacion.** El token de disco pasa por
  `_is_valid` (>=32 caracteres de `[A-Za-z0-9_-]`); la variable de entorno no
  pasaba por nada, asi que `PT_MCP_BRIDGE_TOKEN=x` dejaba un token de UN
  caracter. Es adivinable, y con el token adivinado se cae toda la defensa
  contra la pagina web atacante que este modulo existe para sostener: la
  variable pensada para los tests podia desactivar justo lo que protege.
  Ahora pasa por el mismo gate y, si no sirve, se lanza `BridgeTokenError`
  (que estaba definido y no se usaba en ningun lado).

  Se falla fuerte aca, al reves que con el archivo, y no es incoherente: un
  archivo corrupto es un accidente y rotarlo no pierde nada, pero una variable
  mal puesta es una decision explicita de quien arranca el servidor.

#### Added

- **`bridge_token.py` tiene tests por primera vez** — 20, siendo el ancla de
  seguridad de todo el bridge HTTP. Cubren el aprovisionamiento completo: la
  carrera con `O_EXCL`, la rotacion ante archivo corrupto (vacio, truncado,
  editado a mano), la tolerancia a BOM y espacios, el fallback efimero cuando el
  directorio no se puede escribir, y que la huella no filtre el token.

  El detalle que los hacia imposibles: el CI setea `PT_MCP_BRIDGE_TOKEN` para
  todo el job y `get_bridge_token()` lo devuelve en su PRIMERA linea, asi que el
  camino de archivo no se habria ejecutado ahi nunca. La fixture quita esa
  variable y redirige `token_dir()` a un tmp, de modo que el camino real corre
  tambien en CI y sin tocar el token del usuario que ejecute la suite.

#### Changed

- **`skill/SKILL.md` al dia con todo el repo** (301 -> 395 lineas). Se agrego:
  la tabla de codigos de validacion con que hacer ante cada uno; el patron para
  topologias grandes, cuando el plan no entra por un parametro de tool y hay que
  generarlo local y empujarlo por el buzon; los ocho puertos de la `Cloud-PT` con
  la advertencia de frame relay; `d.moveToLocation(x, y)`; los tres niveles del
  veredicto de ping; y una ronda 3 de hallazgos verificados contra PT 9.0.1 --
  la consola parada en el wizard inicial, la interfaz sin IP que queda en
  shutdown, la asociacion WiFi que no es por proximidad, y el arranque que ahora
  falla si el token del entorno no sirve.

  Tambien se corrigio el limite de `hub_spoke`: la skill decia que enlaza el hub
  con cada spoke, sin mencionar que el hub se queda sin puertos.


### El catalogo mentia sobre la nube

**440 -> 449 tests.**

#### Fixed

- **El catalogo declaraba un solo puerto para `Cloud-PT`.** Leyendo `getPorts()`
  sobre una nube viva en PT 9.0.1 salen ocho:

      Serial0, Serial1, Serial2, Serial3, Modem4, Modem5, Ethernet6, Coaxial7

  El catalogo tenia solo `Ethernet6`, asi que `pt_add_link` rechazaba los otros
  siete y `validate_plan` marcaba el plan invalido antes de que la peticion
  llegara a PT -- por puertos que el dispositivo si tiene. Una nube quedaba
  reducida a un stub de un enlace, cuando en los laboratorios grandes se la usa
  con sus seriales.

- **`Ethernet6` estaba declarado como FastEthernet** con el `full_name` forzado
  a mano. Salia bien por el override, no por la velocidad. Ahora lleva
  `PortSpeed.ETHERNET` y el nombre se deriva solo, como en el resto de los
  modelos. El orquestador buscaba ese puerto con `_fast(...)`, asi que se agrego
  `_ether(...)`: sin eso el enlace router-nube desaparecia en silencio, que es
  justo el tipo de fallo mudo que arreglamos en la tanda anterior. Hay tres
  tests que lo cubren.

#### Added

- `PortSpeed.MODEM`, que faltaba para poder nombrar `Modem4` y `Modem5`.


### Tercera pasada contra PT 9.0.1 — 47 dispositivos

Cinco defectos encontrados revisando el MCP contra Packet Tracer 9.0.1 sobre una
topologia de 47 dispositivos (6 routers en cadena, OSPF area 0, dual-stack, WiFi).
El peor no era un crash: era un **falso OK**.

**392 -> 436 tests.**

#### Fixed

- **El validador aprobaba topologias partidas en islas.** `hub_spoke` pide que el
  hub se enlace con cada spoke, pero un 2911 tiene tres puertos Gigabit. Con seis
  routers `_link_routers` se quedaba sin puertos en el cuarto y simplemente no
  creaba el enlace, sin avisar. Quedaban dos routers sueltos, sin interfaces, sin
  enlaces y con un OSPF de `router-id 0.0.0.0` y cero redes — y `pt_validate_plan`
  devolvia `valid: true`, `error_count: 0`. Una topologia rota se veia idéntica a
  una sana porque todos los chequeos eran por-dispositivo: cada uno existia, cada
  puerto era valido, ninguna IP chocaba. Lo unico que la delata es recorrer el
  grafo. Nuevo `domain/rules/topology_rules.py` con `validate_connectivity`
  (componentes conexas, excluyendo hosts WiFi que no llevan cable a proposito) y
  `validate_routing` (OSPF sin redes, router-id 0.0.0.0).

- **`pt_verify_connectivity` nunca funciono desde un router.** El JS pedia
  `getCommandPrompt()`, que SOLO existe en hosts; contra IOS tira `TypeError:
  Property 'getCommandPrompt' of object is not a function`. Justo la tool para
  verificar routing, inutil en el unico dispositivo que enruta. Verificado contra
  PT 9.0.1: los routers exponen `getCommandLine()` y los PCs exponen las dos, asi
  que ahora se usa esa para ambos. Ademas hay que **cebar la consola**: un router
  recien desplegado por el MCP nunca fue tocado por consola y sigue parado en
  `Would you like to enter the initial configuration dialog? [yes/no]:`, donde el
  `ping` se consume como respuesta al yes/no y no se ejecuta nunca.

- **Un solo AP para toda la topologia.** Con `wireless_laptops=True` se creaba un
  unico `AccessPoint-PT` cableado al switch de la LAN 1, sin importar cuantas LANs
  hubiera. Verificado contra PT 9.0.1: LT9, planificada en la LAN 5, recibia
  `192.168.0.5/24` — del pool DHCP de la LAN 1. Ahora se crea un AP por LAN que
  tenga laptops inalambricas, cada uno cableado al switch de SU LAN.

#### Added

- **`WIRELESS_AMBIGUOUS_ASSOCIATION`: el AP por LAN no alcanza, y hay que decirlo.**
  Un AP por LAN hace POSIBLE el direccionamiento correcto pero **no lo garantiza**.
  Medido contra PT 9.0.1 agregando un AP en la LAN 5 junto a dos laptops de esa
  LAN: LT10 hizo asociacion y DHCP nuevos —paso por `0.0.0.0`— y aun asi eligio el
  AP de la LAN 1 y tomo `192.168.0.13`. Recien con ese AP apagado, LT9 tomo
  `192.168.4.25`, la que le correspondia. O sea que PT **no elige el AP mas
  cercano**: entre APs que comparten el SSID por defecto la asociacion es
  arbitraria, y no hay como desambiguarla porque **PT no expone API de SSID**
  (verificado: ni el AP ni su puerto tienen `setSsid`). El plan ahora emite un
  warning en vez de prometer un direccionamiento que no controla.

- **"CONECTIVIDAD OK" con 75% de perdida.** `interpret_ping` solo decia "llego al
  menos uno", asi que 1 de 4 paquetes se reportaba igual que 4 de 4 y un enlace
  agonizante se veia sano. Nuevo `classify_ping` con tres grados
  (`ok`/`partial`/`none`); `interpret_ping` se mantiene por compatibilidad.

- **El layout se salia del canvas y se pisaba.** El ancho de columna era fijo en
  250 px y el cluster de hosts se centra en el, asi que con 4 PCs por LAN
  (4 x 80 = 320) el cluster desbordaba hacia la LAN vecina y el primer host caia
  en **x = -60**, fuera del canvas. Los servidores, ademas, se colocaban en el
  extremo derecho mientras su cable seguia yendo al PRIMER switch, dibujando
  diagonales de punta a punta. Ahora el ancho de columna se deriva de la LAN mas
  poblada, el origen deja el margen que el centrado necesita, y los servidores van
  en la columna del switch al que se cablean.

- **`pt_health_check` listaba puertos de capa 2 como "cableado sin IP".** Los
  puertos de acceso de cada 2960, los del AP y el Ethernet6 de la nube no llevan
  IP por definicion; ese ruido tapaba el unico caso que importa, el host al que no
  le llego el DHCP. Se filtra por categoria del catalogo (no por `getClassName()`,
  que clasifica por comportamiento: un 3560 responde "Router" y un 2960
  "CiscoDevice"). Un modelo que no resuelve se sigue reportando.


### Segunda pasada contra PT 9.0.1 — 36 dispositivos

Seis defectos encontrados manejando el MCP contra Packet Tracer 9.0.1 sobre una
topología de 36 dispositivos. Cuatro salieron de la primera pasada; dos más
aparecieron al verificar los arreglos **contra el dispositivo** en vez de creerle
al reporte de la tool.

**369 → 392 tests.**

#### Fixed

- **`pt_install_modules_batch` informaba puertos que nunca se crearon.** Dos cosas
  a la vez: los nombres no llevaban el slot (dos HWIC-2T en `"0/0"` y `"0/1"`
  reportaban los mismos `Serial0/0/x`), y sobre todo el envío era
  *fire-and-forget*, así que nadie miraba el retorno de `addModule` — que devuelve
  `false` sin lanzar cuando el slot no existe en ese modelo. Ahora `ports_for_slot()`
  calcula los nombres reales y el JS reporta el resultado de cada módulo **antes**
  del power-on, que era lo único que justificaba no esperar. Devuelve `installed`,
  `installed_count` y `failed`.

- **`pt_add_link` cableaba cruzado todo router↔switch.** Infería la categoría con
  `getClassName()` de PT, que clasifica por comportamiento y no por rol de red: un
  3560 responde `"Router"` (es multicapa) y un 2960 responde `"CiscoDevice"`. La
  categoría `"switch"` no llegaba nunca a las reglas de cableado. Ahora sale del
  modelo vía `category_of_model()`. No rompía la conectividad —el auto-MDIX de PT
  compensa— pero en un simulador educativo enseñaba el cable equivocado.

- **`pt_rename_device` dejaba renombrar a un nombre ya ocupado.** PT lo acepta sin
  chistar y a partir de ahí `getDevice(nombre)` solo resuelve a uno: el otro queda
  en el canvas pero inalcanzable por nombre, y cualquier tool que lo referencie
  trabaja en silencio sobre el equivocado. `pt_add_device` sí validaba; faltaba en
  la otra vía de entrada.

- **`pt_workspace_options` fallaba siempre en `show_device_labels`.**
  `setHideDevLabel` toma **dos** argumentos, no uno. Además cada setter va ahora en
  su propio try/catch: antes uno que fallara abortaba la tanda dejando aplicados los
  anteriores y devolviendo error, o sea "falló" con la mitad de los cambios puestos.
  Se reportan `applied` y `failed`, y el contador refleja lo que PT aceptó.

- **`pt_fix_plan` dejaba el plan internamente inconsistente.** Corregía el puerto en
  el enlace pero no en `device.interfaces`, así que la IP se quedaba en una interfaz
  que ya no usaba ningún enlace. Ahora la migra, IPv4 e IPv6.

#### Changed

- Documentación de slots corregida: el **1941 tiene 2 slots HWIC** (`"0/0"`,`"0/1"`),
  no 4. El 2911 sí acepta `"0/0".."0/3"`. Medido contra PT 9.0.1; decía `0/0..0/3`
  para ambos en el docstring, en `settings.py` y en la skill.
- `WORKSPACE_SETTERS` / `workspace_setter_call()` salen del closure a nivel de
  módulo. La polaridad (PT expone dos opciones en negativo) es justo la clase de
  regla que un refactor puede invertir sin que ningún `assert "..." in src` se
  entere, así que sus tests ahora ejecutan la lógica en vez de leer el fuente.

### El bridge entregaba el resultado de otra operacion

El bridge HTTP entregaba resultados a la operación equivocada. No fallaba: devolvía
datos reales de Packet Tracer, del dispositivo de al lado.

**350 → 369 tests.** Verificado contra Packet Tracer 9.0.1 por el canal HTTP.

La comprobación en vivo salió del propio bug: `pt_add_module` sobre un 2911 tardó
**15 s** —el power-cycle del router se pasa de largo— y el caller esperó sus 15 s
enteros en vez de rendirse a los 9. El módulo se instaló (aparecieron `Serial0/0/0`
y `Serial0/0/1`), o sea que el resultado llegó tarde y quedó huérfano; la llamada
siguiente, un `pt_query_topology`, devolvió **su** topología y no ese resultado. Es
el cruce que antes ocurría, esta vez con PT de verdad.

#### Fixed

- **Los resultados del bridge HTTP se correlacionan por `rid`.** Eran una cola FIFO
  global: quien pedía un resultado se llevaba el primero que hubiera, fuera suyo o no.
  Bastaba que una operación se pasara de su ventana para que su resultado quedara
  huérfano y lo consumiera la siguiente — y a partir de ahí, cada llamada devolvía la
  anterior. Ahora cada operación genera su `rid`, que viaja dentro del JS inyectado y
  PT devuelve al postear. **La extensión no cambia:** nunca construye la URL de
  `/result`, solo ejecuta el JS que le llega, así que el `.pts` sigue siendo el mismo.

- **El caller fija cuánto espera su resultado.** `GET /result` esperaba 9 segundos
  fijos mientras los callers pedían hasta 45. De las 36 llamadas a
  `_bridge_send_and_wait`, **26 pedían más de 9 s**: todas recibían un 204 prematuro y
  se daban por fallidas aunque PT estuviera trabajando bien. El `wait` ahora viaja en
  la petición, con un techo de 60 s para que una espera absurda no ate un thread.

- **Bases de red inválidas explican qué pasó.** `base_network` llegaba cruda hasta
  `IPPlanner`: una `/25` moría con `new prefix must be longer`, una `/24` con un
  `StopIteration` desnudo al pedir la segunda LAN, y un texto cualquiera con
  `AddressValueError`. Los tres salían como stacktrace. Ahora `TopologyRequest` rechaza
  las bases que no dan ni una subred, y el agotamiento real —que depende de cuántas
  LANs pida la topología, y por eso no se sabe hasta el planner— dice cuántas caben y
  qué prefijo usar.

#### Removed

- `PTCommandBridge.send()` y `.send_and_wait()`, que nadie llamaba: el adaptador habla
  con el bridge por HTTP, no por métodos de la instancia. Llevaban una segunda copia
  del mismo bug de correlación y un parámetro `timeout` que no se usaba.

## 0.8.0

El servidor podía construir una red y leerla, pero no mostrarla. Esta versión
cierra eso: el agente ahora entrega un diagrama, no una descripción.

**58 → 61 tools · 319 → 349 tests.** Verificado contra Packet Tracer 9.0.0.0810.

### Added

- **`pt_screenshot`** — captura el canvas lógico a un archivo y devuelve su ruta.
  No devuelve la imagen: son decenas de miles de bytes y llenarían el contexto
  del modelo con datos que nadie puede mirar. PNG por defecto, porque comprime un
  diagrama mucho mejor que JPG (33 KB contra 105 KB sobre el mismo canvas).
- **`pt_add_note`** — escribe una nota sobre el canvas: etiquetar una subred,
  marcar un área OSPF, nombrar un troncal.
- **`pt_clear_annotations`** — borra notas y dibujos. Nunca toca dispositivos ni
  enlaces.

Juntas permiten **topologías auto-documentadas**: construir con `pt_full_build`,
etiquetar cada subred y enlace, y capturar — un diagrama listo para una clase a
partir de un solo prompt.

### Limitación conocida

**No hay tool de dibujo.** Packet Tracer dibuja líneas y círculos en el canvas,
pero no de forma útil desde una extensión: el argumento donde iría el tamaño
resultó controlar el orden de apilado —tres círculos pidiendo 60, 60 y 300
salieron todos del mismo tamaño diminuto— y los colores no producen el color
pedido. Antes que exponer parámetros que no hacen lo que dicen, la anotación
queda limitada a notas de texto. El tamaño de fuente tampoco es configurable,
por la misma razón.

## 0.7.0

Until now the server could build a network but not look at one. It planned,
validated and deployed, and if the result misbehaved the model was blind — it
could redeploy and hope. This release adds the other half: reading the live
devices back, and explaining what they decided and why.

**46 → 58 tools · 188 → 319 tests.** Verified against Packet Tracer 9.0.0.0810.

### Fixed

- **`pt_full_build(deploy=True)` now deploys.** It always went to the clipboard,
  so with the bridge connected it reported `Validación: PASS` and left the canvas
  empty — the main pipeline silently built nothing. It now deploys through the
  `pt_live_deploy` path (device and link verification, plus the reconcile pass
  for the devices PT drops) and falls back to the clipboard only when no channel
  exists.

### Added — reading the live topology

- **`pt_audit_security`** — grades the effective configuration of every IOS
  device: missing `enable secret`, credentials stored reversibly, `service
  password-encryption` off, no local users, no MOTD banner, and a
  config-register left at `0x2142` (which discards the startup-config on the
  next reboot). Findings carry a severity and a suggested fix.
  Credentials never leave the device — only the algorithm label is transmitted,
  because a hash in a tool result ends up in the model's context and the client's
  logs.
- **`pt_inspect_ports`** — per-port line and protocol status, MAC, addressing,
  duplex, bandwidth, MTU, delay, CDP, DHCP-client state, NAT mode and applied
  ACLs. Flags cabled-but-down and line-up-protocol-down.
- **`pt_read_vlans`** — the switch's real VLAN database, separating your VLANs
  from the ones PT ships with.
- **`pt_device_power`** — power a device off and on with read-back, to simulate
  an outage or force a reboot.

### Added — simulation

- **`pt_read_packet_trace`** — the simulation event list: per frame the path,
  the outcome, and **PT's own per-OSI-layer explanation of each decision**. A
  failing ping stops being "no reply" and becomes a cause, e.g. *"The next-hop IP
  address is not in the ARP table. The ARP process buffers this packet."*
- **`pt_simulation_mode`** / **`pt_simulation_step`** — switch between Realtime
  and Simulation, and move the event list forward, back or to the start.

### Added — telemetry, QoS and backup

- **`pt_apply_netflow`** — create, reconfigure or remove a NetFlow exporter
  (collector address, UDP port, version, source interface, monitors) and read the
  result back. Reapplying a name reconfigures rather than duplicating.
- **`pt_read_qos`** — class-maps and policy-maps with their CLI form. Read-only:
  QoS cannot be created programmatically, so author it with IOS CLI and use this
  to confirm it landed.
- **`pt_backup_config`** — the device's real startup-config plus serial,
  config-register, boot images and uptime. Optional full XML dump.
- **`pt_project_metadata`** — saved filename, PT version, description and
  device/link count; flags a project that has never been saved.
- **`pt_workspace_options`** — auto-cabling (turn it off before a scripted build
  if you need links on exact interfaces) and access to the real network, plus the
  canvas labels that decide whether a screenshot is readable.

### Improved

- **`pt_apply_interface_tuning`** gains `ospf_dead_interval` and OSPF
  authentication in both message-digest and plaintext form. The key is emitted
  before authentication is enabled — the other order leaves the interface
  demanding auth with nothing to answer and the adjacency drops. `dead <= hello`
  is rejected, since mismatched timers mean no adjacency forms at all.
- **`pt_set_port`** gains `zone_member` (Zone-Based Firewall), `proxy_arp` —
  turning it off is routine hardening, since a router answering ARPs that are not
  its own leaks topology — and `ike` for IPsec.

Both were extended rather than given their own tools: `pt_apply_interface_tuning`
already set the other OSPF knobs and `pt_set_port` already applied low-level port
attributes, so separate tools would have been mostly duplicate.

### Known limitations

- **No `pt_send_pdu`.** Packet Tracer does not let an extension originate a
  packet the way the GUI's *Add Simple PDU* button does. Generate traffic with a
  real ping (`pt_verify_connectivity`) and then read the trace.
- **QoS is read-only.** Class-maps and policy-maps cannot be created through the
  API; author them with IOS CLI.
- **`zone_member` needs its zone to exist.** Setting it succeeds, but the
  interface line only appears once a matching `zone security` is configured.

## 0.6.0

- The live-deploy bridge authenticates with a per-machine token. Earlier versions
  had an unauthenticated bridge: any web page open while Packet Tracer was
  running could execute code inside it. **Requires the V5 extension.**
