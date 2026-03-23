# LG AutoBright

Automatic backlight control for LG TVs on macOS. Discovers your TV on the local network, connects via WebOS, and adjusts the backlight throughout the day based on a custom curve you draw.

<p align="center">
  <img src="screenshots/connect.png" width="380" alt="TV Discovery">
  &nbsp;&nbsp;
  <img src="screenshots/connected.png" width="380" alt="Connected View">
</p>

## Features

- **Auto-discovery** of LG TVs on the local network via mDNS + AirPlay
- **Interactive curve editor** to set backlight levels across 24 hours
- **Live preview** while dragging nodes on the curve
- **System tray** icon with hide-to-tray on close
- **Launch at login** option via macOS LaunchAgent
- **Enable/disable toggle** to pause automatic adjustments

## Requirements

- macOS (uses system Python for mDNS and WebOS connections)
- Python 3.10+
- An LG TV with WebOS on the same network

## Installation

```bash
git clone https://github.com/yourusername/lgtv-autobright.git
cd lgtv-autobright
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Dependencies

The app also requires [lgtv-autofind](https://github.com/yourusername/lgtv-autofind) for TV discovery. Install it in the same venv:

```bash
pip install -e ../lgtv-autofind  # or wherever your copy lives
```

> **Note:** TV discovery and WebOS communication are run via `/usr/bin/python3` (system Python) because Homebrew/venv Python lacks the Apple entitlements needed for mDNS port binding and local network access.

## Usage

```bash
source .venv/bin/activate
python -m lgtv_autobright.app
```

Or if installed:

```bash
lgtv-autobright
```

On first launch, the app scans for LG TVs on your network. Select your TV or enter its IP manually. The TV will show a pairing prompt on first connection.

### Backlight Curve

The curve editor lets you define backlight levels (0-100) throughout the day:

- **Click** on the chart to add a node
- **Drag** a node to adjust time and brightness
- **Right-click** a node to remove it

The curve wraps around midnight and updates are sent to the TV every 5 minutes. Changes are saved automatically as you edit.

## Architecture

| File | Purpose |
|---|---|
| `app.py` | PyQt6 main window, system tray, UI |
| `brightness.py` | Curve interpolation, scheduler, config |
| `tv.py` | TV connection via system Python subprocess |
| `discovery.py` | mDNS TV discovery via system Python subprocess |
| `scan_ui.py` | Standalone connect/settings UI (legacy) |

## Config

Settings are stored at `~/.config/lgtv-autobright/config.json`. The WebOS client key is stored in `.aiopylgtv.sqlite` in the working directory.

## License

MIT
