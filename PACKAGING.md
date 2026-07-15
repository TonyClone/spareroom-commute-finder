# Packaging & the "Windows protected your PC" problem

Short version: **you cannot make an unsigned download stop triggering SmartScreen / Gatekeeper for free with a code trick** — the warning is about *who signed it*, not *what it does*. Below are the real options, cheapest first. The default (double-click launcher) already works today; the rest are upgrades.

## The reality

- **Windows SmartScreen / Defender**: any file downloaded from the internet gets a "Mark of the Web". Unsigned `.bat` and unsigned `.exe` both show *"Windows protected your PC"* until they earn reputation or are code-signed. A PyInstaller `.exe` is **not** safer here — it often makes things worse (antivirus false positives on one-file bundles).
- **macOS Gatekeeper**: downloaded apps/scripts from an unidentified developer are blocked on first open until right-click → Open, or notarized (paid Apple Developer account).

## Options, cheapest first

| # | Approach | Cost | Removes the warning? | Effort |
|---|----------|------|----------------------|--------|
| 1 | **Double-click launcher + clear instructions** (current default) | Free | No — one "More info → Run anyway" click | Done |
| 2 | **GitHub Release binaries** (PyInstaller, built by CI) | Free | No — still unsigned | Low (workflow included) |
| 3 | **SignPath.io free OSS code signing** | Free for open source | ✅ Yes, on Windows | Medium (apply + wire into CI) |
| 4 | **winget** distribution (`winget install Flatfinder`) | Free | ✅ Yes (winget packages are vetted; no SmartScreen) | Medium (manifest PR per release) |
| 5 | **Paid signing** — Azure Trusted Signing (~$10/mo) or an EV cert | $$ | ✅ Yes | Medium |

### Recommended path for a public, non-technical audience
1. Ship **option 1** now (works, zero cost). The README's "if Windows warns" steps cover it.
2. Cut a **GitHub Release** with **option 2** binaries so users get a single file to download (the [`release.yml`](.github/workflows/release.yml) workflow builds them on `git tag v0.1.0`).
3. Apply for **SignPath (option 3)** — it's free for OSS and is the thing that actually removes the Windows warning. Once approved, sign the Release `.exe` in CI. This is the highest-impact upgrade.
4. Optionally publish to **winget (option 4)** for a truly frictionless `winget install`.

## Building a binary locally

```bash
pip install . pyinstaller
pyinstaller --onefile --console --name Flatfinder \
  --collect-submodules flatfinder --collect-submodules pydantic \
  --exclude-module streamlit --exclude-module pandas --exclude-module pytest \
  packaging/flatfinder_launcher.py
# → dist/Flatfinder(.exe)
```

The executable reads/writes `config.yaml`, `.env` and `data/` **next to itself** (see the
`sys.frozen` branch in [`config.py`](src/flatfinder/config.py)), so ship it in its own folder.
The bundled build is the **menu/CLI** only; the Streamlit dashboard stays a `pip install` feature.

> ⚠️ The included workflow produces **unsigned** binaries — verify the first build via the
> "Run workflow" button before relying on it, and treat SmartScreen/Gatekeeper as expected until
> you add signing (option 3+).

## macOS Gatekeeper cheatsheet (for your README/users)

- First launch: **right-click the app/`.command` → Open → Open** (not double-click).
- Or clear quarantine: `xattr -dr com.apple.quarantine "Launch Flatfinder.command"`.
- Fully removing it requires notarization (paid Apple Developer account, $99/yr).
