// ============================================================================
// PT-MCP Builder Interface v4
// Premium Control Center — Editor, Terminal, Status Dashboard, Quick Build
// ============================================================================

// ------------------------------------------------------------------ State ---

var S = {
    // Connection
    bridgeUp:      false,
    ptConnected:   false,
    lastPollAgo:   null,
    commandCount:  0,
    lastActivity:  null,
    sessionStart:  Date.now(),

    // UI
    activeTab:     "editor",
    errCount:      0,

    // Logs
    logs:          [],       // { ts, type, msg } objects
    logFilter:     "all",
    maxLogs:       500,

    // History
    history:       [],       // string[] — ring buffer of 20 scripts
    historyPos:    -1,       // navigator position

    // Sparkline
    pollTimes:     [],       // timestamps of successful polls (last 20)

    // Editor auto-save debounce
    _saveTimer:    null,
};

var BRIDGE_BASE = "http://127.0.0.1:54321";

// Token compartido con el servidor MCP. Este webview corre bajo el esquema
// this-sm: y no tiene acceso al sistema de archivos, así que no puede leer el
// token del disco: lo pide una vez por POST /pair y lo guarda con $putData,
// que sobrevive a reinicios de Packet Tracer.
var S_TOKEN = "";

function bridgeUrl(path) {
    return BRIDGE_BASE + path + (S_TOKEN ? "?t=" + encodeURIComponent(S_TOKEN) : "");
}

function bootstrapSnippet() {
    var q = encodeURIComponent(S_TOKEN);
    return '/* PT-MCP Bridge */ window.webview.evaluateJavaScriptAsync(' +
        '"setInterval(function(){var x=new XMLHttpRequest();' +
        "x.open('GET','http://127.0.0.1:54321/next?t=" + q + "',true);" +
        'x.onload=function(){if(x.status===200&&x.responseText)' +
        // try/catch: sin él, un error dentro de runCode abre un modal de PT que
        // congela el webview y mata el polling. Las otras copias del bootstrap
        // ya lo tenían; esta se había quedado atrás.
        "{try{$se('runCode',x.responseText)}catch(e){}}};x.onerror=function(){};" +
        'x.send()},500)");';
}

function refreshBootstrapSnippet() {
    var el = document.getElementById("bootstrapSnippet");
    if (el) el.textContent = bootstrapSnippet();
}

/* Carga el token desde el Script Engine, que lo lee del disco.
   El webview no puede tocar archivos; el Script Engine si. $se() devuelve una
   Promise. No hay emparejamiento ni accion del usuario: la extension arranca y
   funciona. */
function loadToken(onDone) {
    try {
        $se("getMcpToken").then(function(tok) {
            if (tok) {
                S_TOKEN = String(tok);
                refreshBootstrapSnippet();
                log("MCP token loaded — bridge authenticated", "ok");
                if (onDone) onDone(true);
            } else {
                reportMissingToken();
                if (onDone) onDone(false);
            }
        }).catch(function(e) {
            log("Could not read the MCP token: " + e, "err");
            if (onDone) onDone(false);
        });
    } catch(e) {
        log("Script Engine unavailable — cannot read the MCP token", "err");
        if (onDone) onDone(false);
    }
}

/* Sin token el bridge devuelve 401 en todo. Decir DONDE se busco, porque la
   causa casi siempre es que el servidor MCP nunca corrio en esta maquina. */
function reportMissingToken() {
    log("No MCP token found on this machine.", "err");
    log("Start the MCP server once (it creates the token on first run), " +
        "then reopen this window.", "warn");
    try {
        $se("getMcpTokenInfo").then(function(info) {
            var d = JSON.parse(info);
            (d.searched || []).forEach(function(p) { log("  looked in: " + p, "info"); });
        }).catch(function() {});
    } catch(e) {}
}

// ------------------------------------------------------------------ Init ---

function init() {
    // El token se lee del disco a través del Script Engine en cada arranque.
    // No se cachea con $putData a propósito: si el servidor MCP lo rota, la
    // copia cacheada quedaría obsoleta y el fallo sería invisible.
    loadToken();
    refreshBootstrapSnippet();

    // Load persisted code
    try {
        $getData("code").then(function(code) {
            if (code) document.getElementById("codeeditor").value = code;
        }).catch(function() {});
    } catch(e) {}

    // Load history
    try {
        $getData("history").then(function(h) {
            if (h) {
                S.history = JSON.parse(h);
                refreshHistoryDropdown();
            }
        }).catch(function() {});
    } catch(e) {}

    log("PT-MCP Control Center started", "info");
    log("Polling bridge at http://127.0.0.1:54321 …", "info");

    // Update uptime every second
    setInterval(updateUptime, 1000);

    // Poll bridge every 2s
    setInterval(pollBridgeStatus, 2000);
    pollBridgeStatus();

    // Poll for commands every 500ms
    setInterval(pollCommands, 500);

    updateQBPreview();
}

// ------------------------------------------------------------------ Tabs ---

function switchTab(name) {
    S.activeTab = name;

    document.querySelectorAll(".tab-btn").forEach(function(b) {
        b.classList.toggle("active", b.id === "tab-" + name);
    });
    document.querySelectorAll(".pane").forEach(function(p) {
        p.classList.toggle("active", p.id === "pane-" + name);
    });

    // Show/hide editor toolbar
    var tb = document.getElementById("editor-toolbar");
    if (tb) tb.style.display = (name === "editor") ? "flex" : "none";

    // When switching to terminal, ensure log is rendered
    if (name === "terminal") renderLog();
}

// ---------------------------------------------------------------- Logging --

var TYPE_LABELS = {
    info: "INFO",
    ok:   " OK ",
    warn: "WARN",
    err:  " ERR",
    cmd:  " CMD",
    recv: "RECV",
};

function log(message, type) {
    type = type || "info";
    var now = new Date();
    var ts = now.toLocaleTimeString("en-GB", { hour12: false });

    S.logs.push({ ts: ts, type: type, msg: message });

    // Trim
    if (S.logs.length > S.maxLogs) S.logs.splice(0, S.logs.length - S.maxLogs);

    S.lastActivity = ts;
    setElText("st-last", ts);

    if (type === "err") {
        S.errCount++;
        var badge = document.getElementById("errBadge");
        if (badge) {
            badge.textContent = S.errCount > 99 ? "99+" : S.errCount;
            badge.classList.add("active");
            // Show notif if not on terminal tab
            if (S.activeTab !== "terminal") showNotif(message);
        }
    }

    // Only re-render if terminal is active (avoid heavy DOM work when hidden)
    if (S.activeTab === "terminal") {
        appendLogLine({ ts: ts, type: type, msg: message });
    }
}

function appendLogLine(entry) {
    // Filter check
    if (!logVisible(entry)) return;

    var search = document.getElementById("logSearch");
    var query = search ? search.value.trim().toLowerCase() : "";

    var panel = document.getElementById("logPanel");
    if (!panel) return;

    // Remove empty state
    var empty = panel.querySelector(".log-empty");
    if (empty) empty.remove();

    var line = document.createElement("div");
    line.className = "log-line " + entry.type;

    var msg = escapeHtml(entry.msg);
    if (query && msg.toLowerCase().indexOf(query) >= 0) {
        msg = msg.replace(new RegExp("(" + escapeRe(query) + ")", "gi"),
            '<span class="log-highlight">$1</span>');
    }

    line.innerHTML =
        '<span class="log-ts">' + entry.ts + '</span>' +
        '<span class="log-tag">' + (TYPE_LABELS[entry.type] || entry.type.toUpperCase()) + '</span>' +
        '<span class="log-msg">' + msg + '</span>';

    panel.appendChild(line);
    panel.scrollTop = panel.scrollHeight;

    updateLogCount();
}

function renderLog() {
    var panel = document.getElementById("logPanel");
    if (!panel) return;
    panel.innerHTML = "";

    var search = document.getElementById("logSearch");
    var query = search ? search.value.trim().toLowerCase() : "";

    var visible = S.logs.filter(logVisible);

    if (visible.length === 0) {
        panel.innerHTML =
            '<div class="log-empty">' +
            '<div class="log-empty-icon"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.45-6.89A2 2 0 0016.76 4H7.24a2 2 0 00-1.79 1.11z"/></svg></div>' +
            '<div class="log-empty-text">No log entries match this filter</div>' +
            '</div>';
        updateLogCount(0);
        return;
    }

    visible.forEach(function(entry) {
        var line = document.createElement("div");
        line.className = "log-line " + entry.type;

        var msg = escapeHtml(entry.msg);
        if (query && msg.toLowerCase().indexOf(query) >= 0) {
            msg = msg.replace(new RegExp("(" + escapeRe(query) + ")", "gi"),
                '<span class="log-highlight">$1</span>');
        }

        line.innerHTML =
            '<span class="log-ts">' + entry.ts + '</span>' +
            '<span class="log-tag">' + (TYPE_LABELS[entry.type] || entry.type.toUpperCase()) + '</span>' +
            '<span class="log-msg">' + msg + '</span>';

        panel.appendChild(line);
    });

    panel.scrollTop = panel.scrollHeight;
    updateLogCount(visible.length);
}

function logVisible(entry) {
    var f = S.logFilter;
    if (f !== "all" && entry.type !== f) return false;
    var search = document.getElementById("logSearch");
    if (search && search.value.trim()) {
        return entry.msg.toLowerCase().indexOf(search.value.trim().toLowerCase()) >= 0;
    }
    return true;
}

function updateLogCount(n) {
    var el = document.getElementById("logCount");
    if (!el) return;
    var count = (n !== undefined) ? n : S.logs.filter(logVisible).length;
    el.textContent = count + " entr" + (count === 1 ? "y" : "ies");
}

function setFilter(f) {
    S.logFilter = f;
    document.querySelectorAll(".filter-btn").forEach(function(b) {
        b.classList.remove("active");
    });
    var btn = document.getElementById("f-" + f);
    if (btn) btn.classList.add("active");
    renderLog();
}

function clearLog() {
    S.logs = [];
    S.errCount = 0;
    var badge = document.getElementById("errBadge");
    if (badge) { badge.textContent = "0"; badge.classList.remove("active"); }
    renderLog();
    log("Log cleared", "info");
}

function exportLogs() {
    var lines = S.logs.map(function(e) {
        return "[" + e.ts + "] [" + (TYPE_LABELS[e.type] || e.type).trim() + "] " + e.msg;
    });
    navigator.clipboard.writeText(lines.join("\n")).then(function() {
        log("Exported " + lines.length + " log entries to clipboard", "ok");
    }).catch(function(e) {
        log("Export failed: " + e, "err");
    });
}

// ---------------------------------------------------------------- Bridge ---

function pollBridgeStatus() {
    try {
        var x = new XMLHttpRequest();
        x.open("GET", bridgeUrl("/status"), true);
        x.timeout = 2000;
        x.onload = function() {
            if (x.status === 401) {
                // El bridge está vivo pero no nos reconoce: token ausente,
                // caducado o rotado. Reintentar el emparejamiento una vez.
                handleUnauthorized();
                setBridgeDown();
                return;
            }
            if (x.status === 200) {
                try {
                    var data = JSON.parse(x.responseText);
                    var wasBridge = S.bridgeUp;
                    var wasPT = S.ptConnected;
                    S.bridgeUp = true;
                    S.ptConnected = data.connected;
                    S.lastPollAgo = data.last_poll_ago;

                    if (S.ptConnected) {
                        S.pollTimes.push(Date.now());
                        if (S.pollTimes.length > 20) S.pollTimes.shift();
                        renderSparkline();
                    }

                    if (!wasBridge)            log("Bridge HTTP online at :54321", "ok");
                    if (!wasPT && S.ptConnected) log("Packet Tracer connected — polling active", "ok");
                    if (wasPT && !S.ptConnected) log("PT disconnected — polling stopped", "warn");

                    updateConnectionUI();
                } catch(e) {
                    setBridgeDown();
                }
            } else {
                setBridgeDown();
            }
        };
        x.onerror = x.ontimeout = function() {
            if (S.bridgeUp) log("Bridge unreachable at :54321", "err");
            setBridgeDown();
        };
        x.send();
    } catch(e) {
        setBridgeDown();
    }
}

function setBridgeDown() {
    S.bridgeUp = false;
    S.ptConnected = false;
    updateConnectionUI();
}

/* Un 401 significa que el bridge está pero nuestro token no sirve — típicamente
   porque el servidor lo rotó. Se relee del disco, con un tope para no entrar en
   bucle si el archivo realmente no está. */
var _tokenReloads = 0;
function handleUnauthorized() {
    if (_tokenReloads >= 3) return;
    _tokenReloads++;
    log("Bridge rejected our token — re-reading it from disk…", "warn");
    loadToken(function(ok) {
        if (ok) _tokenReloads = 0;
    });
}

function pollCommands() {
    if (!S.bridgeUp) return;
    try {
        var x = new XMLHttpRequest();
        x.open("GET", bridgeUrl("/next"), true);
        x.timeout = 1000;
        x.onload = function() {
            if (x.status === 401) { handleUnauthorized(); return; }
            if (x.status === 200 && x.responseText) {
                var cmd = x.responseText;
                var preview = cmd.length > 120 ? cmd.substring(0, 120) + "…" : cmd;
                log("MCP → PT: " + preview, "recv");
                try {
                    $se("runCode", cmd);
                    S.commandCount++;
                    log("Executed successfully", "ok");
                    updateCommandCount();
                } catch(e) {
                    log("Execution failed: " + e.message, "err");
                }
            }
        };
        x.onerror = x.ontimeout = function() {};
        x.send();
    } catch(e) {}
}

function updateConnectionUI() {
    // Header dot + label
    var dot   = document.getElementById("connDot");
    var label = document.getElementById("connLabel");
    var sbDot = document.getElementById("sb-bridge-dot");
    var sbLbl = document.getElementById("sb-bridge-label");

    var timelineFill = document.getElementById("timelineFill");

    if (S.ptConnected) {
        if (dot)   { dot.className = "conn-dot connected"; }
        if (label) { label.textContent = "PT connected (" + Math.round(S.lastPollAgo) + "s ago)"; }
        if (sbDot) sbDot.className = "status-bar-dot ok";
        if (sbLbl) sbLbl.textContent = "Bridge: connected";
        if (timelineFill) timelineFill.style.width = "100%";

        setElText("st-bridge", "UP :54321");
        setElClass("st-bridge", "stat-value ok");
        setCardClass("card-bridge", "stat-card ok");

        setElText("st-pt", "Connected");
        setElClass("st-pt", "stat-value ok");
        setCardClass("card-pt", "stat-card ok");
        setElText("st-poll-sub", "polling every 500ms");

    } else if (S.bridgeUp) {
        if (dot)   { dot.className = "conn-dot bridge-only"; }
        if (label) { label.textContent = "Bridge up / PT: waiting"; }
        if (sbDot) sbDot.className = "status-bar-dot warn";
        if (sbLbl) sbLbl.textContent = "Bridge: up, PT offline";
        if (timelineFill) timelineFill.style.width = "50%";

        setElText("st-bridge", "UP :54321");
        setElClass("st-bridge", "stat-value ok");
        setCardClass("card-bridge", "stat-card ok");

        setElText("st-pt", "Waiting…");
        setElClass("st-pt", "stat-value warn");
        setCardClass("card-pt", "stat-card warn");
        setElText("st-poll-sub", "PT not polling");

    } else {
        if (dot)   { dot.className = "conn-dot"; }
        if (label) { label.textContent = "Bridge: offline"; }
        if (sbDot) sbDot.className = "status-bar-dot err";
        if (sbLbl) sbLbl.textContent = "Bridge: offline";
        if (timelineFill) timelineFill.style.width = "0%";

        setElText("st-bridge", "DOWN");
        setElClass("st-bridge", "stat-value err");
        setCardClass("card-bridge", "stat-card err");

        setElText("st-pt", "Offline");
        setElClass("st-pt", "stat-value err");
        setCardClass("card-pt", "stat-card err");
        setElText("st-poll-sub", "not polling");
    }

    updateCommandCount();
}

function updateCommandCount() {
    setElText("st-cmds", S.commandCount);
    setElText("sb-cmds", S.commandCount + " cmd" + (S.commandCount !== 1 ? "s" : ""));
}

// ---------------------------------------------------------------- Sparkline --

function renderSparkline() {
    var svg = document.getElementById("sparkline");
    if (!svg) return;

    var W = 200, H = 36;
    var times = S.pollTimes;
    if (times.length < 2) { svg.innerHTML = ""; return; }

    var min = times[0], max = times[times.length - 1];
    var range = max - min || 1;

    var bars = times.map(function(t, i) {
        var x = Math.round(((t - min) / range) * (W - 8));
        var h = 8 + (i / times.length) * 20;
        var y = H - h;
        return '<rect x="' + x + '" y="' + y + '" width="6" height="' + h +
            '" rx="2" fill="#4f8ef7" opacity="' + (0.3 + 0.7 * i / times.length) + '"/>';
    });

    svg.innerHTML = bars.join("");
}

// ------------------------------------------------------------------ Uptime --

function updateUptime() {
    var elapsed = Math.floor((Date.now() - S.sessionStart) / 1000);
    var m = Math.floor(elapsed / 60);
    var s = elapsed % 60;
    var str = pad2(m) + ":" + pad2(s);
    setElText("st-uptime", str);
    setElText("sb-uptime", str);
}

// --------------------------------------------------------------- Editor ----

function onEditorInput() {
    var indicator = document.getElementById("saveIndicator");
    if (indicator) { indicator.textContent = "● saving…"; indicator.className = "save-indicator"; }
    if (S._saveTimer) clearTimeout(S._saveTimer);
    S._saveTimer = setTimeout(function() {
        saveCode();
        if (indicator) { indicator.textContent = "● saved"; indicator.className = "save-indicator saved"; }
    }, 800);
    S.historyPos = -1; // reset navigator on manual edit
}

function saveCode() {
    var code = document.getElementById("codeeditor").value;
    try { $putData("code", code); } catch(e) {}
}

function loadCode() {
    try {
        $getData("code").then(function(code) {
            document.getElementById("codeeditor").value = code || "";
        }).catch(function() {});
    } catch(e) {}
}

function executeCode() {
    var code = document.getElementById("codeeditor").value;
    if (!code.trim()) {
        log("Editor is empty — nothing to execute", "warn");
        return;
    }

    if (!S.bridgeUp) {
        log("Bridge is offline — cannot execute. Start the bridge first.", "warn");
        showNotif("Bridge is offline. Start the MCP server and paste the bootstrap snippet.");
        switchTab("status");
        return;
    }

    // Push to history
    if (!S.history.length || S.history[S.history.length - 1] !== code) {
        S.history.push(code);
        if (S.history.length > 20) S.history.shift();
        persistHistory();
        refreshHistoryDropdown();
    }
    S.historyPos = -1;

    var preview = code.trim().substring(0, 100);
    log("Executing: " + preview + (code.length > 100 ? " …" : ""), "cmd");

    // Split into individual commands and send through bridge (same pipeline as Quick Build)
    var lines = code.split("\n").filter(function(l) {
        var t = l.trim();
        return t && !t.startsWith("//");
    });

    var sent = 0;
    lines.forEach(function(line) {
        var cmd = line.trim();
        if (!cmd) return;
        // Add semicolon if missing (PT Script Engine requires it)
        if (!cmd.endsWith(";")) cmd += ";";
        try {
            var x = new XMLHttpRequest();
            x.open("POST", bridgeUrl("/queue"), false);
            x.setRequestHeader("Content-Type", "text/plain");
            x.send(cmd);
            if (x.status === 200) sent++;
        } catch(e) {}
    });

    if (sent > 0) {
        S.commandCount += sent;
        updateCommandCount();
        log(sent + " command" + (sent > 1 ? "s" : "") + " queued via bridge", "ok");
        switchTab("terminal");
    } else {
        log("Failed to queue commands — check bridge connection", "err");
        switchTab("terminal");
    }
}

function clearEditor() {
    var editor = document.getElementById("codeeditor");
    if (!editor.value.trim()) return;
    editor.value = "";
    log("Editor cleared", "info");
    S.historyPos = -1;
    try { $putData("code", ""); } catch(e) {}
}

function copyToClipboard() {
    var code = document.getElementById("codeeditor").value;
    navigator.clipboard.writeText(code).then(function() {
        log("Copied to clipboard (" + code.length + " chars)", "ok");
    }).catch(function(e) {
        log("Clipboard write failed: " + e, "err");
    });
}

function pasteFromClipboard() {
    navigator.clipboard.readText().then(function(text) {
        document.getElementById("codeeditor").value = text;
        log("Pasted from clipboard (" + text.length + " chars)", "ok");
        onEditorInput();
    }).catch(function(e) {
        log("Clipboard read failed: " + e, "err");
    });
}

// ----------------------------------------------------------- History -------

function persistHistory() {
    try { $putData("history", JSON.stringify(S.history)); } catch(e) {}
}

function refreshHistoryDropdown() {
    var sel = document.getElementById("historySelect");
    if (!sel) return;
    sel.innerHTML = '<option value="">— History (' + S.history.length + ') —</option>';
    for (var i = S.history.length - 1; i >= 0; i--) {
        var preview = S.history[i].trim().replace(/\n/g, " ").substring(0, 60);
        var opt = document.createElement("option");
        opt.value = i;
        opt.textContent = (S.history.length - i) + ". " + preview;
        sel.appendChild(opt);
    }
}

function loadFromHistory(idx) {
    if (idx === "" || idx === null) return;
    var code = S.history[parseInt(idx, 10)];
    if (code !== undefined) {
        document.getElementById("codeeditor").value = code;
        log("Loaded from history #" + (parseInt(idx, 10) + 1), "info");
    }
    document.getElementById("historySelect").value = "";
}

// -------------------------------------------------- Keyboard shortcuts -----

function editorKeyDown(e) {
    var editor = document.getElementById("codeeditor");

    // Ctrl+Enter → Run
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        executeCode();
        return;
    }

    // Ctrl+K → Clear editor
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        clearEditor();
        return;
    }

    // Arrow Up at start of textarea → history back
    if (e.key === "ArrowUp" && editor.selectionStart === 0 && S.history.length > 0) {
        if (S.historyPos < S.history.length - 1) {
            S.historyPos++;
            editor.value = S.history[S.history.length - 1 - S.historyPos];
        }
        e.preventDefault();
        return;
    }

    // Arrow Down → history forward
    if (e.key === "ArrowDown" && editor.selectionEnd === editor.value.length && S.historyPos >= 0) {
        S.historyPos--;
        if (S.historyPos < 0) {
            editor.value = "";
        } else {
            editor.value = S.history[S.history.length - 1 - S.historyPos];
        }
        e.preventDefault();
        return;
    }
}

// Global keyboard shortcut — Ctrl+L to clear log from any tab
document.addEventListener("keydown", function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "l") {
        e.preventDefault();
        clearLog();
    }
});

// -------------------------------------------------------- Bootstrap snippet -

function copyBootstrap() {
    navigator.clipboard.writeText(bootstrapSnippet()).then(function() {
        log("Bootstrap snippet copied to clipboard", "ok");
    }).catch(function(e) {
        log("Failed to copy: " + e, "err");
    });
}

// ---------------------------------------------------------- Quick Build ----

function getQBValues() {
    return {
        template:      val("qb-template"),
        routing:       val("qb-routing"),
        routerModel:   val("qb-router-model"),
        switchModel:   val("qb-switch-model"),
        routers:       intVal("qb-routers"),
        pcs:           intVal("qb-pcs"),
        switches:      intVal("qb-switches"),
        servers:       intVal("qb-servers"),
        dhcp:          checked("qb-dhcp"),
        wan:           checked("qb-wan"),
        floating:      checked("qb-floating"),
    };
}

function updateQBPreview() {
    var v = getQBValues();
    var parts = [
        v.template, v.routing,
        v.routers + "R", v.pcs + "PC", v.switches + "SW",
        v.servers > 0 ? v.servers + "SRV" : null,
        v.dhcp ? "DHCP" : null,
        v.wan ? "WAN" : null,
    ].filter(Boolean);
    setElText("qbPreview", parts.join(" · "));
}

function buildQBScript(v) {
    // Generates addDevice + addLink commands matching the PT Builder API:
    //   addDevice(name, model, x, y);
    //   addLink(devA, portA, devB, portB, cableType);
    // Cable types: "cross" for router↔router, "straight" for everything else.

    var lines = [];
    var spacing = 250;
    var routerY = 200;

    // Helper: router port name (ISR4321/4331 uses triple index)
    function rPort(idx) {
        if (v.routerModel === "ISR4321" || v.routerModel === "ISR4331") {
            return "GigabitEthernet0/0/" + idx;
        }
        return "GigabitEthernet0/" + idx;
    }

    // Determine effective router count based on template
    var numRouters = v.routers;
    if (v.template === "single_lan") numRouters = 1;
    if (v.template === "three_router_triangle") numRouters = 3;

    lines.push("// Quick Build: " + v.template + " | " + numRouters + "x " + v.routerModel +
        " | " + v.pcs + " PC/LAN | " + v.routing);

    // ── ROUTER POSITIONS (depend on template) ────────────────────────
    var rPos = []; // { name, x, y }

    if (v.template === "star" || v.template === "hub_spoke") {
        // Central hub + spokes in a circle
        var cx = 400, cy = 250;
        rPos.push({ name: "R1", x: cx, y: cy });
        var rad = 220;
        for (var i = 1; i < numRouters; i++) {
            var angle = (2 * Math.PI * (i - 1)) / Math.max(numRouters - 1, 1) - Math.PI / 2;
            rPos.push({
                name: "R" + (i + 1),
                x: Math.round(cx + rad * Math.cos(angle)),
                y: Math.round(cy + rad * Math.sin(angle))
            });
        }
    } else if (v.template === "three_router_triangle") {
        rPos = [
            { name: "R1", x: 300, y: 100 },
            { name: "R2", x: 100, y: 350 },
            { name: "R3", x: 500, y: 350 }
        ];
    } else {
        // Linear chain: single_lan, multi_lan, multi_lan_wan, branch_office, router_on_a_stick
        for (var r = 0; r < numRouters; r++) {
            rPos.push({ name: "R" + (r + 1), x: 100 + r * spacing, y: routerY });
        }
    }

    // ── ADD DEVICES ──────────────────────────────────────────────────
    lines.push("// Devices");

    rPos.forEach(function(rp) {
        lines.push('addDevice("' + rp.name + '", "' + v.routerModel + '", ' + rp.x + ', ' + rp.y + ');');
    });

    rPos.forEach(function(rp, idx) {
        var swYPos = rp.y + 150;
        for (var sw = 0; sw < v.switches; sw++) {
            var swName = "SW" + (idx + 1) + (v.switches > 1 ? String.fromCharCode(65 + sw) : "");
            lines.push('addDevice("' + swName + '", "' + v.switchModel + '", ' + (rp.x + sw * 80) + ', ' + swYPos + ');');
        }
        for (var p = 0; p < v.pcs; p++) {
            var pcName = "PC" + (idx + 1) + "-" + (p + 1);
            var pcX = rp.x - Math.floor(v.pcs / 2) * 60 + p * 60;
            lines.push('addDevice("' + pcName + '", "PC-PT", ' + pcX + ', ' + (rp.y + 300) + ');');
        }
    });

    if (v.servers > 0) {
        for (var s = 0; s < v.servers; s++) {
            lines.push('addDevice("SRV' + (s + 1) + '", "Server-PT", ' + (100 + s * 80) + ', ' + (routerY + 430) + ');');
        }
    }

    if (v.wan && v.template === "multi_lan_wan") {
        var cloudX = 100 + Math.floor(numRouters / 2) * spacing;
        lines.push('addDevice("Cloud1", "Cloud-PT", ' + cloudX + ', ' + (routerY - 150) + ');');
    }

    // ── ADD LINKS ─────────────────────────────────────────────────────
    lines.push("// Links");

    // --- Router ↔ Router links ---
    if (v.template === "star" || v.template === "hub_spoke") {
        // Hub (R1) connects to each spoke via consecutive GE ports
        for (var si = 1; si < numRouters; si++) {
            lines.push('addLink("R1", "' + rPort(si - 1) + '", "R' + (si + 1) + '", "' + rPort(0) + '", "cross");');
        }
    } else if (v.template === "three_router_triangle") {
        // Triangle: R1↔R2, R2↔R3, R3↔R1  (each router uses GE0/0 and GE0/1 for WAN)
        lines.push('addLink("R1", "' + rPort(0) + '", "R2", "' + rPort(0) + '", "cross");');
        lines.push('addLink("R2", "' + rPort(1) + '", "R3", "' + rPort(0) + '", "cross");');
        lines.push('addLink("R3", "' + rPort(1) + '", "R1", "' + rPort(1) + '", "cross");');
    } else {
        // Linear chain: R[i] → R[i+1]
        // Port scheme matching real topology output:
        //   R1 (edge):   GE0/0 = WAN right,      GE0/1 = LAN
        //   Middle:      GE0/0 = WAN left,        GE0/1 = WAN right,  GE0/2 = LAN
        //   R_last (edge): GE0/0 = WAN left,      GE0/1 = LAN
        for (var ri = 0; ri < numRouters - 1; ri++) {
            var fromR = "R" + (ri + 1);
            var toR   = "R" + (ri + 2);
            var fromPort = (ri === 0) ? rPort(0) : rPort(1); // edge uses 0, middle uses 1 (right exit)
            var toPort   = rPort(0);                          // always arrives on 0
            lines.push('addLink("' + fromR + '", "' + fromPort + '", "' + toR + '", "' + toPort + '", "cross");');
        }
    }

    // --- Router → Switch LAN links ---
    if (v.switches > 0) {
        rPos.forEach(function(rp, idx) {
            var rName  = rp.name;
            var swName = "SW" + (idx + 1) + (v.switches > 1 ? "A" : "");
            var lanIdx;

            if (v.template === "star" || v.template === "hub_spoke") {
                if (idx === 0) {
                    lanIdx = numRouters - 1; // Hub: LAN port after all WAN ports
                } else {
                    lanIdx = 1;              // Spoke: GE0/0=WAN, GE0/1=LAN
                }
            } else if (v.template === "three_router_triangle") {
                lanIdx = 2; // GE0/0 & GE0/1 are WAN, GE0/2 = LAN
            } else {
                // Linear chain
                if (numRouters === 1) {
                    lanIdx = 0; // Single router: GE0/0 = LAN
                } else if (idx === 0 || idx === numRouters - 1) {
                    lanIdx = 1; // Edge: GE0/0=WAN, GE0/1=LAN
                } else {
                    lanIdx = 2; // Middle: GE0/0=WAN left, GE0/1=WAN right, GE0/2=LAN
                }
            }

            lines.push('addLink("' + rName + '", "' + rPort(lanIdx) + '", "' + swName + '", "GigabitEthernet0/1", "straight");');
        });
    }

    // --- Switch → PCs ---
    if (v.switches > 0 && v.pcs > 0) {
        rPos.forEach(function(rp, idx) {
            var swName = "SW" + (idx + 1) + (v.switches > 1 ? "A" : "");
            for (var p = 0; p < v.pcs; p++) {
                var pcName = "PC" + (idx + 1) + "-" + (p + 1);
                var swPort = "FastEthernet0/" + (p + 1);
                lines.push('addLink("' + swName + '", "' + swPort + '", "' + pcName + '", "FastEthernet0", "straight");');
            }
        });
    }

    // --- Switch → Servers ---
    if (v.servers > 0 && v.switches > 0) {
        var srvSw = "SW1" + (v.switches > 1 ? "A" : "");
        for (var s2 = 0; s2 < v.servers; s2++) {
            var srvSwPort = "FastEthernet0/" + (v.pcs + 1 + s2);
            lines.push('addLink("' + srvSw + '", "' + srvSwPort + '", "SRV' + (s2 + 1) + '", "FastEthernet0", "straight");');
        }
    }

    // --- WAN Cloud link ---
    if (v.wan && v.template === "multi_lan_wan") {
        // Connect Cloud to R1 on next available GE port
        var cloudPort = (numRouters === 1) ? 1 : 2;
        lines.push('addLink("R1", "' + rPort(cloudPort) + '", "Cloud1", "Ethernet0", "straight");');
    }

    // ── IOS CONFIGURATIONS ────────────────────────────────────────────
    lines.push("// IOS Configurations");

    // Build WAN link map: for each router, which port connects to which router, and what IPs
    // Inter-router links use 10.0.{linkIdx}.0/30 — .1 for left/hub side, .2 for right/spoke side
    // LAN subnets use 192.168.{routerIdx}.0/24 — gateway .1, PCs from .2
    var wanLinks = []; // { fromR, toR, fromPort, toPort, subnet: linkIdx }

    if (v.template === "star" || v.template === "hub_spoke") {
        for (var wi = 1; wi < numRouters; wi++) {
            wanLinks.push({
                fromR: "R1", toR: "R" + (wi + 1),
                fromPort: rPort(wi - 1), toPort: rPort(0),
                linkIdx: wi - 1
            });
        }
    } else if (v.template === "three_router_triangle") {
        wanLinks.push({ fromR: "R1", toR: "R2", fromPort: rPort(0), toPort: rPort(0), linkIdx: 0 });
        wanLinks.push({ fromR: "R2", toR: "R3", fromPort: rPort(1), toPort: rPort(0), linkIdx: 1 });
        wanLinks.push({ fromR: "R3", toR: "R1", fromPort: rPort(1), toPort: rPort(1), linkIdx: 2 });
    } else {
        for (var wri = 0; wri < numRouters - 1; wri++) {
            wanLinks.push({
                fromR: "R" + (wri + 1), toR: "R" + (wri + 2),
                fromPort: (wri === 0) ? rPort(0) : rPort(1),
                toPort: rPort(0),
                linkIdx: wri
            });
        }
    }

    // Compute LAN port index for each router (same logic as links section)
    function getLanIdx(idx) {
        if (v.template === "star" || v.template === "hub_spoke") {
            return (idx === 0) ? numRouters - 1 : 1;
        } else if (v.template === "three_router_triangle") {
            return 2;
        } else {
            if (numRouters === 1) return 0;
            if (idx === 0 || idx === numRouters - 1) return 1;
            return 2;
        }
    }

    // Generate configureIosDevice for each router
    rPos.forEach(function(rp, idx) {
        var cfg = [];
        cfg.push("enable");
        cfg.push("configure terminal");
        cfg.push("hostname " + rp.name);
        cfg.push("no ip domain-lookup");
        cfg.push("");

        // WAN interfaces
        wanLinks.forEach(function(wl) {
            if (wl.fromR === rp.name) {
                var ip = "10.0." + wl.linkIdx + ".1";
                cfg.push("interface " + wl.fromPort);
                cfg.push(" ip address " + ip + " 255.255.255.252");
                cfg.push(" no shutdown");
                cfg.push(" exit");
                cfg.push("");
            }
            if (wl.toR === rp.name) {
                var ip2 = "10.0." + wl.linkIdx + ".2";
                cfg.push("interface " + wl.toPort);
                cfg.push(" ip address " + ip2 + " 255.255.255.252");
                cfg.push(" no shutdown");
                cfg.push(" exit");
                cfg.push("");
            }
        });

        // LAN interface
        if (v.switches > 0 || v.pcs > 0) {
            var lanPort = rPort(getLanIdx(idx));
            var lanNet = "192.168." + idx + ".1";
            cfg.push("interface " + lanPort);
            cfg.push(" ip address " + lanNet + " 255.255.255.0");
            cfg.push(" no shutdown");
            cfg.push(" exit");
            cfg.push("");
        }

        // DHCP pool
        if (v.dhcp && (v.switches > 0 || v.pcs > 0)) {
            cfg.push("ip dhcp excluded-address 192.168." + idx + ".1 192.168." + idx + ".1");
            cfg.push("ip dhcp pool LAN_" + rp.name);
            cfg.push(" network 192.168." + idx + ".0 255.255.255.0");
            cfg.push(" default-router 192.168." + idx + ".1");
            cfg.push(" dns-server 8.8.8.8");
            cfg.push(" exit");
            cfg.push("");
        }

        // Routing
        if (v.routing === "static") {
            // Static routes to all other LAN subnets
            rPos.forEach(function(otherRp, otherIdx) {
                if (otherIdx === idx) return;
                // Find next-hop: the WAN peer IP toward destination
                var nextHop = findNextHop(rp.name, otherIdx, wanLinks, rPos, numRouters, v.template);
                if (nextHop) {
                    cfg.push("ip route 192.168." + otherIdx + ".0 255.255.255.0 " + nextHop);
                }
            });
            cfg.push("");
        } else if (v.routing === "ospf") {
            cfg.push("router ospf 1");
            cfg.push(" router-id " + (idx + 1) + "." + (idx + 1) + "." + (idx + 1) + "." + (idx + 1));
            // Advertise LAN
            if (v.switches > 0 || v.pcs > 0) {
                cfg.push(" network 192.168." + idx + ".0 0.0.0.255 area 0");
            }
            // Advertise WAN links
            wanLinks.forEach(function(wl) {
                if (wl.fromR === rp.name || wl.toR === rp.name) {
                    cfg.push(" network 10.0." + wl.linkIdx + ".0 0.0.0.3 area 0");
                }
            });
            cfg.push(" exit");
            cfg.push("");
        } else if (v.routing === "eigrp") {
            cfg.push("router eigrp 100");
            if (v.switches > 0 || v.pcs > 0) {
                cfg.push(" network 192.168." + idx + ".0 0.0.0.255");
            }
            wanLinks.forEach(function(wl) {
                if (wl.fromR === rp.name || wl.toR === rp.name) {
                    cfg.push(" network 10.0." + wl.linkIdx + ".0 0.0.0.3");
                }
            });
            cfg.push(" no auto-summary");
            cfg.push(" exit");
            cfg.push("");
        } else if (v.routing === "rip") {
            cfg.push("router rip");
            cfg.push(" version 2");
            if (v.switches > 0 || v.pcs > 0) {
                cfg.push(" network 192.168." + idx + ".0");
            }
            wanLinks.forEach(function(wl) {
                if (wl.fromR === rp.name || wl.toR === rp.name) {
                    cfg.push(" network 10.0." + wl.linkIdx + ".0");
                }
            });
            cfg.push(" no auto-summary");
            cfg.push(" exit");
            cfg.push("");
        }

        cfg.push("end");
        cfg.push("write memory");

        // Escape for JS string: replace \ with \\ then " with \"  then newlines with \n
        var cfgStr = cfg.join("\\n");
        lines.push('configureIosDevice("' + rp.name + '", "' + cfgStr + '");');
    });

    // Generate switch hostname config
    rPos.forEach(function(rp, idx) {
        for (var sw = 0; sw < v.switches; sw++) {
            var swName = "SW" + (idx + 1) + (v.switches > 1 ? String.fromCharCode(65 + sw) : "");
            var swCfg = "enable\\nhostname " + swName + "\\nend\\nwrite memory";
            lines.push('configureIosDevice("' + swName + '", "' + swCfg + '");');
        }
    });

    // Configure PCs
    lines.push("// PC IP Configuration");
    rPos.forEach(function(rp, idx) {
        for (var p = 0; p < v.pcs; p++) {
            var pcName = "PC" + (idx + 1) + "-" + (p + 1);
            if (v.dhcp) {
                lines.push('configurePcIp("' + pcName + '", true);');
            } else {
                var pcIp   = "192.168." + idx + "." + (p + 2);
                var pcMask = "255.255.255.0";
                var pcGw   = "192.168." + idx + ".1";
                lines.push('configurePcIp("' + pcName + '", false, "' + pcIp + '", "' + pcMask + '", "' + pcGw + '");');
            }
        }
    });

    // Configure Servers
    if (v.servers > 0) {
        for (var sv = 0; sv < v.servers; sv++) {
            var srvName = "SRV" + (sv + 1);
            if (v.dhcp) {
                lines.push('configurePcIp("' + srvName + '", true);');
            } else {
                var srvIp = "192.168.0." + (v.pcs + 2 + sv);
                lines.push('configurePcIp("' + srvName + '", false, "' + srvIp + '", "255.255.255.0", "192.168.0.1");');
            }
        }
    }

    return lines.join("\n");
}

// Helper: find next-hop IP for static routing (simple: direct neighbor or via first hop)
function findNextHop(fromName, destIdx, wanLinks, rPos, numRouters, template) {
    // For star/hub_spoke: hub knows all spokes directly, spokes go through hub
    if (template === "star" || template === "hub_spoke") {
        if (fromName === "R1") {
            // Hub to spoke: direct link
            for (var i = 0; i < wanLinks.length; i++) {
                if (wanLinks[i].toR === rPos[destIdx].name) {
                    return "10.0." + wanLinks[i].linkIdx + ".2";
                }
            }
        } else {
            // Spoke to anywhere: go through hub (R1)
            for (var j = 0; j < wanLinks.length; j++) {
                if (wanLinks[j].toR === fromName) {
                    return "10.0." + wanLinks[j].linkIdx + ".1";
                }
            }
        }
        return null;
    }

    // For triangle: find direct link to dest, or route through first available neighbor
    if (template === "three_router_triangle") {
        var destName = rPos[destIdx].name;
        // Direct link?
        for (var t = 0; t < wanLinks.length; t++) {
            if (wanLinks[t].fromR === fromName && wanLinks[t].toR === destName) {
                return "10.0." + wanLinks[t].linkIdx + ".2";
            }
            if (wanLinks[t].toR === fromName && wanLinks[t].fromR === destName) {
                return "10.0." + wanLinks[t].linkIdx + ".1";
            }
        }
        // Not directly connected — go via first neighbor
        for (var u = 0; u < wanLinks.length; u++) {
            if (wanLinks[u].fromR === fromName) return "10.0." + wanLinks[u].linkIdx + ".2";
            if (wanLinks[u].toR === fromName) return "10.0." + wanLinks[u].linkIdx + ".1";
        }
        return null;
    }

    // Linear chain: find direction toward destination
    var fromIdx = -1;
    for (var fi = 0; fi < rPos.length; fi++) {
        if (rPos[fi].name === fromName) { fromIdx = fi; break; }
    }
    if (fromIdx < 0) return null;

    if (destIdx > fromIdx) {
        // Go right: use link fromIdx → fromIdx+1
        for (var r = 0; r < wanLinks.length; r++) {
            if (wanLinks[r].fromR === fromName) return "10.0." + wanLinks[r].linkIdx + ".2";
        }
        // Might be the "toR" side
        for (var r2 = 0; r2 < wanLinks.length; r2++) {
            if (wanLinks[r2].toR === rPos[fromIdx + 1].name && wanLinks[r2].fromR === fromName) {
                return "10.0." + wanLinks[r2].linkIdx + ".2";
            }
        }
    } else {
        // Go left: use link fromIdx-1 → fromIdx, next-hop is .1
        for (var l = 0; l < wanLinks.length; l++) {
            if (wanLinks[l].toR === fromName) return "10.0." + wanLinks[l].linkIdx + ".1";
        }
    }
    return null;
}

function quickBuild() {
    var v = getQBValues();

    if (!S.ptConnected) {
        log("PT not connected — check Status tab and paste the bootstrap snippet first", "warn");
        showNotif("PT is not connected. Paste the bootstrap snippet in PT first.");
        switchTab("status");
        return;
    }

    var script = buildQBScript(v);
    log("Quick Build: sending " + v.routers + "R " + v.pcs + "PC topology via bridge…", "cmd");

    var lines = script.split("\n").filter(function(l) { return l.trim() && !l.trim().startsWith("//"); });
    var sent = 0;
    lines.forEach(function(line) {
        try {
            var x = new XMLHttpRequest();
            x.open("POST", bridgeUrl("/queue"), false);  // sync for simplicity
            x.setRequestHeader("Content-Type", "text/plain");
            x.send(line);
            if (x.status === 200) sent++;
        } catch(e) {}
    });

    if (sent > 0) {
        S.commandCount += sent;
        updateCommandCount();
        log("Quick Build: " + sent + " commands queued", "ok");
        switchTab("terminal");
    } else {
        log("Quick Build: failed to queue commands (bridge error)", "err");
    }
}

// ------------------------------------------------------- Notifications -----

function showNotif(msg) {
    var strip = document.getElementById("notifStrip");
    var msgEl = document.getElementById("notifMsg");
    if (strip && msgEl) {
        msgEl.textContent = msg;
        strip.classList.add("active");
        setTimeout(closeNotif, 8000);
    }
}

function closeNotif() {
    var strip = document.getElementById("notifStrip");
    if (strip) strip.classList.remove("active");
}

// ------------------------------------------------------- Utilities ---------

function setElText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
}

function setElClass(id, cls) {
    var el = document.getElementById(id);
    if (el) el.className = cls;
}

function setCardClass(id, cls) {
    var el = document.getElementById(id);
    if (el) el.className = cls;
}

function val(id) {
    var el = document.getElementById(id);
    return el ? el.value : "";
}

function intVal(id) {
    return parseInt(val(id), 10) || 0;
}

function checked(id) {
    var el = document.getElementById(id);
    return el ? el.checked : false;
}

function pad2(n) {
    return n < 10 ? "0" + n : "" + n;
}

function escapeHtml(str) {
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(String(str)));
    return d.innerHTML;
}

function escapeRe(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}