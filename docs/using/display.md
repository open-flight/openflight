---
icon: lucide/tv
---

# TV Display Mode

OpenFlight serves a fullscreen-friendly browser display for tablets, TV
browsers, or a Chrome tab cast to a Chromecast.

## Setup

1. Start OpenFlight as usual:

    ```bash
    scripts/start-kiosk.sh
    ```

2. Find the OpenFlight host on your LAN — its hostname or its IP address.

3. From another laptop, tablet, or TV browser, open:

    ```
    http://<openflight-host>:8080/display
    ```

4. For Chromecast, open that page in Chrome and use Chrome's built-in **Cast**
   feature to cast the tab.

!!! tip "Prefer the hostname over the IP"

    Raspberry Pi OS broadcasts its hostname over mDNS (Avahi), so
    `http://openflight.local:8080/display` keeps working even after the Pi's
    DHCP lease expires and it returns on a different address. A bookmarked IP
    breaks unless you reserved it on your router.

    Set the name in Raspberry Pi Imager's **Hostname** field when you flash the
    card. The default is `raspberrypi`, i.e. `raspberrypi.local`.

    The viewing device has to support mDNS. macOS, iOS, Windows 10+, and most
    Linux desktops do — some smart-TV browsers do not, so use the IP there.

## Limitations

This is browser and tab casting only. OpenFlight does not include native Cast
SDK support.

If the display is on a different port, pass `--web-port` and use that port in
the URL — see [running & modes](running.md#frequently-used-flags).

## Related

- [Auto-start & kiosk mode](../setup/auto-start.md) — fullscreen on the Pi's own
  display, and starting on boot
- [Running & modes](running.md) — the full set of run options
