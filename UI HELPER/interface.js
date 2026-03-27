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

var BOOTSTRAP_SNIPPET =
    '/* PT-MCP Bridge */ window.webview.evaluateJavaScriptAsync(' +
    '"setInterval(function(){var x=new XMLHttpRequest();' +
    "x.open('GET','http://127.0.0.1:54321/next',true);" +
    'x.onload=function(){if(x.status===200&&x.responseText)' +
    "{$se('runCode',x.responseText)}};x.onerror=function(){};" +
    'x.send()},500)");';

// ------------------------------------------------------------------ Init ---

function init() {
    // Show bootstrap snippet
    var el = document.getElementById("bootstrapSnippet");
    if (el) el.textContent = BOOTSTRAP_SNIPPET;

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
            '<div class="log-empty-icon">📭</div>' +
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
        x.open("GET", "http://127.0.0.1:54321/status", true);
        x.timeout = 2000;
        x.onload = function() {
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

function pollCommands() {
    if (!S.bridgeUp) return;
    try {
        var x = new XMLHttpRequest();
        x.open("GET", "http://127.0.0.1:54321/next", true);
        x.timeout = 1000;
        x.onload = function() {
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

    var flat = code.replace(/\n/g, "");
    try {
        $se("runCode", flat);
        S.commandCount++;
        updateCommandCount();
        log("Execution complete", "ok");
        // Switch to terminal for feedback
        switchTab("terminal");
    } catch(e) {
        log("Execution error: " + e.message, "err");
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
    navigator.clipboard.writeText(BOOTSTRAP_SNIPPET).then(function() {
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
    // Generates addDevice + addLink commands for a linear multi-LAN topology.
    // Port layout per router:
    //   GE0/0 = LAN uplink (to switch or direct to PCs)
    //   GE0/1 = WAN link to RIGHT neighbor (used by R1 and each intermediate as incoming-left)
    //   GE0/2 = WAN link to RIGHT neighbor for intermediate/last routers
    // ISR4321 uses GigabitEthernet0/0/x naming instead of GigabitEthernet0/x.

    var lines = [
        "// Quick Build: " + v.routers + "x " + v.routerModel +
        " | " + v.pcs + " PC/LAN | " + v.routing,
    ];

    var spacing = 250;
    var routerY = 200;
    var switchY = 350;
    var pcBaseY = 500;

    // Helper: router port name (ISR4321 uses triple index)
    function rPort(idx) {
        if (v.routerModel === "ISR4321" || v.routerModel === "ISR4331") {
            return "GigabitEthernet0/0/" + idx;
        }
        return "GigabitEthernet0/" + idx;
    }

    // Helper: switch primary LAN port name
    function swLANPort() { return "GigabitEthernet0/1"; }

    // ── ADD DEVICES ──────────────────────────────────────────────────
    lines.push("// Devices");

    for (var r = 0; r < v.routers; r++) {
        var rx = 100 + r * spacing;
        lines.push("addDevice('R" + (r+1) + "','" + v.routerModel + "'," + rx + "," + routerY + ")");
    }

    for (var r2 = 0; r2 < v.routers; r2++) {
        var rx2 = 100 + r2 * spacing;
        for (var sw = 0; sw < v.switches; sw++) {
            var swName = "SW" + (r2+1) + (v.switches > 1 ? String.fromCharCode(65+sw) : "");
            lines.push("addDevice('" + swName + "','" + v.switchModel + "'," + (rx2 + sw*80) + "," + switchY + ")");
        }
    }

    for (var r3 = 0; r3 < v.routers; r3++) {
        var rx3 = 100 + r3 * spacing;
        for (var p = 0; p < v.pcs; p++) {
            var pcName = "PC" + (r3+1) + "-" + (p+1);
            var pcX = rx3 - Math.floor(v.pcs/2) * 60 + p * 60;
            lines.push("addDevice('" + pcName + "','PC-PT'," + pcX + "," + pcBaseY + ")");
        }
    }

    if (v.servers > 0) {
        for (var s = 0; s < v.servers; s++) {
            lines.push("addDevice('SRV" + (s+1) + "','Server-PT'," + (100 + s*80) + "," + (pcBaseY+130) + ")");
        }
    }

    // ── ADD LINKS ────────────────────────────────────────────────────
    lines.push("// Links");

    // 1. Router ↔ Router (linear chain)
    // R[i] right-port → R[i+1] left-port
    //   R1: port 0=LAN, port 1=right-WAN
    //   R_mid: port 0=LAN, port 1=incoming-left, port 2=outgoing-right
    //   R_last: port 0=LAN, port 1=incoming-left
    for (var ri = 0; ri < v.routers - 1; ri++) {
        var fromRouter = "R" + (ri + 1);
        var toRouter   = "R" + (ri + 2);
        // From router: index 0 uses port 1 as its only WAN link; others use port 2 (their "right" exit)
        var fromPort = rPort(ri === 0 ? 1 : 2);
        // To router always receives on port 1
        var toPort   = rPort(1);
        lines.push("addLink('" + fromRouter + "','" + fromPort + "','" + toRouter + "','" + toPort + "')");
    }

    // 2. Router → Switch (LAN uplink via GE0/0 → switch GE0/1 or FA0/1 on 2960)
    if (v.switches > 0) {
        for (var r4 = 0; r4 < v.routers; r4++) {
            var rName   = "R" + (r4 + 1);
            var swFirst = "SW" + (r4 + 1) + (v.switches > 1 ? "A" : "");
            var rLANPort = rPort(0);
            // 2960/3560 uplink: GigabitEthernet0/1 (L2 switch uplink port)
            lines.push("addLink('" + rName + "','" + rLANPort + "','" + swFirst + "','GigabitEthernet0/1')");
        }
    }

    // 3. Switch → PCs  (SW FA0/2, FA0/3, … → PC FastEthernet0)
    if (v.switches > 0 && v.pcs > 0) {
        for (var r5 = 0; r5 < v.routers; r5++) {
            var swName5 = "SW" + (r5 + 1) + (v.switches > 1 ? "A" : "");
            for (var p2 = 0; p2 < v.pcs; p2++) {
                var pcName2 = "PC" + (r5+1) + "-" + (p2+1);
                // FA0/2 onwards (FA0/1 is uplink to router)
                var swPort = "FastEthernet0/" + (p2 + 2);
                lines.push("addLink('" + swName5 + "','" + swPort + "','" + pcName2 + "','FastEthernet0')");
            }
        }
    }

    // 4. Switch → Servers  (appended after PCs on SW1)
    if (v.servers > 0 && v.switches > 0) {
        var srvSw = "SW1" + (v.switches > 1 ? "A" : "");
        for (var s2 = 0; s2 < v.servers; s2++) {
            // Ports after the PCs already patched (FA0/2 … FA0/pcs+1)
            var srvSwPort = "FastEthernet0/" + (v.pcs + 2 + s2);
            lines.push("addLink('" + srvSw + "','" + srvSwPort + "','SRV" + (s2+1) + "','FastEthernet0')");
        }
    }

    return lines.join("\n");
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
            x.open("POST", "http://127.0.0.1:54321/queue", false);  // sync for simplicity
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