# Govee LAN Control — Home Assistant integration

A custom integration that controls Govee lamps and LED strips **directly over the local network** (LAN API, UDP) — no cloud, no API key. Protocol: [official Govee guide](https://app-h5.govee.com/user-manual/wlan-guide).

**Features:**
- On/off, brightness, RGB color, color temperature (2000–9000 K)
- Multiple lamps: each lamp is added separately (auto-discovery or by IP)
- Automatic tracking of lamp IP changes (via periodic network scan)
- Status polling every 10 seconds plus instant push replies from the device

## Step 1. Prepare the lamps

1. The lamp must **support the LAN API**. The list of supported models is at the end of the [Govee guide](https://app-h5.govee.com/user-manual/wlan-guide) (H6159, H619A/B/C/E, H615A–E, H6072, H6046 and many others).
2. Connect the lamp to a **2.4 GHz** Wi-Fi network using the Govee Home app.
3. In the Govee Home app open the device → gear icon (settings) → enable **LAN Control**. If the option is missing, update the device firmware and the app; if it is still missing, the model does not support the LAN API.
4. **Recommended:** give the lamp a permanent IP address (DHCP reservation in your router). The integration can follow IP changes, but a fixed address is more reliable.

## Step 2. Install the integration

### Option A: via HACS (recommended — one-click updates)

1. HACS → menu (⋮) → **Custom repositories**.
2. Paste `https://github.com/dekamaru/ha_govee_lan_control`, category **Integration**, click Add.
3. Find "Govee LAN Control" in HACS and click **Download**.
4. Restart Home Assistant.

### Option B: manual

1. Copy the `custom_components/govee_lan` folder into your Home Assistant configuration directory so that you end up with:
   ```
   <config>/custom_components/govee_lan/manifest.json
   ```
   `<config>` is the folder that contains `configuration.yaml` (`/config` on Home Assistant OS; the Samba or File editor add-ons make copying easy).
2. Restart Home Assistant.

## Step 3. Add the lamps

1. **Settings → Devices & Services → Add Integration** → search for **"Govee LAN Control"**.
2. Pick a method:
   - **Discover devices automatically** — the integration sends a multicast scan and lists the lamps it finds. Pick one from the list.
   - **Add device by IP address** — enter the lamp's IP (visible in your router or in the Govee Home app → device settings). The integration verifies that the device responds.
3. **Repeat for each lamp** — one config entry = one lamp. Already-configured lamps are hidden from the discovery list.

Each lamp appears as a `light.*` entity with brightness, RGB and color temperature — use it in cards, automations and scenes as usual.

## Diagnostics

If a lamp is not discovered or does not respond, run the test script from any computer on the same network (requires only Python 3.10+):

```bash
python tools/govee_scan.py
```

The script lists every lamp with LAN Control enabled (IP, model, ID). To test controlling a specific lamp:

```bash
python tools/govee_scan.py --ip 192.168.1.50 --on --color 255 0 0 --brightness 50
```

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Auto-discovery finds nothing | LAN Control is not enabled; the lamp and HA are on different VLANs/subnets; the router filters multicast (IGMP snooping). Fix: add the lamp **by IP** — that path does not need multicast. |
| "UDP port 4002 is in use" error | Conflict with the built-in **"Govee lights local"** integration (or other Govee LAN software on the same machine, e.g. SignalRGB). Govee devices always reply to port 4002, so only one program can listen on it. Remove the built-in integration's entries. |
| Lamp goes "unavailable" from time to time | Weak Wi-Fi signal at the lamp, or its IP changed — check that the address is reserved in the router. |
| Device added by IP but does not respond | Check that no firewall blocks UDP ports 4001–4003 and that HA and the lamp are on the same subnet. |

### Limitations

- Scenes/effects from the Govee app are not available through the LAN API (only on/off, brightness, color, color temperature).
- Segment control (different colors on parts of a strip) is not exposed by the public LAN API.

## Compatibility

Requires Home Assistant 2024.1 or newer. No dependencies — Python standard library only.
