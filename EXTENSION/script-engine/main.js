function builder() {
    this.m_builderUuid = "";
    this.errors = [];
}

builder.prototype.init = function () {
    var menu = ipc.appWindow().getMenuBar().getExtensionsPopupMenu();
    this.m_builderUuid = menu.insertItem("", "MCP BUILDER");
    var menuItem = menu.getMenuItemByUuid(this.m_builderUuid);
    menuItem.registerEvent("onClicked", this, this.menuClicked);
};

builder.prototype.cleanUp = function () {
    if (this.m_builderUuid != "") {
        var menu = ipc.appWindow().getMenuBar().getExtensionsPopupMenu();
        _ScriptModule.unregisterIpcEventByID("MenuItem", this.m_builderUuid, "onClicked", this, this.menuClicked);
        menu.removeItemUuid(this.m_builderUuid);
        this.m_builderUuid = "";
    }
};

builder.prototype.menuClicked = function (src, args) {
    window.show();
};

function startBridge() {
    window.show();
}

/*
 * Token shared with the MCP server.
 *
 * The bridge requires a token on every request: without it, any web page
 * open in the browser could queue JS that PT runs with new Function()
 * (a text/plain POST is a "simple" CORS request, so listening on
 * 127.0.0.1 did not prevent it).
 *
 * The webview has no access to the file system, but the Script Engine does,
 * via ipc.systemFileManager(). So the token is read from disk here and the
 * webview requests it with $se("getMcpToken"), which returns a Promise.
 * The user does nothing: they install the extension and it works.
 */
function mcpTokenCandidates() {
    var paths = [];
    try {
        // getUserFolder() -> "C:/Users/<user>/Cisco Packet Tracer 9.0.0"
        var uf = String(ipc.appWindow().getUserFolder());
        var home = uf.substring(0, uf.lastIndexOf("/"));
        if (home) {
            paths.push(home + "/AppData/Local/packet-tracer-mcp/bridge_token");
            paths.push(home + "/.local/state/packet-tracer-mcp/bridge_token");
            paths.push(home + "/.packet-tracer-mcp/bridge_token");
        }
    } catch (e) {}
    return paths;
}

function getMcpToken() {
    var fm;
    try {
        fm = ipc.systemFileManager();
    } catch (e) {
        return "";
    }
    var paths = mcpTokenCandidates();
    for (var i = 0; i < paths.length; i++) {
        try {
            if (fm.fileExists(paths[i])) {
                var raw = String(fm.getFileContents(paths[i]));
                // The file is written without a trailing newline, but it costs nothing
                // to tolerate spaces or a BOM if someone opened it with Notepad.
                return raw.replace(/^﻿/, "").replace(/^\s+|\s+$/g, "");
            }
        } catch (e) {}
    }
    return "";
}

/* Diagnostics for the UI: where it was searched, without revealing the token. */
function getMcpTokenInfo() {
    var paths = mcpTokenCandidates();
    var found = getMcpToken();
    return JSON.stringify({
        found: found.length > 0,
        length: found.length,
        searched: paths
    });
}

/* ==================================================================
 * FILE BRIDGE (works with the window CLOSED)
 *
 * The HTTP polling lives in the webview (the window). This channel lives in the
 * Script Engine, which runs whenever PT is open. The MCP server drops commands
 * as req_*.js files in the mailbox; they are executed here and the result is
 * returned as res_*.txt. It coexists with HTTP: the server picks a single
 * channel per command, so it never runs twice.
 *
 * It does not use XMLHttpRequest (the Script Engine does not have it): only
 * systemFileManager, which is available here.
 * ================================================================== */

var FILE_BRIDGE_TICK_FAST_MS = 250;   // there was recent activity
var FILE_BRIDGE_TICK_IDLE_MS = 1500;  // mailbox has been empty for a while
var FILE_BRIDGE_ORPHAN_S = 60;        // req/res older than this are purged
var _fileBridgeTimer = null;
var _fileBridgeDir = "";
var _fileBridgeCount = 0;   // commands executed by the file channel

/* State of the file channel, so the webview can display it. The file-bridge
   runs with the window open or closed; this tells the user they can close
   the window and PT will keep executing. */
function fileBridgeStatus() {
    return JSON.stringify({
        active: _fileBridgeTimer !== null,
        count: _fileBridgeCount,
        dir: _fileBridgeDir || mcpBridgeDir()
    });
}

function mcpBridgeDir() {
    // The mailbox lives next to the token: <token dir>/bridge.
    var paths = mcpTokenCandidates();
    for (var i = 0; i < paths.length; i++) {
        var base = paths[i].replace(/\/bridge_token$/, "");
        if (base !== paths[i]) return base + "/bridge";
    }
    return "";
}

/* Runs the JS of a req, capturing whatever it reports, without touching the
 * rest of the environment. Local reportResult(): the command calls it and its value goes to the res. */
function runFileBridgeCommand(js) {
    var captured = "";
    var report = function (d) { captured = String(d); };
    try {
        (new Function("reportResult", js))(report);
    } catch (e) {
        captured = "PT_ERROR: " + e;
    }
    return captured;
}

function fileBridgeTick() {
    var fm;
    try { fm = ipc.systemFileManager(); } catch (e) { return schedule(FILE_BRIDGE_TICK_IDLE_MS); }

    var dir = _fileBridgeDir || (_fileBridgeDir = mcpBridgeDir());
    if (!dir) return schedule(FILE_BRIDGE_TICK_IDLE_MS);

    try {
        if (!fm.directoryExists(dir)) { fm.makeDirectory(dir); }
        // Heartbeat: the server checks this file's date to know whether PT
        // (with the window closed) is still alive.
        fm.writePlainTextToFile(dir + "/alive.txt", String(Date.now()));
    } catch (e) {
        return schedule(FILE_BRIDGE_TICK_IDLE_MS);
    }

    var worked = false;
    var now = Math.floor(Date.now() / 1000);
    var files;
    try { files = fm.getFilesInDirectory(dir); } catch (e) { files = []; }

    for (var i = 0; i < files.length; i++) {
        var f = String(files[i]);
        if (f === "." || f === "..") continue;

        // Orphan purge: res with no owner (fire-and-forget or timeouts) and req
        // files that are too old and were never processed. Keeps the mailbox from growing.
        if (f.indexOf("res_") === 0 || f.indexOf("req_") === 0) {
            try {
                if (now - fm.getFileModificationTime(dir + "/" + f) > FILE_BRIDGE_ORPHAN_S) {
                    fm.removeFile(dir + "/" + f);
                    continue;
                }
            } catch (e) {}
        }
        if (f.indexOf("req_") !== 0 || f.slice(-3) !== ".js") continue;

        var name = f.substring(4, f.length - 3);   // req_<name>.js -> <name>
        var js;
        try { js = String(fm.getFileContents(dir + "/" + f)); } catch (e) { continue; }

        var result = runFileBridgeCommand(js);
        try { fm.writePlainTextToFile(dir + "/res_" + name + ".txt", result); } catch (e) {}
        try { fm.removeFile(dir + "/" + f); } catch (e) {}
        _fileBridgeCount++;
        worked = true;
    }

    schedule(worked ? FILE_BRIDGE_TICK_FAST_MS : FILE_BRIDGE_TICK_IDLE_MS);

    function schedule(ms) { _fileBridgeTimer = setTimeout(fileBridgeTick, ms); }
}

function startFileBridge() {
    if (_fileBridgeTimer) return;
    // Starts the loop; it reschedules itself depending on whether there is activity.
    fileBridgeTick();
}

/* The helpers the file channel needs (lwAddDevice/lwAddLink and the improved
 * versions of configurePcIp/addModule/etc.) are installed by installMcpHelpers()
 * at startup — see below. Both channels, HTTP and file, run the same versions,
 * with or without a window. */

/* ==================================================================
 * MCP HELPERS
 *
 * Improved versions of the helpers the MCP server needs. They used to be
 * injected over HTTP (runtime patches), so they only existed while the window
 * was open. They now live in the extension: available to BOTH channels
 * (HTTP and file), with or without a window.
 *
 * lwAddDevice / lwAddLink do not exist in userfunctions.js — they are required
 * to deploy a new topology. The others override the native ones with more
 * robust versions (e.g. configurePcIp, which does not hardcode FastEthernet0).
 *
 * GLOBAL is captured at file level, where `this` is the global object of the
 * Script Engine. installMcpHelpers() is called from main(), once the rest of the
 * files have loaded, so these versions win regardless of load order.
 * ================================================================== */

var GLOBAL = this;

function installMcpHelpers() {
    // addModule — power-cycle around the native addModule (some modules
    // fail if the device is powered on).
    GLOBAL.addModule = function (deviceName, slot, model) {
        var device = ipc.network().getDevice(deviceName);
        if (!device) { return false; }
        var hasPower = typeof device.getPower === "function" && typeof device.setPower === "function";
        var powerState = false;
        if (hasPower) { powerState = device.getPower(); device.setPower(false); }
        var moduleType = allModuleTypes[model];
        var result = device.addModule(slot, moduleType, model);
        if (hasPower && powerState) {
            device.setPower(true);
            if (typeof device.skipBoot === "function") { device.skipBoot(); }
        }
        if (result != true) { return false; }
        return true;
    };

    // lwAddDevice — creates the device in the Logical view (visible without save+reload).
    // The global addDevice writes to the model but PT generates an auto-name
    // (Router0, Switch1) that we rename to the requested one.
    GLOBAL.lwAddDevice = function (name, deviceType, model, x, y) {
        var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
        var autoName = lw.addDevice(deviceType, model, x, y);
        if (autoName && autoName !== name) {
            var d = ipc.network().getDevice(autoName);
            if (d && typeof d.setName === "function") { d.setName(name); }
        }
        // Fallback: lw.addDevice fails silently for some models
        // (Laptop-PT returns "" and creates nothing). If it is still not findable, we use
        // the global addDevice, which resolves by MODEL NAME.
        if (!ipc.network().getDevice(name)) {
            try { addDevice(name, model, x, y); } catch (e) {}
        }
        return name;
    };

    // lwAddLink — creates the link in the Logical view. Cable as a string or an int enum.
    GLOBAL.lwAddLink = function (d1, p1, d2, p2, cable) {
        var CT = {
            straight: 8100, cross: 8101, crossover: 8101, roll: 8102, fiber: 8103,
            phone: 8104, cable: 8105, serial: 8106, auto: 8107, console: 8108,
            wireless: 8109, coaxial: 8110, octal: 8111, cellular: 8112, usb: 8113,
            custom_io: 8114
        };
        var t = (typeof cable === "number") ? cable : (CT[(cable || "auto").toLowerCase()] || 8107);
        var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
        return lw.createLink(d1, p1, d2, p2, t);
    };

    // configurePcIp — does not hardcode FastEthernet0: it looks for the first ethernet port
    // (or Wireless0) by iterating getPorts(). Works with PC/Server/Laptop.
    GLOBAL.configurePcIp = function (deviceName, dhcpEnabled, ipaddress, subnetMask, defaultGateway, dnsServer) {
        var device = ipc.network().getDevice(deviceName);
        if (!device) { return false; }
        var port = null;
        if (typeof device.getPorts === "function") {
            var ports = device.getPorts();
            for (var i = 0; i < ports.length; i++) {
                var pn = ports[i];
                if (typeof pn !== "string") { continue; }
                if (pn.indexOf("Ethernet") >= 0 || pn === "Wireless0") {
                    var p = device.getPort(pn);
                    if (p) { port = p; break; }
                }
            }
        }
        if (!port) { port = device.getPort("FastEthernet0"); }
        if (!port) { return false; }
        if (dhcpEnabled === true || dhcpEnabled === false) {
            if (typeof device.setDhcpFlag === "function") { device.setDhcpFlag(dhcpEnabled); }
        }
        if (ipaddress && subnetMask) { port.setIpSubnetMask(ipaddress, subnetMask); }
        if (defaultGateway) {
            if (typeof device.setDefaultGateway === "function") { device.setDefaultGateway(defaultGateway); }
            else if (typeof port.setDefaultGateway === "function") { port.setDefaultGateway(defaultGateway); }
        }
        if (dnsServer && typeof port.setDnsServerIp === "function") { port.setDnsServerIp(dnsServer); }
        return true;
    };

    // configurePcIpv6 — IPv6 + SLAAC (auto-config via RA) on the host's first
    // ethernet/Wireless0 port. addIpv6Address fails on HostPort.
    GLOBAL.configurePcIpv6 = function (deviceName) {
        var device = ipc.network().getDevice(deviceName);
        if (!device) { return false; }
        if (typeof device.getPorts !== "function") { return false; }
        var ports = device.getPorts();
        for (var i = 0; i < ports.length; i++) {
            var pn = ports[i];
            if (typeof pn !== "string") { continue; }
            if (pn.indexOf("Ethernet") >= 0 || pn === "Wireless0") {
                var p = device.getPort(pn);
                if (p) {
                    if (typeof p.setIpv6Enabled === "function") { p.setIpv6Enabled(true); }
                    if (typeof p.setIpv6AddressAutoConfig === "function") { p.setIpv6AddressAutoConfig(true); }
                    return true;
                }
            }
        }
        return false;
    };

    // swapLaptopToWireless — replaces a laptop's ethernet NIC with a wireless one
    // (slot "0" -> PT-LAPTOP-NM-1W) so it has Wireless0 and
    // auto-associates with an AP by its default SSID.
    GLOBAL.swapLaptopToWireless = function (deviceName) {
        var device = ipc.network().getDevice(deviceName);
        if (!device) { return false; }
        var hasPower = typeof device.getPower === "function" && typeof device.setPower === "function";
        if (hasPower) { device.setPower(false); }
        try { device.removeModule("0"); } catch (e) {}
        var result = device.addModule("0", allModuleTypes["PT-LAPTOP-NM-1W"], "PT-LAPTOP-NM-1W");
        if (hasPower) {
            device.setPower(true);
            if (typeof device.skipBoot === "function") { device.skipBoot(); }
        }
        return result == true;
    };
}

function main() {
    builder = new builder();
    builder.init();
    window = new htmlWindow();
    // Installs the improved helpers before starting any channel, so that
    // HTTP and file run exactly the same versions.
    installMcpHelpers();
    startBridge();
    // The file bridge runs independently of the window.
    startFileBridge();
}

function cleanUp() {
    builder.cleanUp();
    if (_fileBridgeTimer) { clearTimeout(_fileBridgeTimer); _fileBridgeTimer = null; }
}
