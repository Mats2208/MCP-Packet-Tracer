# Packet Tracer Script Engine — API Reference

Everything documented here was **verified against a live Packet Tracer 8.x instance**.
Methods were discovered by enumerating IPC objects with `for(var p in obj)` and
confirmed by actually calling them.

---

## Table of Contents

1. [Critical Constraints](#critical-constraints)
2. [IPC Entry Points](#ipc-entry-points)
3. [Logical Workspace](#logical-workspace)
4. [Device](#device)
5. [Port](#port)
6. [Network](#network)
7. [Link](#link)
8. [Verified Device Ports](#verified-device-ports)
9. [PTBuilder Functions (userfunctions.js)](#ptbuilder-functions)
10. [HTTP Bridge Architecture](#http-bridge-architecture)
11. [Cable Types](#cable-types)
12. [Module Types](#module-types)
13. [Device Type IDs](#device-type-ids)
14. [Known Limitations](#known-limitations)

---

## Critical Constraints

### XMLHttpRequest does NOT exist in the Script Engine

The PT Script Engine (where `$se('runCode', ...)` executes) does **not** have
`XMLHttpRequest`, `fetch`, or any HTTP API. Only the **QWebEngine webview**
(Chromium-based) has `XMLHttpRequest`.

To send HTTP from the Script Engine, route through the webview:

```javascript
window.webview.evaluateJavaScriptAsync(
    "var x=new XMLHttpRequest();" +
    "x.open('POST','http://127.0.0.1:54321/result',true);" +
    "x.setRequestHeader('Content-Type','text/plain');" +
    "x.send('hello');"
);
```

### Port objects are not enumerable

`for(var p in port)` returns nothing. But methods like `getName()`, `getIpAddress()`
etc. **do work** when called directly. The methods are implemented in native C++ and
not exposed as enumerable JS properties.

### Device names may be auto-modified

When calling `lw.addDevice(type, model, x, y)`, PT returns the name it assigned
(e.g., "Router0"). You must then call `device.setName("R1")` to rename it.

---

## IPC Entry Points

```javascript
// Application window
ipc.appWindow()

// Active workspace
ipc.appWindow().getActiveWorkspace()

// Logical workspace (where devices live)
ipc.appWindow().getActiveWorkspace().getLogicalWorkspace()
// Shorthand: lw

// Network (device and link access)
ipc.network()
```

---

## Logical Workspace

Object: `ipc.appWindow().getActiveWorkspace().getLogicalWorkspace()`

### Device Management

| Method | Description |
|--------|-------------|
| `addDevice(type, model, x, y)` | Add device. Returns assigned name or empty on failure. |
| `removeDevice(name)` | Remove device and all its links. |

### Link Management

| Method | Description |
|--------|-------------|
| `createLink(dev1Name, port1Name, dev2Name, port2Name, linkType)` | Create a link. Returns `true` on success. |
| `deleteLink(devName, portName)` | Delete the link on a specific port. |

### Canvas / Positioning

| Method | Description |
|--------|-------------|
| `moveCanvasItemBy(name, dx, dy)` | Move device by relative offset. |
| `setCanvasItemRealPos(name, x, y)` | Set device absolute position. |
| `getCanvasItemRealX(name)` | Get X position of canvas item. |
| `getCanvasItemRealY(name)` | Get Y position of canvas item. |
| `centerOn(name)` | Center viewport on device. |

### Notes & Annotations

| Method | Description |
|--------|-------------|
| `addNote(text, x, y)` | Add a text note to canvas. |
| `changeNoteText(noteId, text)` | Change existing note text. |
| `getCanvasNoteText(noteId)` | Get text of a note. |
| `getCanvasNoteIds()` | Get all note IDs. |

### Drawing

| Method | Description |
|--------|-------------|
| `drawCircle(x, y, r)` | Draw circle on canvas. |
| `drawLine(x1, y1, x2, y2)` | Draw line on canvas. |
| `getCanvasEllipseIds()` | Get ellipse item IDs. |
| `getCanvasLineIds()` | Get line item IDs. |

### Cluster / Grouping

| Method | Description |
|--------|-------------|
| `addCluster(name)` | Create device cluster. |
| `removeCluster(id)` | Remove cluster. |
| `moveItemToCluster(itemId, clusterId)` | Move item into cluster. |
| `showClusterContents(clusterId)` | Expand cluster. |

### All Verified Methods

```
addCluster, addDevice, addNote, addRemoteNetwork, addTextPopup,
autoConnectDevices, centerOn, centerOnComponentByName, changeNoteText,
clearLayer, createLink, deleteLink, drawCircle, drawLine,
getCanvasEllipseIds, getCanvasItemIds, getCanvasItemRealX,
getCanvasItemRealY, getCanvasItemX, getCanvasItemY, getCanvasLineIds,
getCanvasNoteIds, getCanvasNoteText, getCanvasPolygonIds,
getCanvasRectIds, getClassName, getCluster, getClusterForItem,
getClusterFromItem, getClusterIdForItem, getClusterItemId,
getComponentChildCountFor, getComponentChildForAt,
getComponentChildForByName, getComponentItem, getComponentItemsCount,
getCurrentCluster, getCurrentZoom, getEllipseItemData,
getIncNoteZOrder, getLayerInbetweenComponents, getLineItemData,
getMUItemCount, getObjectUuid, getPolygonItemData, getRectItemData,
getRootCluster, getState, getUnusedLayer, getWorkspaceImage,
isLayerUsed, moveCanvasItemBy, moveItemToCluster, moveRemoteNetwork,
removeCanvasItem, removeCluster, removeDevice, removeRemoteNetwork,
removeTextPopup, setCanvasItemRealPos, setCanvasItemX, setCanvasItemY,
setDeviceCustomImage, showClusterContents, unCluster
```

---

## Device

Object: `ipc.network().getDevice(name)`

### Identity

| Method | Description |
|--------|-------------|
| `getName()` | Get device name. |
| `setName(name)` | Set device name. |
| `getModel()` | Get model string (e.g. "2911"). |
| `getType()` | Get device type ID (0=router, 1=switch, ...). |
| `getSerialNumber()` | Get serial number. |
| `getObjectUuid()` | Get unique ID. |

### Position

| Method | Description |
|--------|-------------|
| `getXCoordinate()` | Get X position. **NOT `getX()`!** |
| `getYCoordinate()` | Get Y position. **NOT `getY()`!** |
| `getCenterXCoordinate()` | Get center X. |
| `getCenterYCoordinate()` | Get center Y. |
| `moveToLocation(x, y)` | Move to absolute position. |
| `moveToLocationCentered(x, y)` | Move centered on coordinates. |

### Ports

| Method | Description |
|--------|-------------|
| `getPort(name)` | Get port by name (e.g. "GigabitEthernet0/0"). |
| `getPortAt(index)` | Get port by index. |
| `getPortCount()` | Get total port count. |
| `getPorts()` | Get all ports (may not be iterable in JS). |

### Modules

| Method | Description |
|--------|-------------|
| `addModule(slot, moduleType, model)` | Add hardware module. Power off first! |
| `removeModule(slot)` | Remove module from slot. |
| `getSupportedModule(slot)` | Check if slot supports a module type. |
| `getRootModule()` | Get root module object. |

### Power & Boot

| Method | Description |
|--------|-------------|
| `getPower()` | Get power state (boolean). |
| `setPower(state)` | Set power on/off. |
| `skipBoot()` | Skip IOS boot sequence (instant ready). |

### Configuration

| Method | Description |
|--------|-------------|
| `enterCommand(cmd, mode)` | Enter CLI command. `mode`: "global", "enable", "" (auto-detect). |
| `getCommandLine()` | Get current CLI prompt/state. |
| `setDhcpFlag(enabled)` | Enable/disable DHCP on PC-type devices. |

### Custom Images

| Method | Description |
|--------|-------------|
| `setCustomLogicalImage(path)` | Set custom icon for logical view. |
| `setCustomPhysicalImage(path)` | Set custom icon for physical view. |
| `getCustomLogicalImage()` | Get current custom logical image. |
| `restoreToDefault()` | Restore default appearance. |

### All Verified Methods

```
activityTreeToXml, addCustomVar, addDeviceExternalAttributes,
addModule, addProgrammingSerialOutputs, addSound, addUserDesktopApp,
clearDeviceExternalAttributes, clearProgrammingSerialOutputs,
destroySounds, getAreaLeftX, getAreaTopY, getCenterXCoordinate,
getCenterYCoordinate, getClassName, getCommandLine, getCustomInterface,
getCustomLogicalImage, getCustomPhysicalImage, getCustomVarNameAt,
getCustomVarStr, getCustomVarValueStrAt, getCustomVarsCount,
getDescriptor, getDeviceExternalAttributeValue,
getDeviceExternalAttributes, getGlobalXPhysicalWS,
getGlobalYPhysicalWS, getModel, getName, getObjectUuid,
getPhysicalObject, getPort, getPortAt, getPortCount, getPorts,
getPower, getProcess, getProgrammingSerialOutputs, getRootModule,
getSerialNumber, getSupportedModule, getType, getUpTime, getUsbPortAt,
getUsbPortCount, getUserDesktopAppAt, getUserDesktopAppByDir,
getUserDesktopAppById, getUserDesktopAppCount, getXCoordinate,
getXPhysicalWS, getYCoordinate, getYPhysicalWS, hasCustomVar,
isDesktopAvailable, isOutdated, isProjectRunning, moveByInPhysicalWS,
moveToLocInPhysicalWS, moveToLocation, moveToLocationCentered,
playSound, relinkUserDesktopApp, removeCustomVar, removeModule,
removeUserDesktopApp, restoreToDefault, runCodeInProject, runProject,
serializeToXml, setCustomInterface, setCustomLogicalImage,
setCustomPhysicalImage, setDeviceExternalAttributes, setName, setPower,
setTime, stopProject, stopSound, stopSounds,
subtractDeviceExternalAttributes, updateTemplateCreationTime
```

---

## Port

Object: `device.getPort("GigabitEthernet0/0")` or `device.getPortAt(index)`

**Note:** Port methods are NOT enumerable via `for..in` — they are C++ bindings.
These were verified by calling them directly.

| Method | Description |
|--------|-------------|
| `getName()` | Port name (e.g. "GigabitEthernet0/0"). |
| `getIpAddress()` | Current IP address. |
| `getSubnetMask()` | Current subnet mask. |
| `setIpSubnetMask(ip, mask)` | Set IP and mask (for PC/Server ports). |
| `setDefaultGateway(gw)` | Set default gateway (for PC/Server). |
| `setDnsServerIp(dns)` | Set DNS server (for PC/Server). |
| `getMacAddress()` | Get MAC address. |
| `getBandwidth()` | Get bandwidth in Kbps (1000000 = 1Gbps). |
| `getLink()` | Get the Link object connected to this port (or null). |
| `getObjectUuid()` | Get unique ID. |

---

## Network

Object: `ipc.network()`

| Method | Description |
|--------|-------------|
| `getDevice(name)` | Get device by name. |
| `getDeviceAt(index)` | Get device by index. |
| `getDeviceCount()` | Total device count. |
| `getLinkAt(index)` | Get link by index. |
| `getLinkCount()` | Total link count. |
| `getObjectUuid()` | Unique ID. |
| `getTotalDeviceAttributeValue(attr)` | Aggregate device attribute. |

---

## Link

Object: `ipc.network().getLinkAt(index)` or `port.getLink()`

| Method | Description |
|--------|-------------|
| `getPort1()` | First port of the link. |
| `getPort2()` | Second port of the link. |
| `getOtherPort(port)` | Given one port, get the other end. |
| `getConnectionType()` | Cable type ID. |
| `getObjectUuid()` | Unique ID. |

---

## Verified Device Ports

Every port was probed from a live PT 8.x instance. **No router has serial ports
by default** — you need to add a HWIC-2T module (which may or may not work
depending on slot compatibility).

### Routers

| Model | pt_type | Ports | GigE Count |
|-------|---------|-------|------------|
| 1941 | `1941` | GigabitEthernet0/0, GigabitEthernet0/1 | 2 |
| 2901 | `2901` | GigabitEthernet0/0, GigabitEthernet0/1 | 2 |
| 2911 | `2911` | GigabitEthernet0/0, GigabitEthernet0/1, GigabitEthernet0/2 | 3 |
| ISR 4321 | `ISR4321` | GigabitEthernet0/0/0, GigabitEthernet0/0/1 | 2 |

> All routers also have a `Vlan1` SVI (not used for physical cabling).

### Switches

| Model | pt_type | Ports |
|-------|---------|-------|
| 2960 | `2960-24TT` | FastEthernet0/1-24 (24), GigabitEthernet0/1-2 (2) = 26 |
| 3560 | `3560-24PS` | FastEthernet0/1-24 (24), GigabitEthernet0/1-2 (2) = 26 |

> Both also have `Vlan1` SVI.

### End Devices

| Model | pt_type | Ports |
|-------|---------|-------|
| PC | `PC-PT` | FastEthernet0, Bluetooth |
| Server | `Server-PT` | FastEthernet0 |
| Laptop | `Laptop-PT` | FastEthernet0, Bluetooth |

### Other

| Model | pt_type | Ports |
|-------|---------|-------|
| Cloud | `Cloud-PT` | Serial0-3, Modem4-5, Ethernet6, Coaxial7 |
| Access Point | `AccessPoint-PT` | Port 0, Port 1 |

---

## PTBuilder Functions

These functions are defined in `PTBuilder/source/userfunctions.js` and are
available in the PT Script Engine when the PTBuilder extension is loaded.

### Core Functions (from PTBuilder)

```javascript
// Add a device to the canvas
addDevice(deviceName, deviceModel, x, y)
// Returns: true/false

// Add a hardware module (power off, insert, power on, skipBoot)
addModule(deviceName, slot, moduleModel)
// Returns: true/false

// Create a cable between two interfaces
addLink(dev1Name, dev1Interface, dev2Name, dev2Interface, linkType)
// linkType: "straight", "cross", "fiber", "console"
// Returns: true/false

// Configure a PC/Server/Laptop IP
configurePcIp(deviceName, dhcpEnabled, ip, mask, gateway, dns)
// dhcpEnabled: true for DHCP, false for static

// Send CLI commands to a router or switch
configureIosDevice(deviceName, commands)
// commands: newline-separated CLI string

// Get device names with optional type filter
getDevices(filter, startsWith)
// filter: "router", 0, ["switch","router"], etc.
// Returns: string array of device names
```

### Bridge Functions (PT-MCP bidirectional)

```javascript
// Send data back to the Python MCP server via HTTP bridge
// Routes through QWebEngine webview (Script Engine has NO XMLHttpRequest)
reportResult(data)
// data: string or object (auto-serialized to JSON)

// Query all devices currently in the topology
queryTopology()
// Calls reportResult with { devices: [...], count: N }

// Delete a device (using lw.removeDevice, NOT lw.deleteDevice)
deleteDevice(deviceName)
// Calls reportResult with { success: true/false }

// Rename a device
renameDevice(oldName, newName)
// Calls reportResult with { success: true/false }

// Move a device (using device.moveToLocation, NOT lw.moveDeviceInLogicalWorkspace)
moveDevice(deviceName, x, y)
// Calls reportResult with { success: true/false }

// Delete a link on a specific interface
deleteLink(deviceName, interfaceName)
// Calls reportResult with { success: true/false }
```

---

## HTTP Bridge Architecture

```
┌─────────────────────┐     HTTP :54321      ┌─────────────────────────┐
│   Python MCP Server │◄────────────────────►│    PTCommandBridge      │
│   (port 39000)      │  POST /queue (JS)    │    (ThreadingHTTPServer)│
│                     │  GET  /next  (poll)   │                        │
│                     │  POST /result (data)  │                        │
│                     │  GET  /result (poll)  │                        │
└─────────────────────┘                       └────────┬────────────────┘
                                                        │
                                                  XMLHttpRequest
                                                  (webview only)
                                                        │
                                              ┌─────────▼──────────┐
                                              │  QWebEngine Webview │
                                              │  setInterval poll   │
                                              │  GET /next → code   │
                                              │  $se('runCode',cmd) │
                                              └─────────┬──────────┘
                                                        │
                                                  runCode()
                                                        │
                                              ┌─────────▼──────────┐
                                              │  PT Script Engine   │
                                              │  No XMLHttpRequest! │
                                              │  Has: ipc, window,  │
                                              │  userfunctions.js   │
                                              └────────────────────┘
```

### Bootstrap Script

Paste in **Builder Code Editor** (Extensions > Builder Code Editor) and click Run:

```javascript
/* PT-MCP Bridge */
window.webview.evaluateJavaScriptAsync("setInterval(function(){var x=new XMLHttpRequest();x.open('GET','http://127.0.0.1:54321/next',true);x.onload=function(){if(x.status===200&&x.responseText){$se('runCode',x.responseText)}};x.onerror=function(){};x.send()},500)");
```

### How it works

1. Python MCP server starts bridge on `:54321` automatically
2. User pastes bootstrap into Builder Code Editor (one-time setup)
3. Bootstrap injects a polling loop into the **webview** (not the Script Engine)
4. Every 500ms, webview does `GET /next` — if there's a command, runs it via `$se('runCode', cmd)`
5. Commands execute in Script Engine where `userfunctions.js` is available
6. To return data, code calls `reportResult(data)` which:
   - Encodes the payload with `encodeURIComponent`
   - Uses `window.webview.evaluateJavaScriptAsync` to inject `XMLHttpRequest` into the webview
   - The webview POSTs data to `http://127.0.0.1:54321/result`
7. Python retrieves the result via long-poll `GET /result`

### Bridge Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/next` | GET | Webview polls this. Returns queued JS command or empty. |
| `/ping` | GET | Health check. Returns "pong". |
| `/status` | GET | Connection status JSON. |
| `/result` | GET | Long-poll for result from PT (9s timeout). |
| `/result` | POST | PT posts execution results here (via webview). |
| `/queue` | POST | Python enqueues JS commands here. |

---

## Cable Types

From `PTBuilder/source/links.js`:

| Name | PTBuilder Key |
|------|---------------|
| Copper Straight-Through | `straight` |
| Copper Cross-Over | `cross` |
| Fiber | `fiber` |
| Console | `console` |
| Serial DCE | `serial` |

### Cable Rules

| Connection | Cable |
|-----------|-------|
| Router ↔ Switch | straight |
| Switch ↔ PC/Server/Laptop | straight |
| Switch ↔ AccessPoint | straight |
| Router ↔ Router | cross |
| Switch ↔ Switch | cross |
| Router ↔ Cloud | straight |
| Router ↔ PC (direct) | cross |

---

## Module Types

From `PTBuilder/source/modules.js`. Add with `addModule(device, slot, model)`.

| Module | Description |
|--------|-------------|
| HWIC-2T | 2 Serial ports (for routers) |
| HWIC-4ESW | 4 Ethernet switch ports |
| WIC-1AM | Analog modem |
| WIC-2T | 2 Serial ports (older) |
| WIC-1ENET | 1 Ethernet port |
| NM-1FE-TX | 1 FastEthernet |
| NM-2FE2W | 2 FastEthernet |
| HWIC-AP-AG-B | Wireless AP |

> Serial ports require HWIC-2T module. Slot compatibility varies by router model.

---

## Device Type IDs

Used internally by `lw.addDevice(typeId, model, x, y)`.

| ID | Category |
|----|----------|
| 0 | Router |
| 1 | Switch |
| 2 | Cloud |
| 3 | Bridge |
| 4 | Hub |
| 5 | Repeater |
| 7 | Access Point |
| 8 | PC |
| 9 | Server |
| 10 | Printer |
| 11 | Wireless Router |
| 16 | Multilayer Switch (3560, 3650) |
| 17 | Laptop (getType() return) |
| 18 | Laptop-PT (addDevice type) |
| 27 | ASA Firewall |

> Note: `device.getType()` may return a different ID than `allDeviceTypes[model]`.
> For example, Laptop-PT's addDevice type is 18, but getType() returns 17.

---

## Known Limitations

1. **No serial ports by default** — Routers 1941, 2901, 2911 ship with only
   GigabitEthernet ports. Serial requires HWIC-2T module insertion.

2. **HWIC-2T may not work on all slots** — `addModule("R1", 1, "HWIC-2T")`
   returned `false` on a 2911 during testing. Slot 0 may work on some models.

3. **Port methods not enumerable** — `for(var p in port)` returns empty.
   Methods exist but are not discoverable at runtime through iteration.

4. **Laptop-PT type ID discrepancy** — PTBuilder uses type 18 for addDevice,
   but `getType()` returns 17. `getDevices` filter uses type 17.

5. **No NAT, ACL, VLAN, STP** — The MCP server does not generate these
   configurations (yet).

6. **Cloud-PT ports are heterogeneous** — Serial0-3, Modem4-5, Ethernet6,
   Coaxial7. Only `Ethernet6` is usable for normal Ethernet connections.

7. **enterCommand mode detection** — Pass `""` as mode for auto-detection,
   `"global"` for global config, `"enable"` for privileged exec.

8. **One bootstrap per session** — The polling loop persists until PT is closed.
   No need to re-paste the bootstrap within the same PT session.
