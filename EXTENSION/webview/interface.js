// ============================================================================
// MCP Control Center V6 — the window Packet Tracer shows for the MCP extension.
// Connection state, what the agent did, and an advanced JS console. The bridge
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
    [/lwAddDevice\("((?:[^"\\]|\\.)*)",\s*\d+,\s*"((?:[^"\\]|\\.)*)"/, function(m) { return "Added " + m[1] + " (" + m[2] + ")"; }],
    [/addDevice\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"/, function(m) { return "Added " + m[1] + " (" + m[2] + ")"; }],
    [/(?:lwAddLink|addLink)\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"/,
        function(m) { return "Cabled " + m[1] + " " + shortPort(m[2]) + " ↔ " + m[3] + " " + shortPort(m[4]); }],
    [/configureIosDevice\("((?:[^"\\]|\\.)*)"/, function(m) { return "Configured " + m[1]; }],
    [/configurePcIp\("((?:[^"\\]|\\.)*)",\s*true/, function(m) { return m[1] + " gets its IP by DHCP"; }],
    [/configurePcIp\("((?:[^"\\]|\\.)*)",\s*false,\s*"([^"]*)"/, function(m) { return "Set " + m[1] + " to " + m[2]; }],
    [/addModule\("((?:[^"\\]|\\.)*)",\s*"([^"]*)",\s*"([^"]*)"/, function(m) { return "Installed " + m[3] + " on " + m[1] + " (slot " + m[2] + ")"; }],
    [/swapLaptopToWireless\("((?:[^"\\]|\\.)*)"/, function(m) { return m[1] + " switched to Wi-Fi"; }],
    [/removeDevice\(/, function() { return "Removed a device"; }]
];

function shortPort(p) {
    return p.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa").replace("Ethernet", "Eth");
}

function describeBatch(lines) {
    var reads = 0;
    lines.forEach(function(line) {
        for (var i = 0; i < PATTERNS.length; i++) {
            var m = line.match(PATTERNS[i][0]);
            if (m) { addEntry("ok", PATTERNS[i][1](m)); return; }
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

function addEntry(type, text, detail) {
    S.entries.push({ ts: clock(), type: type, text: text, detail: detail || "", count: 1 });
    if (S.entries.length > S.maxEntries) S.entries.splice(0, S.entries.length - S.maxEntries);
    if (type === "err") S.errCount++;
    renderList();
}

var DOT = { ok: "ok", err: "err", warn: "pend", info: "", read: "", cmd: "" };

function renderList() {
    var list = document.getElementById("list");
    var shown = S.entries.filter(function(e) {
        return S.filter === "all" || e.type === "err" || e.type === "warn";
    });
    if (!shown.length) {
        list.innerHTML = '<div class="empty">' + (S.filter === "all"
            ? "Nothing yet. Ask your AI agent to build a network — every action shows up here."
            : "No problems so far.") + "</div>";
    } else {
        list.innerHTML = shown.map(function(e) {
            return '<div class="row ' + e.type + '">' +
                '<span class="time">' + e.ts + "</span>" +
                '<span class="dot ' + (DOT[e.type] || "") + '"></span>' +
                '<span class="msg">' + escapeHtml(e.text) +
                (e.detail ? '<span class="detail">' + escapeHtml(e.detail) + "</span>" : "") +
                "</span></div>";
        }).join("");
        list.scrollTop = list.scrollHeight;
    }
    setText("errCount", S.errCount ? String(S.errCount) : "");
}

function setFilter(f) {
    S.filter = f;
    document.getElementById("f-all").className = f === "all" ? "on" : "";
    document.getElementById("f-err").className = f === "err" ? "on" : "";
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
    var state, cls;
    if (S.tokenOk === false) { state = "Security token missing"; cls = "err"; }
    else if (S.ptConnected) { state = "Connected to your agent"; cls = "ok"; }
    else if (S.bridgeUp) { state = "MCP server found, connecting…"; cls = "pend"; }
    else { state = "Waiting for the MCP server"; cls = "pend"; }
    setText("stateText", state);
    setDot("stateDot", cls);

    setDot("httpDot", S.ptConnected ? "ok" : (S.bridgeUp ? "pend" : ""));
    setText("httpSub", S.ptConnected ? "127.0.0.1:54321 · live" : (S.bridgeUp ? "127.0.0.1:54321 · starting" : "Start your AI agent"));

    setDot("fileDot", fb ? "ok" : "");
    setText("fileSub", fb ? "Active · survives closing this" : "Inactive");

    setDot("tokenDot", S.tokenOk === true ? "ok" : (S.tokenOk === false ? "err" : ""));
    setText("tokenSub", S.tokenOk === true ? "Found · only your agent gets in" : (S.tokenOk === false ? "Not found on this machine" : "Reading…"));

    setText("cmdCount", String(S.commandCount));
}

function updateUptime() {
    var s = Math.floor((Date.now() - S.sessionStart) / 1000);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    setText("uptime", h ? h + ":" + pad2(m) : pad2(m) + ":" + pad2(s % 60));
}

// -------------------------------------------------------------- Console ----

function toggleConsole() {
    var c = document.getElementById("console");
    var open = !c.classList.contains("open");
    c.classList.toggle("open", open);
    document.getElementById("advToggle").textContent = open ? "Advanced ▾" : "Advanced ▸";
    if (open) document.getElementById("code").focus();
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
function setDot(id, cls) { var el = document.getElementById(id); if (el) el.className = "dot" + (cls ? " " + cls : ""); }
function pad2(n) { return n < 10 ? "0" + n : String(n); }
function escapeHtml(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
