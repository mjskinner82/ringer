# swarm HUD

Tauri v2 port of `SwarmHUD.swift`. It reads the same `~/.config/swarm/config.toml` state settings as `swarm.py`, scans `<state_dir>/runs/*.json`, and emits the run array into the shared dashboard UI.

## Prerequisites

```bash
source ~/.cargo/env
cargo install tauri-cli --locked
```

Install the platform prerequisites from the Tauri v2 guide for your OS:

- macOS: Xcode Command Line Tools.
- Linux: WebKitGTK/AppIndicator development packages for your distro.
- Windows: Microsoft C++ Build Tools and WebView2.

## Development

```bash
cd hud
cargo tauri dev
```

No Node/npm toolchain is used. `build.rs` copies `../dashboard/dashboard.html` and `frontend/hud.js` into `hud/dist/` before Tauri builds or runs.

## Release Build

```bash
cd hud
cargo tauri build
```

On macOS this produces a `.app` bundle and `.dmg` under `hud/target/release/bundle/`.
