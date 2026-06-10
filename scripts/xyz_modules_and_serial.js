var mods = [
  ["Internet", "0", "PT-CLOUD-NM-1S"],
  ["RTR-1", "0/0", "HWIC-2T"],
  ["RTR-4", "1", "NM-4A/S"],
  ["RTR-5", "0/0", "HWIC-2T"],
  ["RTR-6", "0/0", "HWIC-2T"]
];
var i;
for (i = 0; i < mods.length; i++) {
  var d = ipc.network().getDevice(mods[i][0]);
  if (!d) continue;
  if (typeof d.getPower === "function") d.setPower(false);
  d.addModule(mods[i][1], allModuleTypes[mods[i][2]], mods[i][2]);
}
addLink("Internet", "Serial0", "RTR-1", "Serial0/0/0", "serial");
addLink("RTR-1", "Serial0/0/1", "RTR-4", "Serial1/0", "serial");
addLink("RTR-4", "Serial1/1", "RTR-5", "Serial0/0/0", "serial");
addLink("RTR-4", "Serial1/2", "RTR-6", "Serial0/0/0", "serial");
addLink("RTR-5", "Serial0/0/1", "RTR-6", "Serial0/0/1", "serial");
for (i = 0; i < mods.length; i++) {
  var d2 = ipc.network().getDevice(mods[i][0]);
  if (!d2) continue;
  if (typeof d2.getPower === "function") {
    d2.setPower(true);
    if (typeof d2.skipBoot === "function") d2.skipBoot();
  }
}
