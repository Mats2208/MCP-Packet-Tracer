"""
Comprehensive test of ALL PTBuilder functions through the HTTP bridge.
Tests each function one-by-one to determine what works and what doesn't.

KEY INSIGHT: The PT Script Engine does NOT have XMLHttpRequest.
Only the QWebEngine webview has it. All result reporting must go through
window.webview.evaluateJavaScriptAsync() which is what reportResult() does.
"""
import urllib.request
import json
import time
import sys

BRIDGE = "http://127.0.0.1:54321"


def send_raw(js_code: str) -> int:
    """Queue a JS command (fire-and-forget)."""
    data = js_code.encode("utf-8")
    req = urllib.request.Request(f"{BRIDGE}/queue", data=data, method="POST")
    req.add_header("Content-Type", "text/plain")
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status


def wait_result(timeout: float = 10.0) -> str:
    """Wait for a result from PT via GET /result (long-poll)."""
    try:
        with urllib.request.urlopen(f"{BRIDGE}/result", timeout=timeout) as r:
            if r.status == 204:
                return "TIMEOUT: no result"
            return r.read().decode("utf-8")
    except Exception as e:
        return f"TIMEOUT/ERROR: {e}"


def send_and_wait(js_code: str, timeout: float = 10.0) -> str:
    """
    Queue a JS command that calls reportResult() internally,
    then wait for the result.
    reportResult() routes through the webview where XMLHttpRequest exists.
    """
    send_raw(js_code)
    return wait_result(timeout)


def send_expr(js_expr: str, timeout: float = 10.0) -> str:
    """
    Evaluate a JS expression in the Script Engine and get the result back.
    Wraps the expression with try/catch and calls reportResult().
    reportResult() is defined in userfunctions.js and routes HTTP through webview.
    """
    wrapped = (
        "try { var __r = (function(){ " + js_expr + " })(); "
        "reportResult(String(__r)); "
        "} catch(__e) { reportResult('ERROR:' + __e); }"
    )
    send_raw(wrapped)
    return wait_result(timeout)


def test(name: str, func, *args):
    """Run a test and print result."""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        result = func(*args)
        # Truncate long results
        display = result if len(result) < 500 else result[:500] + "..."
        print(f"  RESULT: {display}")
        success = not result.startswith("TIMEOUT") and not result.startswith("ERROR:")
        print(f"  STATUS: {'OK' if success else 'FAIL'}")
        return result, success
    except Exception as e:
        print(f"  EXCEPTION: {e}")
        print(f"  STATUS: FAIL")
        return str(e), False


def main():
    results = {}
    
    # Check bridge status first
    print("Checking bridge status...")
    try:
        with urllib.request.urlopen(f"{BRIDGE}/status", timeout=2) as r:
            status = json.loads(r.read().decode())
            print(f"  Bridge: connected={status['connected']}, last_poll={status['last_poll_ago']}s ago")
            if not status["connected"]:
                print("PT is not connected! Aborting.")
                sys.exit(1)
    except Exception as e:
        print(f"  Bridge not running: {e}")
        sys.exit(1)

    # ================================================================
    # PHASE 1: Test basic IPC API calls
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 1: Basic IPC API")
    print("#"*60)

    # Test ipc.network().getDeviceCount()
    r, ok = test("ipc.network().getDeviceCount()", 
                  send_expr, "return ipc.network().getDeviceCount()")
    results["ipc.network().getDeviceCount()"] = ok
    time.sleep(1)

    # Test ipc.appWindow() exists
    r, ok = test("ipc.appWindow() exists",
                  send_expr, "return typeof ipc.appWindow()")
    results["ipc.appWindow()"] = ok
    time.sleep(1)

    # Test getActiveWorkspace
    r, ok = test("getActiveWorkspace()",
                  send_expr, "return typeof ipc.appWindow().getActiveWorkspace()")
    results["getActiveWorkspace()"] = ok
    time.sleep(1)

    # Test getLogicalWorkspace
    r, ok = test("getLogicalWorkspace()",
                  send_expr, "return typeof ipc.appWindow().getActiveWorkspace().getLogicalWorkspace()")
    results["getLogicalWorkspace()"] = ok
    time.sleep(1)

    # ================================================================
    # PHASE 2: Test getDevices() and queryTopology()
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 2: Query functions")
    print("#"*60)

    # getDevices() - no filter
    r, ok = test("getDevices() - all devices",
                  send_and_wait, "reportResult(JSON.stringify(getDevices()))")
    results["getDevices()"] = ok
    time.sleep(1)

    # getDevices with filter
    r, ok = test("getDevices('router')",
                  send_and_wait, 'reportResult(JSON.stringify(getDevices("router")))')
    results["getDevices(filter)"] = ok
    time.sleep(1)

    # queryTopology() - our custom function
    r, ok = test("queryTopology()",
                  send_and_wait, "queryTopology()")
    results["queryTopology()"] = ok
    time.sleep(1)

    # ================================================================
    # PHASE 3: Test addDevice() with different models
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 3: addDevice() - test each model")
    print("#"*60)

    device_tests = [
        ("Router 2911",     "TestRouter1",  "2911",         100, 100),
        ("Router 1941",     "TestRouter2",  "1941",         200, 100),
        ("Router 2901",     "TestRouter3",  "2901",         300, 100),
        ("Router ISR4321",  "TestRouter4",  "ISR4321",      400, 100),
        ("Switch 2960",     "TestSwitch1",  "2960-24TT",    100, 200),
        ("Switch 3560",     "TestSwitch2",  "3560-24PS",    200, 200),
        ("PC-PT",           "TestPC1",      "PC-PT",        100, 300),
        ("Server-PT",       "TestServer1",  "Server-PT",    200, 300),
        ("Laptop-PT",       "TestLaptop1",  "Laptop-PT",    300, 300),
        ("Cloud-PT",        "TestCloud1",   "Cloud-PT",     400, 300),
        ("AccessPoint-PT",  "TestAP1",      "AccessPoint-PT", 500, 300),
    ]

    for label, name, model, x, y in device_tests:
        js = f'addDevice("{name}", "{model}", {x}, {y})'
        r, ok = test(f"addDevice({label}: {model})",
                      send_expr, f'return {js}')
        results[f"addDevice({model})"] = ok
        time.sleep(1.5)

    # Verify devices were created
    r, ok = test("Verify devices created (getDevices)",
                  send_and_wait, "reportResult(JSON.stringify(getDevices()))")
    print(f"  Devices in PT: {r}")
    time.sleep(1)

    # ================================================================
    # PHASE 4: Test addLink() with different cable types
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 4: addLink() - test cable types")
    print("#"*60)

    link_tests = [
        ("straight: Router-Switch", "TestRouter1", "GigabitEthernet0/0", "TestSwitch1", "FastEthernet0/1", "straight"),
        ("cross: Router-Router",    "TestRouter1", "GigabitEthernet0/1", "TestRouter2", "GigabitEthernet0/0", "cross"),
        ("straight: Switch-PC",     "TestSwitch1", "FastEthernet0/2", "TestPC1", "FastEthernet0", "straight"),
        ("straight: Switch-Server", "TestSwitch1", "FastEthernet0/3", "TestServer1", "FastEthernet0", "straight"),
        ("straight: Switch-Laptop", "TestSwitch1", "FastEthernet0/4", "TestLaptop1", "FastEthernet0", "straight"),
    ]

    for label, d1, p1, d2, p2, cable in link_tests:
        js = f'addLink("{d1}", "{p1}", "{d2}", "{p2}", "{cable}")'
        r, ok = test(f"addLink({label})",
                      send_expr, f"return {js}")
        results[f"addLink({cable}: {label.split(':')[0].strip()})"] = ok
        time.sleep(1.5)

    # ================================================================
    # PHASE 5: Test configurePcIp()
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 5: configurePcIp()")
    print("#"*60)

    # Static IP
    r, ok = test("configurePcIp(static)",
                  send_expr,
                  'configurePcIp("TestPC1", false, "192.168.1.2", "255.255.255.0", "192.168.1.1"); return "ok"')
    results["configurePcIp(static)"] = ok
    time.sleep(1.5)

    # DHCP
    r, ok = test("configurePcIp(dhcp)",
                  send_expr,
                  'configurePcIp("TestLaptop1", true); return "ok"')
    results["configurePcIp(dhcp)"] = ok
    time.sleep(1.5)

    # ================================================================
    # PHASE 6: Test configureIosDevice()
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 6: configureIosDevice()")
    print("#"*60)

    cmds = "enable\\nconf t\\nhostname TestR1\\ninterface GigabitEthernet0/0\\nip address 192.168.1.1 255.255.255.0\\nno shutdown\\nexit\\nexit"
    r, ok = test("configureIosDevice(router)",
                  send_expr,
                  f'configureIosDevice("TestRouter1", "{cmds}"); return "ok"')
    results["configureIosDevice(router)"] = ok
    time.sleep(2)

    # ================================================================
    # PHASE 7: Test addModule()
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 7: addModule()")
    print("#"*60)

    r, ok = test("addModule(HWIC-2T on 2911)",
                  send_expr,
                  'return addModule("TestRouter1", 1, "HWIC-2T")')
    results["addModule()"] = ok
    time.sleep(1.5)

    # ================================================================
    # PHASE 8: Test bidirectional functions (our custom ones)
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 8: Bidirectional interaction functions")
    print("#"*60)

    # renameDevice
    r, ok = test("renameDevice()",
                  send_and_wait,
                  'renameDevice("TestPC1", "RenamedPC1")')
    results["renameDevice()"] = ok
    time.sleep(1.5)

    # moveDevice
    r, ok = test("moveDevice()",
                  send_and_wait,
                  'moveDevice("RenamedPC1", 600, 400)')
    results["moveDevice()"] = ok
    time.sleep(1.5)

    # Rename back for cleanup
    send_and_wait('renameDevice("RenamedPC1", "TestPC1")')
    time.sleep(1)

    # deleteLink
    r, ok = test("deleteLink()",
                  send_and_wait,
                  'deleteLink("TestSwitch1", "FastEthernet0/4")')
    results["deleteLink()"] = ok
    time.sleep(1.5)

    # deleteDevice
    r, ok = test("deleteDevice()",
                  send_and_wait,
                  'deleteDevice("TestLaptop1")')
    results["deleteDevice()"] = ok
    time.sleep(1.5)

    # ================================================================
    # PHASE 9: Test device info methods
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 9: Device info methods")
    print("#"*60)

    # device.getName()
    r, ok = test("device.getName()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); return d.getName()')
    results["device.getName()"] = ok
    time.sleep(1)

    # device.getType()
    r, ok = test("device.getType()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); return d.getType()')
    results["device.getType()"] = ok
    time.sleep(1)

    # device.getModel()
    r, ok = test("device.getModel()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); return d.getModel()')
    results["device.getModel()"] = ok
    time.sleep(1)

    # device.getXCoordinate() / getYCoordinate() (NOT getX/getY)
    r, ok = test("device.getXCoordinate()/getYCoordinate()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); return d.getXCoordinate() + "," + d.getYCoordinate()')
    results["device.getXCoordinate()/getYCoordinate()"] = ok
    time.sleep(1)

    # device.getPortCount()
    r, ok = test("device.getPortCount()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); return d.getPortCount()')
    results["device.getPortCount()"] = ok
    time.sleep(1)

    # device.getPort() by name
    r, ok = test("device.getPort(name)",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); var p = d.getPort("GigabitEthernet0/0"); return p.getName()')
    results["device.getPort(name)"] = ok
    time.sleep(1)

    # port.getIpAddress()
    r, ok = test("port.getIpAddress()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); var p = d.getPort("GigabitEthernet0/0"); return p.getIpAddress()')
    results["port.getIpAddress()"] = ok
    time.sleep(1)

    # port.getSubnetMask()
    r, ok = test("port.getSubnetMask()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); var p = d.getPort("GigabitEthernet0/0"); return p.getSubnetMask()')
    results["port.getSubnetMask()"] = ok
    time.sleep(1)

    # port.getMacAddress()
    r, ok = test("port.getMacAddress()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); var p = d.getPort("GigabitEthernet0/0"); return p.getMacAddress()')
    results["port.getMacAddress()"] = ok
    time.sleep(1)

    # port.getBandwidth()
    r, ok = test("port.getBandwidth()",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); var p = d.getPort("GigabitEthernet0/0"); return p.getBandwidth()')
    results["port.getBandwidth()"] = ok
    time.sleep(1)

    # Enumerate all ports of a device
    r, ok = test("Enumerate ports of device",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); var count = d.getPortCount(); var ports = []; for(var i=0; i<count; i++) { var p = d.getPortAt(i); ports.push(p.getName()); } return JSON.stringify(ports)')
    results["enumerate device ports"] = ok
    time.sleep(1)

    # network.getLinkCount() and getLinkAt()
    r, ok = test("network.getLinkCount()",
                  send_expr,
                  'return ipc.network().getLinkCount()')
    results["network.getLinkCount()"] = ok
    time.sleep(1)

    # ================================================================
    # PHASE 10: Verify correct lw methods exist
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 10: LogicalWorkspace method verification")
    print("#"*60)

    r, ok = test("typeof lw.removeDevice (correct method)",
                  send_expr,
                  'var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace(); return typeof lw.removeDevice')
    results["lw.removeDevice exists"] = ok
    time.sleep(1)

    r, ok = test("typeof lw.deleteLink",
                  send_expr,
                  'var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace(); return typeof lw.deleteLink')
    results["lw.deleteLink exists"] = ok
    time.sleep(1)

    r, ok = test("typeof lw.createLink",
                  send_expr,
                  'var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace(); return typeof lw.createLink')
    results["lw.createLink exists"] = ok
    time.sleep(1)

    r, ok = test("typeof lw.moveCanvasItemBy",
                  send_expr,
                  'var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace(); return typeof lw.moveCanvasItemBy')
    results["lw.moveCanvasItemBy exists"] = ok
    time.sleep(1)

    r, ok = test("typeof lw.setCanvasItemRealPos",
                  send_expr,
                  'var lw = ipc.appWindow().getActiveWorkspace().getLogicalWorkspace(); return typeof lw.setCanvasItemRealPos')
    results["lw.setCanvasItemRealPos exists"] = ok
    time.sleep(1)

    r, ok = test("typeof device.moveToLocation",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); return typeof d.moveToLocation')
    results["device.moveToLocation exists"] = ok
    time.sleep(1)

    # ================================================================
    # PHASE 11: Verify the 2911 actual port list
    # ================================================================
    print("\n" + "#"*60)
    print("# PHASE 11: Router 2911 actual ports (catalog verification)")
    print("#"*60)

    r, ok = test("2911 actual ports",
                  send_expr,
                  'var d = ipc.network().getDevice("TestRouter1"); var count = d.getPortCount(); var ports = []; for(var i=0; i<count; i++) { ports.push(d.getPortAt(i).getName()); } return JSON.stringify(ports)')
    results["2911 actual ports"] = ok
    time.sleep(1)

    # ================================================================
    # CLEANUP: Remove all test devices
    # ================================================================
    print("\n" + "#"*60)
    print("# CLEANUP: Removing test devices")
    print("#"*60)

    cleanup_devices = [
        "TestRouter1", "TestRouter2", "TestRouter3", "TestRouter4",
        "TestSwitch1", "TestSwitch2",
        "TestPC1", "TestServer1", "TestCloud1", "TestAP1",
    ]
    for name in cleanup_devices:
        send_raw(f'try {{ ipc.appWindow().getActiveWorkspace().getLogicalWorkspace().removeDevice("{name}") }} catch(e) {{}}')
        time.sleep(0.5)
    
    # Wait and verify cleanup
    time.sleep(2)
    r, _ = test("Verify cleanup",
                send_and_wait, "reportResult(JSON.stringify(getDevices()))")

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n\n" + "="*60)
    print("SUMMARY OF ALL TESTS")
    print("="*60)
    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    print(f"\nTotal: {len(results)} tests | PASSED: {passed} | FAILED: {failed}")
    print()
    
    for name, ok in results.items():
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {name}")


if __name__ == "__main__":
    main()
