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

/* PENDIENTE (requiere probar con esta V5 + PT abierto):
 * Los comandos interactivos (query, configureIosDevice, getCommandPrompt, save/
 * open, verify) usan funciones NATIVAS de userfunctions.js, asi que ya funcionan
 * por este canal. Pero pt_live_deploy de una topologia NUEVA usa lwAddDevice /
 * lwAddLink, que hoy los inyecta el servidor por HTTP (runtime patches) y NO
 * estan en userfunctions.js. Por el canal de archivo esas dos funciones faltan.
 * Fix pendiente: definir lwAddDevice/lwAddLink/swapLaptopToWireless/
 * configurePcIpv6 en el scope global del Script Engine (aca, codigo propio, sin
 * tocar userfunctions.js), para que ambos canales las tengan y el servidor deje
 * de inyectarlas. Portar desde _RUNTIME_PATCHES_JS del tool_registry. */

function main() {
    builder = new builder();
    builder.init();
    window = new htmlWindow();
    startBridge();
    // El bridge por archivo corre independiente de la ventana.
    startFileBridge();
}

function cleanUp() {
    builder.cleanUp();
    if (_fileBridgeTimer) { clearTimeout(_fileBridgeTimer); _fileBridgeTimer = null; }
}
