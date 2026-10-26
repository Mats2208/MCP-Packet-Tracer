/*
 * core.js — the Script Engine runtime the MCP server talks to.
 *
 * This file replaces the PTBuilder runtime the extension used to be built with. The
 * global names and signatures are kept exactly (addDevice, addLink, configureIosDevice,
 * getDevices, runCode, htmlWindow, allModuleTypes, ...) because the server and
 * main.js call them by name; the implementation is this project's own, written
 * against Packet Tracer's IpcAPI. Licensed MIT with the rest of the repo.
 *
 * main.js loads after this file and overrides some of them with richer versions
 * (configurePcIp, addModule) inside installMcpHelpers().
 */

/* Device types that boot IOS: skipping the boot makes the CLI usable at once. */
function isIosDeviceType(type) {
    return type === 0 || type === 1 || type === 16; // router, switch, multilayer switch
}

function skipBootIfIos(device) {
    if (isIosDeviceType(device.getType()) && typeof device.skipBoot === "function") {
        device.skipBoot();
    }
}

/*
 * Creates `model` at (x, y) and names it `deviceName`. PT names new devices itself
 * (Router0, Switch1), so the device is renamed right after. Returns true on success.
 */
function addDevice(deviceName, deviceModel, x, y) {
    var type = allDeviceTypes[deviceModel];
    if (type === undefined) { return false; }
    var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
    var autoName = lw.addDevice(type, deviceModel, x, y);
    if (!autoName) { return false; }
    var device = ipc.network().getDevice(autoName);
    if (!device) { return false; }
    if (autoName !== deviceName) { device.setName(deviceName); }
    skipBootIfIos(device);
    return true;
}

/* Cables a pair of ports. `linkType` is a name ("straight", "cross", ...) or a PT link-type number. */
function addLink(device1Name, device1Interface, device2Name, device2Interface, linkType) {
    var type = (typeof linkType === "number") ? linkType : allLinkTypes[String(linkType || "auto").toLowerCase()];
    if (type === undefined) { return false; }
    var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
    return lw.createLink(device1Name, device1Interface, device2Name, device2Interface, type) === true;
}

/*
 * Types an IOS configuration into a router or switch: one command per line, entered
 * from global configuration mode, then saved with `write memory`.
 */
function configureIosDevice(deviceName, commands) {
    var device = ipc.network().getDevice(deviceName);
    if (!device) { return false; }
    skipBootIfIos(device);
    var lines = String(commands || "").split("\n");
    device.enterCommand("!", "global");
    for (var i = 0; i < lines.length; i++) {
        device.enterCommand(lines[i], "");
    }
    device.enterCommand("write memory", "enable");
    return true;
}

/* Lower-case type names accepted by getDevices() as a filter. Ids measured on PT 9.0.1
   (2026-10-25) with getType(); PTBuilder's list was off from laptop (18, not 17) onwards. */
var deviceTypes = {
    router: 0, switch: 1, cloud: 2, bridge: 3, hub: 4, repeater: 5, coaxialsplitter: 6,
    accesspoint: 7, pc: 8, server: 9, printer: 10, wirelessrouter: 11, ipphone: 12,
    dslmodem: 13, cablemodem: 14, remotenetwork: 15, multilayerswitch: 16, laptop: 18,
    tabletpc: 19, smartphone: 20, wirelessenddevice: 21, wiredenddevice: 22, tv: 23,
    homevoip: 24, analogphone: 25, asa: 27, dlc: 29, homerouter: 30, celltower: 31,
    centralofficeserver: 32, sniffer: 35, mcu: 36, sbc: 37, thing: 39, wlc: 41,
    lightweightaccesspoint: 44, powerdistribution: 45, patchpanel: 46, wallmount: 47,
    merakisecurity: 48, merakiserver: 49, networkcontroller: 50
};

/* For the panel's Tools: what is on the canvas, as JSON (devices by type, links). */
function mcpCanvasSummary() {
    var names = {};
    for (var k in deviceTypes) { names[deviceTypes[k]] = k; }
    var net = ipc.network();
    var byType = {};
    var shown = 0;
    for (var i = 0; i < net.getDeviceCount(); i++) {
        var t = net.getDeviceAt(i).getType();
        if (t === deviceTypes.powerdistribution) { continue; } // PT's own, off-canvas
        var label = names[t] || ("type " + t);
        byType[label] = (byType[label] || 0) + 1;
        shown++;
    }
    return JSON.stringify({ devices: shown, links: net.getLinkCount(), byType: byType });
}

/*
 * Names of the devices on the canvas. `filter`: a type name, a type number, or an
 * array of them; `startsWith`: a name prefix. Both optional.
 */
function getDevices(filter, startsWith) {
    var wanted = null;
    if (filter !== undefined && filter !== null && filter !== "") {
        var list = (filter instanceof Array) ? filter : [filter];
        wanted = [];
        for (var i = 0; i < list.length; i++) {
            wanted.push(typeof list[i] === "string" ? deviceTypes[list[i].toLowerCase()] : list[i]);
        }
    }
    var prefix = startsWith || "";
    var net = ipc.network();
    var names = [];
    for (var j = 0; j < net.getDeviceCount(); j++) {
        var d = net.getDeviceAt(j);
        var name = d.getName();
        if ((!wanted || wanted.indexOf(d.getType()) !== -1) && name.indexOf(prefix) === 0) {
            names.push(name);
        }
    }
    return names;
}

/*
 * Runs a block of JavaScript in the Script Engine. Returns true when it ran, false
 * when it threw. Errors never open a dialog: a modal freezes Packet Tracer until a
 * human clicks it, which is fatal for an agent driving it. The message is kept in
 * runCodeLastError for the panel to show.
 */
var runCodeLastError = "";

function runCode(scriptText) {
    try {
        (new Function(scriptText))();
        runCodeLastError = "";
        return true;
    } catch (e) {
        runCodeLastError = String(e) + (e && e.lineNumber ? " (line " + e.lineNumber + ")" : "");
        return false;
    }
}

/* The MCP Control Center window (webview/index.html). One instance, reopened on demand. */
function htmlWindow() {
    this.webview = null;
    this.webviewId = "";
}

htmlWindow.prototype.show = function () {
    if (!this.webviewId || webViewManager.getWebView(this.webviewId) == null) {
        this.webview = webViewManager.createWebView("MCP Control Center", "this-sm:index.html", 800, 500);
        this.webviewId = this.webview.getWebViewId();
        this.webview.registerEvent("closed", this, this.windowClosed);
        this.webview.setMinimumWidth(400);
        this.webview.setMinimumHeight(300);
    }
    this.webview.hide();
    this.webview.show();
};

htmlWindow.prototype.windowClosed = function (src, args) {
    this.webview.unregisterEvent("closed", this, this.windowClosed);
    this.webviewId = "";
};

// BEGIN GENERATED TABLES (python -m src.packet_tracer_mcp.devtools.ext_tables)

var allDeviceTypes = {
    "1841": 0,
    "1941": 0,
    "2620XM": 0,
    "2621XM": 0,
    "2811": 0,
    "2901": 0,
    "2911": 0,
    "2950-24": 1,
    "2950T-24": 1,
    "2960-24TT": 1,
    "3560-24PS": 16,
    "3650-24PS": 16,
    "3702i": 44,
    "5505": 27,
    "5506-X": 27,
    "7960": 12,
    "819HG-4G-IOX": 0,
    "819HGW": 0,
    "829": 0,
    "AccessPoint-PT": 7,
    "AccessPoint-PT-A": 7,
    "AccessPoint-PT-AC": 7,
    "AccessPoint-PT-N": 7,
    "Analog-Phone-PT": 25,
    "Bridge-PT": 3,
    "CGR1240": 0,
    "Cable-Modem-PT": 14,
    "Cell-Tower": 31,
    "Central-Office-Server": 32,
    "Cloud-PT": 2,
    "Cloud-PT-Empty": 2,
    "CoAxialSplitter-PT": 6,
    "Copper Patch Panel": 46,
    "Copper Wall Mount": 47,
    "DLC100": 29,
    "DSL-Modem-PT": 13,
    "Fiber Patch Panel": 46,
    "Fiber Wall Mount": 47,
    "Home-VoIP-PT": 24,
    "HomeRouter-PT-AC": 30,
    "Hub-PT": 4,
    "IE-2000": 16,
    "ISR4321": 0,
    "ISR4331": 0,
    "LAP-PT": 44,
    "Laptop-PT": 18,
    "Linksys-WRT300N": 11,
    "MCU-PT": 36,
    "Meraki-MX65W": 48,
    "Meraki-Server": 49,
    "NetworkController": 50,
    "PC-PT": 8,
    "Printer-PT": 10,
    "Repeater-PT": 5,
    "Router-PT": 0,
    "Router-PT-Empty": 0,
    "SBC-PT": 37,
    "SMARTPHONE-PT": 20,
    "Server-PT": 9,
    "Sniffer": 35,
    "Switch-PT": 1,
    "Switch-PT-Empty": 1,
    "TV-PT": 23,
    "TabletPC-PT": 19,
    "Thing": 39,
    "WLC-2504": 41,
    "WLC-3504": 41,
    "WLC-PT": 41,
    "WiredEndDevice-PT": 22,
    "WirelessEndDevice-PT": 21
};

var allModuleTypes = {
    "1240-Cellular": 2,
    "1240-Cover": 2,
    "AC-POWER-SUPPLY": 4,
    "ACCESS_POINT_POWER_ADAPTER": 31,
    "ASA-Cover": 19,
    "C3650-BUILTIN": 32,
    "C3650-SFP-BUILTIN": 32,
    "GLC-FE-100FX-RGD": 30,
    "GLC-GE-100FX": 30,
    "GLC-LH-SMD": 30,
    "GLC-T": 30,
    "GLC-TE": 30,
    "HWIC-1GE-SFP": 2,
    "HWIC-2T": 2,
    "HWIC-4ESW": 2,
    "HWIC-8A": 2,
    "HWIC-AP-AG-B": 2,
    "IP_PHONE_POWER_ADAPTER": 11,
    "ISR4321-BUILTIN": 32,
    "ISR4331-BUILTIN": 32,
    "Linksys-WMP300N": 7,
    "Linksys-WPC300N": 9,
    "MERAKI-POWER-ADAPTER": 34,
    "NIM-2T": 2,
    "NIM-Cover": 2,
    "NIM-ES2-4": 2,
    "NM-1E": 1,
    "NM-1E2W": 1,
    "NM-1FE-FX": 1,
    "NM-1FE-TX": 1,
    "NM-1FE2W": 1,
    "NM-2E2W": 1,
    "NM-2FE2W": 1,
    "NM-2W": 1,
    "NM-4A/S": 1,
    "NM-4E": 1,
    "NM-8A/S": 1,
    "NM-8AM": 1,
    "NM-Cover": 1,
    "NM-ESW-161": 1,
    "POWER-COVER-PLATE": 4,
    "PT-CELL-NM-1CX": 21,
    "PT-CELL-NM-3G/4G": 21,
    "PT-CLOUD-NM-1AM": 5,
    "PT-CLOUD-NM-1CE": 5,
    "PT-CLOUD-NM-1CFE": 5,
    "PT-CLOUD-NM-1CGE": 5,
    "PT-CLOUD-NM-1CX": 10,
    "PT-CLOUD-NM-1FFE": 5,
    "PT-CLOUD-NM-1FGE": 5,
    "PT-CLOUD-NM-1S": 5,
    "PT-CONTROLLER-BUILTIN": 32,
    "PT-HEADPHONE": 16,
    "PT-HOST-NM-1AM": 7,
    "PT-HOST-NM-1CE": 7,
    "PT-HOST-NM-1CFE": 7,
    "PT-HOST-NM-1CGE": 7,
    "PT-HOST-NM-1FFE": 7,
    "PT-HOST-NM-1FGE": 7,
    "PT-HOST-NM-1W": 7,
    "PT-HOST-NM-1W-A": 7,
    "PT-HOST-NM-1W-AC": 7,
    "PT-HOST-NM-3G/4G": 7,
    "PT-HOST-NM-COVER": 7,
    "PT-IOT-CUSTOM-IO": 26,
    "PT-IOT-NM-1CE": 23,
    "PT-IOT-NM-1CFE": 23,
    "PT-IOT-NM-1CGE": 23,
    "PT-IOT-NM-1W": 23,
    "PT-IOT-NM-1W-AC": 23,
    "PT-IOT-NM-3G/4G": 23,
    "PT-IOT-POWER-ADAPTER": 27,
    "PT-LAPTOP-NM-1AM": 9,
    "PT-LAPTOP-NM-1CE": 9,
    "PT-LAPTOP-NM-1CFE": 9,
    "PT-LAPTOP-NM-1CGE": 9,
    "PT-LAPTOP-NM-1FFE": 9,
    "PT-LAPTOP-NM-1FGE": 9,
    "PT-LAPTOP-NM-1W": 9,
    "PT-LAPTOP-NM-1W-A": 9,
    "PT-LAPTOP-NM-1W-AC": 9,
    "PT-LAPTOP-NM-3G/4G": 9,
    "PT-MICROPHONE": 16,
    "PT-MODEM-NM-1CE": 8,
    "PT-MODEM-NM-1CFE": 8,
    "PT-MODEM-NM-1CGE": 8,
    "PT-PDA-NM-1AM": 13,
    "PT-PDA-NM-1CE": 13,
    "PT-PDA-NM-1CFE": 13,
    "PT-PDA-NM-1CGE": 13,
    "PT-PDA-NM-1FFE": 13,
    "PT-PDA-NM-1FGE": 13,
    "PT-PDA-NM-1W": 13,
    "PT-PDA-NM-1W-A": 13,
    "PT-PDA-NM-1W-AC": 13,
    "PT-PDA-NM-3G/4G": 13,
    "PT-REPEATER-NM-1CE": 6,
    "PT-REPEATER-NM-1CFE": 6,
    "PT-REPEATER-NM-1CGE": 6,
    "PT-REPEATER-NM-1FFE": 6,
    "PT-REPEATER-NM-1FGE": 6,
    "PT-REPEATER-NM-COVER": 6,
    "PT-ROUTER-NM-1AM": 3,
    "PT-ROUTER-NM-1CE": 3,
    "PT-ROUTER-NM-1CFE": 3,
    "PT-ROUTER-NM-1CGE": 3,
    "PT-ROUTER-NM-1FFE": 3,
    "PT-ROUTER-NM-1FGE": 3,
    "PT-ROUTER-NM-1S": 3,
    "PT-ROUTER-NM-1SS": 3,
    "PT-ROUTER-NM-COVER": 3,
    "PT-SWITCH-NM-1CE": 4,
    "PT-SWITCH-NM-1CFE": 4,
    "PT-SWITCH-NM-1CGE": 4,
    "PT-SWITCH-NM-1FFE": 4,
    "PT-SWITCH-NM-1FGE": 4,
    "PT-SWITCH-NM-COVER": 4,
    "PT-TABLETPC-NM-1AM": 12,
    "PT-TABLETPC-NM-1CE": 12,
    "PT-TABLETPC-NM-1CFE": 12,
    "PT-TABLETPC-NM-1CGE": 12,
    "PT-TABLETPC-NM-1FFE": 12,
    "PT-TABLETPC-NM-1FGE": 12,
    "PT-TABLETPC-NM-1W": 12,
    "PT-TABLETPC-NM-1W-A": 12,
    "PT-TABLETPC-NM-1W-AC": 12,
    "PT-TABLETPC-NM-3G": 12,
    "PT-UNV-PWR-ADAPTER": 28,
    "PT-WIREDENDDEVICE-NM-1CE": 15,
    "PT-WIREDENDDEVICE-NM-1CFE": 15,
    "PT-WIREDENDDEVICE-NM-1CGE": 15,
    "PT-WIREDENDDEVICE-NM-1FFE": 15,
    "PT-WIREDENDDEVICE-NM-1FGE": 15,
    "PT-WIREDENDDEVICE-NM-1W": 15,
    "PT-WIREDENDDEVICE-NM-1W-A": 15,
    "PT-WIRELESSENDDEVICE-NM-1CE": 14,
    "PT-WIRELESSENDDEVICE-NM-1CFE": 14,
    "PT-WIRELESSENDDEVICE-NM-1CGE": 14,
    "PT-WIRELESSENDDEVICE-NM-1FFE": 14,
    "PT-WIRELESSENDDEVICE-NM-1FGE": 14,
    "PT-WIRELESSENDDEVICE-NM-1W": 14,
    "PT-WIRELESSENDDEVICE-NM-1W-A": 14,
    "PT-WIRELESSENDDEVICE-NM-1W-AC": 14,
    "ROUTER-ADAPTER": 29,
    "WIC-1AM": 2,
    "WIC-1ENET": 2,
    "WIC-1T": 2,
    "WIC-2AM": 2,
    "WIC-2T": 2,
    "WIC-Cover": 2
};

var allLinkTypes = {
    "auto": 8107,
    "cable": 8105,
    "cellular": 8112,
    "coaxial": 8110,
    "console": 8108,
    "cross": 8101,
    "crossover": 8101,
    "custom_io": 8114,
    "fiber": 8103,
    "octal": 8111,
    "phone": 8104,
    "roll": 8102,
    "serial": 8106,
    "straight": 8100,
    "usb": 8113,
    "wireless": 8109
};

// END GENERATED TABLES
