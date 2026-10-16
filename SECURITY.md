# Security

Halbach ConnectHub runs a local web server on your PC and can expose filtered data to the internet through a tunnel, so security reports are welcome.

## Reporting a problem

Please report privately, not in a public issue: use GitHub's "Report a vulnerability" button on this repository (Security tab), or email levbot.py@gmail.com. Include what you did, what you expected and what happened. I'll answer as soon as I can.

## What is in scope

- A way for a visitor to read a field that was not allowed, or any key with a private-looking name.
- A way to reach the control panel (not just the live hub) through the tunnel.
- A way for a web page you have open to drive the control panel (token, `Host` or `Origin` bypass).
- A way to make the hub publish a file from the never-publish list, or from outside the folder you chose.
- A way to make a live module call something other than a service on `127.0.0.1`.
- A way for a drop-off submission to publish itself, switch itself on, escape the inbox folder, or run script on the site (for example through the Markdown renderer).

## What is out of scope

- Anything that already needs code running as you on your PC.
- Data you chose to allow: allowed fields are public by design.
- Denial of service against your own home connection beyond the documented viewer cap.

See `docs/ARCHITECTURE.md` for the threat model.
