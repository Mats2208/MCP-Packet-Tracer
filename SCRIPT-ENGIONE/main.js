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
 * Token compartido con el servidor MCP.
 *
 * El bridge exige un token en cada peticion: sin el, cualquier pagina web
 * abierta en el navegador podia encolar JS que PT ejecuta con new Function()
 * (un POST text/plain es una peticion CORS "simple", asi que escuchar en
 * 127.0.0.1 no lo impedia).
 *
 * El webview no tiene acceso al sistema de archivos, pero el Script Engine si,
 * via ipc.systemFileManager(). Asi que el token se lee de disco aca y el
 * webview lo pide con $se("getMcpToken"), que devuelve una Promise.
 * El usuario no hace nada: instala la extension y funciona.
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
                // El archivo se escribe sin salto final, pero no cuesta nada
                // tolerar espacios o un BOM si alguien lo abrio con el Notepad.
                return raw.replace(/^﻿/, "").replace(/^\s+|\s+$/g, "");
            }
        } catch (e) {}
    }
    return "";
}

/* Diagnostico para la UI: donde se busco, sin revelar el token. */
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
 * BRIDGE POR ARCHIVO (funciona con la ventana CERRADA)
 *
 * El polling HTTP vive en el webview (la ventana). Este canal vive en el
 * Script Engine, que corre siempre que PT esta abierto. El servidor MCP deja
 * comandos como archivos req_*.js en el buzon; aca se ejecutan y se devuelve el
 * resultado como res_*.txt. Coexiste con el HTTP: el servidor elige un solo
 * canal por comando, asi que nunca se ejecuta dos veces.
 *
 * No usa XMLHttpRequest (el Script Engine no lo tiene): solo systemFileManager,
 * que si esta disponible aca.
 * ================================================================== */

var FILE_BRIDGE_TICK_FAST_MS = 250;   // hubo actividad reciente
var FILE_BRIDGE_TICK_IDLE_MS = 1500;  // buzon vacio hace rato
var FILE_BRIDGE_ORPHAN_S = 60;        // req/res mas viejos que esto se purgan
var _fileBridgeTimer = null;
var _fileBridgeDir = "";
var _fileBridgeCount = 0;   // comandos ejecutados por el canal de archivo

/* Estado del canal de archivo, para que el webview lo muestre. El file-bridge
   corre con la ventana abierta o cerrada; esto le dice al usuario que puede
   cerrar la ventana y PT va a seguir ejecutando. */
function fileBridgeStatus() {
    return JSON.stringify({
        active: _fileBridgeTimer !== null,
        count: _fileBridgeCount,
        dir: _fileBridgeDir || mcpBridgeDir()
    });
}

function mcpBridgeDir() {
    // El buzon vive junto al token: <dir del token>/bridge.
    var paths = mcpTokenCandidates();
    for (var i = 0; i < paths.length; i++) {
        var base = paths[i].replace(/\/bridge_token$/, "");
        if (base !== paths[i]) return base + "/bridge";
    }
    return "";
}

/* Ejecuta el JS de un req capturando lo que reporte, sin tocar el resto del
 * entorno. reportResult() local: el comando la llama y su valor va al res. */
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
        // Heartbeat: el servidor mira la fecha de este archivo para saber si PT
        // (con la ventana cerrada) sigue vivo.
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

        // Purga de huerfanos: res sin dueno (fire-and-forget o timeouts) y req
        // muy viejos que quedaron sin procesar. Evita que el buzon crezca.
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
    // Arranca el loop; se auto-reprograma segun haya o no actividad.
    fileBridgeTick();
}

/* Los helpers que el canal de archivo necesita (lwAddDevice/lwAddLink y las
 * versiones mejoradas de configurePcIp/addModule/etc.) los instala
 * installMcpHelpers() al arrancar — ver mas abajo. Ambos canales, HTTP y
 * archivo, ejecutan las mismas versiones, con o sin ventana. */

/* ==================================================================
 * MCP HELPERS
 *
 * Versiones mejoradas de los helpers que el servidor MCP necesita. Antes las
 * inyectaba por HTTP (runtime patches), asi que solo existian cuando la ventana
 * estaba abierta. Aca viven en la extension: disponibles para AMBOS canales
 * (HTTP y archivo), con o sin ventana.
 *
 * lwAddDevice / lwAddLink no existen en userfunctions.js — son necesarias para
 * desplegar una topologia nueva. Las demas sobreescriben a las nativas con
 * versiones mas robustas (p.ej. configurePcIp que no hardcodea FastEthernet0).
 *
 * GLOBAL se captura a nivel de archivo, donde `this` es el objeto global del
 * Script Engine. installMcpHelpers() se llama desde main(), cuando el resto de
 * archivos ya cargo, de modo que estas versiones ganan sin importar el orden.
 * ================================================================== */

var GLOBAL = this;

function installMcpHelpers() {
    // addModule — power-cycle alrededor de addModule nativo (algunos modulos
    // fallan si el device esta encendido).
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

    // lwAddDevice — crea el device en la vista Logica (visible sin save+reload).
    // El addDevice global escribe al modelo pero PT genera un auto-nombre
    // (Router0, Switch1) que renombramos al solicitado.
    GLOBAL.lwAddDevice = function (name, deviceType, model, x, y) {
        var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace();
        var autoName = lw.addDevice(deviceType, model, x, y);
        if (autoName && autoName !== name) {
            var d = ipc.network().getDevice(autoName);
            if (d && typeof d.setName === "function") { d.setName(name); }
        }
        // Fallback: lw.addDevice falla en silencio para algunos modelos
        // (Laptop-PT devuelve "" y no crea nada). Si no quedo findable, usamos
        // el addDevice global, que resuelve por NOMBRE de modelo.
        if (!ipc.network().getDevice(name)) {
            try { addDevice(name, model, x, y); } catch (e) {}
        }
        return name;
    };

    // lwAddLink — crea el link en la vista Logica. Cable como string o enum int.
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

    // configurePcIp — no hardcodea FastEthernet0: busca el primer puerto ethernet
    // (o Wireless0) iterando getPorts(). Funciona con PC/Server/Laptop.
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

    // configurePcIpv6 — IPv6 + SLAAC (auto-config por RA) en el primer puerto
    // ethernet/Wireless0 del host. addIpv6Address falla en HostPort.
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

    // swapLaptopToWireless — cambia el NIC ethernet de una laptop por uno
    // inalambrico (slot "0" -> PT-LAPTOP-NM-1W) para que tenga Wireless0 y
    // auto-asocie a un AP por SSID default.
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
    // Instala los helpers mejorados antes de arrancar cualquier canal, para que
    // HTTP y archivo ejecuten exactamente las mismas versiones.
    installMcpHelpers();
    startBridge();
    // El bridge por archivo corre independiente de la ventana.
    startFileBridge();
}

function cleanUp() {
    builder.cleanUp();
    if (_fileBridgeTimer) { clearTimeout(_fileBridgeTimer); _fileBridgeTimer = null; }
}
