// ============================================================================
// MCP Control Center V6 — the window Packet Tracer shows for the MCP extension.
// Connection state, what the agent did, debug tools and a JS console. The bridge
// logic (token, long-poll, file channel) is unchanged from V5.
// ============================================================================

var S = {
    bridgeUp:     false,
    ptConnected:  false,
    tokenOk:      null,     // null = not read yet
    fileBridge:   null,
    commandCount: 0,
    sessionStart: Date.now(),
    entries:      [],       // { ts, type, text, detail, count }
    filter:       "all",
    errCount:     0,
    maxEntries:   400
};

var BRIDGE_BASE = "http://127.0.0.1:54321";

// Token shared with the MCP server. This webview runs under the this-sm:
// scheme and has no file system access, so it asks the Script Engine for it
// with $se("getMcpToken"), which can read files. It is deliberately not cached:
// if the server rotates the token, a cached copy would silently go stale.
var S_TOKEN = "";

function bridgeUrl(path) {
    return BRIDGE_BASE + path + (S_TOKEN ? "?t=" + encodeURIComponent(S_TOKEN) : "");
}

// ------------------------------------------------------------------ Token ---

function loadToken(onDone) {
    try {
        $se("getMcpToken").then(function(tok) {
            if (tok) {
                S_TOKEN = String(tok);
                S.tokenOk = true;
                updateUI();
                if (onDone) onDone(true);
            } else {
                S.tokenOk = false;
                reportMissingToken();
                updateUI();
                if (onDone) onDone(false);
            }
        }).catch(function(e) {
            S.tokenOk = false;
            addEntry("err", "Could not read the security token", String(e));
            updateUI();
            if (onDone) onDone(false);
        });
    } catch(e) {
        S.tokenOk = false;
        addEntry("err", "Script Engine unavailable — cannot read the security token");
        updateUI();
        if (onDone) onDone(false);
    }
}

/* Without a token the bridge answers 401 to everything. The cause is almost
   always that the MCP server never ran on this machine, so say so. */
function reportMissingToken() {
    addEntry("err", "No security token on this machine",
        "Start your AI agent with the MCP server once (it creates the token), then reopen this window.");
    try {
        $se("getMcpTokenInfo").then(function(info) {
            var d = JSON.parse(info);
            if (d.searched && d.searched.length) addEntry("info", "Looked for it in", d.searched.join("\n"));
        }).catch(function() {});
    } catch(e) {}
}

/* A 401 means the bridge is up but our token is not valid — typically because
   the server rotated it. Re-read it from disk, capped so it cannot loop. */
var _tokenReloads = 0;
function handleUnauthorized() {
    if (_tokenReloads >= 3) return;
    _tokenReloads++;
    addEntry("warn", "The MCP server rejected the token — reading it again");
    loadToken(function(ok) { if (ok) _tokenReloads = 0; });
}

// ------------------------------------------------------------------ Init ---

function init() {
    loadToken();
    try {
        $getData("code").then(function(code) {
            if (code) document.getElementById("code").value = code;
        }).catch(function() {});
    } catch(e) {}

    document.getElementById("code").addEventListener("keydown", function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runConsole(); }
    });
    document.getElementById("code").addEventListener("input", function() {
        try { $putData("code", document.getElementById("code").value); } catch(e) {}
    });

    setInterval(updateUptime, 1000);
    setInterval(pollBridgeStatus, 2000);
    pollBridgeStatus();
    pollCommands();   // re-chains itself (long-poll)
    updateUI();
}

// ---------------------------------------------------------------- Bridge ---

function pollBridgeStatus() {
    try {
        var x = new XMLHttpRequest();
        x.open("GET", bridgeUrl("/status"), true);
        x.timeout = 2000;
        x.onload = function() {
            if (x.status === 401) { handleUnauthorized(); setBridgeDown(); return; }
            if (x.status !== 200) { setBridgeDown(); return; }
            try {
                var data = JSON.parse(x.responseText);
                var wasPT = S.ptConnected;
                S.bridgeUp = true;
                S.ptConnected = data.connected;
                if (!wasPT && S.ptConnected) addEntry("ok", "Connected to the MCP server");
                if (wasPT && !S.ptConnected) addEntry("warn", "The MCP server stopped polling");
                // The file channel runs in the Script Engine, with or without this window.
                try {
                    $se("fileBridgeStatus").then(function(s) {
                        try { S.fileBridge = JSON.parse(s); } catch(e) {}
                        updateUI();
                    }).catch(function() {});
                } catch(e) {}
                updateUI();
            } catch(e) {
                setBridgeDown();
            }
        };
        x.onerror = x.ontimeout = function() {
            if (S.bridgeUp) addEntry("warn", "Lost the MCP server", "Nothing listens on 127.0.0.1:54321 — is your agent still running?");
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
    updateUI();
}

/* Chained long-poll: the bridge holds /next until there is something (or ~2 s)
   and returns ALL queued commands at once. It always re-chains — no branch
   leaves the loop dead. */
function pollCommands() {
    if (!S.bridgeUp) { setTimeout(pollCommands, 500); return; }
    var again = function() { setTimeout(pollCommands, 0); };
    try {
        var x = new XMLHttpRequest();
        x.open("GET", bridgeUrl("/next"), true);
        x.timeout = 8000;   // looser than the bridge's long-poll
        x.onload = function() {
            if (x.status === 401) { handleUnauthorized(); setTimeout(pollCommands, 1000); return; }
            if (x.status === 200 && x.responseText) {
                var batch = x.responseText;
                try {
                    // One runCode for the whole batch: each command carries its own
                    // try/catch, so one bad command does not take down the others.
                    $se("runCode", batch);
                    var lines = batch.split("\n").filter(function(l) { return l.trim(); });
                    S.commandCount += lines.length;
                    describeBatch(lines);
                } catch(e) {
                    addEntry("err", "Packet Tracer could not run a command", e.message);
                }
                updateUI();
            }
            again();
        };
        x.onerror = x.ontimeout = function() { setTimeout(pollCommands, 500); };
        x.send();
    } catch(e) {
        setTimeout(pollCommands, 1000);
    }
}

// ------------------------------------------------------- What the agent did -

/* Turns the JavaScript the server sends into sentences. Anything not recognised
   is a read (query, console poll) and is folded into one counting row. */
var PATTERNS = [
    [/lwAddDevice\("((?:[^"\\]|\\.)*)",\s*\d+,\s*"((?:[^"\\]|\\.)*)"/, function(m) { return "Added " + m[1] + " (" + m[2] + ")"; }, "device"],
    [/addDevice\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"/, function(m) { return "Added " + m[1] + " (" + m[2] + ")"; }, "device"],
    [/(?:lwAddLink|addLink)\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"/,
        function(m) { return "Cabled " + m[1] + " " + shortPort(m[2]) + " ↔ " + m[3] + " " + shortPort(m[4]); }, "link"],
    [/configureIosDevice\("((?:[^"\\]|\\.)*)"/, function(m) { return "Configured " + m[1]; }, "config"],
    [/configurePcIp\("((?:[^"\\]|\\.)*)",\s*true/, function(m) { return m[1] + " gets its IP by DHCP"; }, "ip"],
    [/configurePcIp\("((?:[^"\\]|\\.)*)",\s*false,\s*"([^"]*)"/, function(m) { return "Set " + m[1] + " to " + m[2]; }, "ip"],
    [/addModule\("((?:[^"\\]|\\.)*)",\s*"([^"]*)",\s*"([^"]*)"/, function(m) { return "Installed " + m[3] + " on " + m[1] + " (slot " + m[2] + ")"; }, "device"],
    [/swapLaptopToWireless\("((?:[^"\\]|\\.)*)"/, function(m) { return m[1] + " switched to Wi-Fi"; }, "ip"],
    [/removeDevice\(/, function() { return "Removed a device"; }, "device"]
];

function shortPort(p) {
    return p.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa").replace("Ethernet", "Eth");
}

function describeBatch(lines) {
    var reads = 0;
    lines.forEach(function(line) {
        for (var i = 0; i < PATTERNS.length; i++) {
            var m = line.match(PATTERNS[i][0]);
            if (m) { addEntry("ok", PATTERNS[i][1](m), "", PATTERNS[i][2]); return; }
        }
        reads++;
    });
    if (reads) addRead(reads);
}

function addRead(n) {
    var last = S.entries[S.entries.length - 1];
    if (last && last.type === "read") {
        last.count += n;
        last.ts = clock();
        last.text = "Read from Packet Tracer ×" + last.count;
        renderList();
        return;
    }
    addEntry("read", "Read from Packet Tracer" + (n > 1 ? " ×" + n : ""));
    S.entries[S.entries.length - 1].count = n;
}

// ------------------------------------------------------------- Activity ----

function clock() {
    return new Date().toLocaleTimeString("en-GB", { hour12: false });
}

function addEntry(type, text, detail, kind) {
    S.entries.push({ ts: clock(), type: type, text: text, detail: detail || "", count: 1, kind: kind || type, fresh: true });
    if (S.entries.length > S.maxEntries) S.entries.splice(0, S.entries.length - S.maxEntries);
    if (type === "err") S.errCount++;
    renderList();
}

var ICONS = {
    device: '<rect x="2.5" y="3.5" width="11" height="9" rx="2"/><path d="M5.5 12.5v1.5M10.5 12.5v1.5"/>',
    link:   '<circle cx="4" cy="8" r="2"/><circle cx="12" cy="8" r="2"/><path d="M6 8h4"/>',
    config: '<path d="M3.5 5.5 6 8l-2.5 2.5M8 11h4.5"/>',
    ip:     '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c1.6 1.6 2.4 3.4 2.4 5.5S9.6 11.9 8 13.5C6.4 11.9 5.6 10.1 5.6 8S6.4 4.1 8 2.5"/>',
    read:   '<path d="M1.8 8S4 3.8 8 3.8 14.2 8 14.2 8 12 12.2 8 12.2 1.8 8 1.8 8z"/><circle cx="8" cy="8" r="1.8"/>',
    ok:     '<path d="m3.5 8.5 3 3 6-7"/>',
    warn:   '<path d="M8 2.8 14 13H2z"/><path d="M8 7v2.6M8 11.4v.1"/>',
    err:    '<circle cx="8" cy="8" r="5.5"/><path d="m6 6 4 4M10 6l-4 4"/>',
    info:   '<circle cx="8" cy="8" r="5.5"/><path d="M8 7.2v3.6M8 5.2v.1"/>',
    tool:   '<path d="M10.5 2.5a3 3 0 0 0-3.2 4L2.8 11a1.3 1.3 0 0 0 1.9 1.9l4.5-4.5a3 3 0 0 0 4-3.2l-1.8 1.8-1.7-.4-.4-1.7z"/>'
};

function icon(kind) {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
        (ICONS[kind] || ICONS.info) + "</svg>";
}

function renderList() {
    var list = document.getElementById("list");
    var shown = S.entries.filter(function(e) {
        return S.filter === "all" || e.type === "err" || e.type === "warn";
    });
    if (!shown.length) {
        list.innerHTML = S.filter === "all"
            ? '<div class="empty"><div><b>Nothing yet</b>Ask your AI agent to build a network — every action shows up here.</div></div>'
            : '<div class="empty"><div><b>No problems</b>Errors and warnings would show up here.</div></div>';
    } else {
        list.innerHTML = shown.map(function(e) {
            // Only a row added since the last render animates in; re-renders stay still.
            var cls = "row " + e.type + (e.fresh ? " new" : "");
            e.fresh = false;
            return '<div class="' + cls + '">' +
                '<span class="ico">' + icon(e.kind) + "</span>" +
                '<span class="msg">' + escapeHtml(e.text) +
                (e.detail ? '<span class="detail">' + escapeHtml(e.detail) + "</span>" : "") +
                "</span>" +
                '<span class="time">' + e.ts + "</span></div>";
        }).join("");
        list.scrollTop = list.scrollHeight;
    }
    setText("errCount", S.errCount ? String(S.errCount) : "");
}

function setFilter(f) {
    S.filter = f;
    document.getElementById("f-all").className = "filter" + (f === "all" ? " on" : "");
    document.getElementById("f-err").className = "filter" + (f === "err" ? " on" : "");
    renderList();
}

function copyLogs() {
    var text = S.entries.map(function(e) {
        return e.ts + "  " + e.type.toUpperCase() + "  " + e.text + (e.detail ? "\n          " + e.detail : "");
    }).join("\n");
    navigator.clipboard.writeText(text).then(function() {
        addEntry("info", "Activity copied to the clipboard");
    }).catch(function(e) { addEntry("err", "Could not copy", String(e)); });
}

// --------------------------------------------------------------- Status ----

function updateUI() {
    var fb = S.fileBridge && S.fileBridge.active;
    var state, sub, cls;
    if (S.tokenOk === false) {
        state = "Security token missing"; cls = "err";
        sub = "Start your AI agent with the MCP server once, then reopen this window";
    } else if (S.ptConnected) {
        state = "Connected to your agent"; cls = "ok";
        sub = "Your AI is driving Packet Tracer" + (fb ? " · keeps working if you close this window" : "");
    } else if (S.bridgeUp) {
        state = "Connecting…"; cls = "pend";
        sub = "The MCP server is up; waiting for the first poll";
    } else {
        state = "Waiting for your agent"; cls = "pend";
        sub = "Start Claude Code, OpenCode or Codex with the packet-tracer MCP";
    }
    document.getElementById("hero").className = "status hero " + cls;
    setText("stateText", state);
    setText("stateSub", sub);

    setLed("httpDot", S.ptConnected ? "ok" : (S.bridgeUp ? "pend" : ""));
    setText("httpSub", S.ptConnected ? ":54321 · live" : (S.bridgeUp ? ":54321 · starting" : ":54321 · offline"));

    setLed("fileDot", fb ? "ok" : "");
    setText("fileSub", fb ? "Active" + (S.fileBridge.count ? " · " + S.fileBridge.count + " cmds" : "") : "Inactive");

    setLed("tokenDot", S.tokenOk === true ? "ok" : (S.tokenOk === false ? "err" : ""));
    setText("tokenSub", S.tokenOk === true ? "Loaded" : (S.tokenOk === false ? "Not found" : "Reading…"));

    setText("cmdCount", String(S.commandCount));
}

function updateUptime() {
    var s = Math.floor((Date.now() - S.sessionStart) / 1000);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    setText("uptime", h ? h + ":" + pad2(m) : pad2(m) + ":" + pad2(s % 60));
}

// -------------------------------------------------------------- Console ----

function toggleConsole() {
    var adv = document.getElementById("adv");
    adv.classList.toggle("adv-open");
    var open = adv.classList.contains("adv-open");
    setText("advToggle", open ? "Hide console" : "Console");
    if (open) document.getElementById("code").focus();
}

function showTab(name) {
    document.getElementById("panel").className = "panel" + (name === "tools" ? " tools-on" : "");
    document.getElementById("tab-activity").className = name === "activity" ? "on" : "";
    document.getElementById("tab-tools").className = name === "tools" ? "on" : "";
}

// ---------------------------------------------------------------- Tools ----

function toolCheckBridge() {
    var t0 = Date.now();
    var x = new XMLHttpRequest();
    x.open("GET", bridgeUrl("/status"), true);
    x.timeout = 3000;
    x.onload = function() {
        var ms = Date.now() - t0;
        if (x.status === 401) { addEntry("err", "Bridge answered, but rejected the token", "Use Reload token, or restart your agent", "tool"); return; }
        try {
            var d = JSON.parse(x.responseText);
            addEntry(d.connected ? "info" : "warn", "Bridge answered in " + ms + " ms",
                (d.connected ? "Polling normally" : "Not polling right now") +
                (d.last_poll_ago != null ? " · last poll " + d.last_poll_ago + " s ago" : ""), "tool");
        } catch(e) { addEntry("err", "Bridge sent something unexpected (" + x.status + ")", "", "tool"); }
        showTab("activity");
    };
    x.onerror = x.ontimeout = function() {
        addEntry("err", "No bridge on 127.0.0.1:54321", "Start your AI agent with the packet-tracer MCP", "tool");
        showTab("activity");
    };
    x.send();
}

function toolCanvas() {
    try {
        $se("mcpCanvasSummary").then(function(raw) {
            var d = JSON.parse(raw);
            var parts = Object.keys(d.byType).sort().map(function(k) { return d.byType[k] + " " + k; });
            addEntry("info", d.devices + " devices · " + d.links + " links on the canvas", parts.join(" · ") || "Empty canvas", "tool");
            showTab("activity");
        }).catch(function(e) { addEntry("err", "Could not read the canvas", String(e), "tool"); showTab("activity"); });
    } catch(e) { addEntry("err", "Script Engine unavailable", String(e), "tool"); }
}

function toolReloadToken() {
    _tokenReloads = 0;
    loadToken(function(ok) {
        addEntry(ok ? "info" : "err", ok ? "Token reloaded from disk" : "Token still not found", "", "tool");
        showTab("activity");
    });
}

function toolDiagnostics() {
    var lines = [
        "MCP Control Center V6",
        "HTTP bridge: " + (S.ptConnected ? "connected" : (S.bridgeUp ? "up, not polling" : "offline")),
        "File bridge: " + (S.fileBridge ? JSON.stringify(S.fileBridge) : "unknown"),
        "Token: " + (S.tokenOk === true ? "loaded" : (S.tokenOk === false ? "missing" : "unknown")),
        "Commands this session: " + S.commandCount,
        "User agent: " + navigator.userAgent
    ];
    var finish = function(extra) {
        var text = lines.concat(extra || []).join("\n");
        navigator.clipboard.writeText(text).then(function() {
            addEntry("info", "Diagnostics copied to the clipboard", "", "tool");
        }).catch(function(e) { addEntry("err", "Could not copy", String(e), "tool"); });
        showTab("activity");
    };
    try {
        $se("mcpCanvasSummary").then(function(raw) { finish(["Canvas: " + raw]); }).catch(function() { finish(); });
    } catch(e) { finish(); }
}

function toolClear() {
    S.entries = [];
    S.errCount = 0;
    renderList();
    showTab("activity");
}

/* Sends the code through the bridge, the same path the agent's commands take. */
function runConsole() {
    var code = document.getElementById("code").value.trim();
    if (!code) return;
    if (!S.bridgeUp) {
        addEntry("warn", "Nothing to run it with", "The MCP server is not running. Start your AI agent first.");
        return;
    }
    try {
        var x = new XMLHttpRequest();
        x.open("POST", bridgeUrl("/queue"), true);
        x.setRequestHeader("Content-Type", "text/plain");
        x.timeout = 5000;
        x.onload = function() {
            if (x.status === 401) { handleUnauthorized(); return; }
            addEntry(x.status === 200 ? "info" : "err",
                x.status === 200 ? "Console script queued" : "The bridge refused the script (" + x.status + ")",
                code.length > 160 ? code.substring(0, 160) + "…" : code);
        };
        x.onerror = x.ontimeout = function() { addEntry("err", "Could not reach the bridge"); };
        x.send(code);
    } catch(e) {
        addEntry("err", "Could not send the script", String(e));
    }
}

function clearCode() {
    document.getElementById("code").value = "";
    try { $putData("code", ""); } catch(e) {}
}

// ---------------------------------------------------------------- Helpers --

function setText(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }
function setLed(id, cls) { var el = document.getElementById(id); if (el) el.className = "led" + (cls ? " " + cls : ""); }
function pad2(n) { return n < 10 ? "0" + n : String(n); }
function escapeHtml(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
