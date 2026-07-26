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
| 3 | **SignPath.io free OSS code signing** | Free for OSS *(needs an established project — stars/history)* | ✅ Yes, on Windows | Medium (apply + wire into CI) |
| 4 | **winget** distribution (`winget install …`) | Free | ✅ Largely (trusted install channel) | Medium (manifest PR per release) |
| 5 | **Paid signing** — Azure Trusted Signing (~$10/mo) or an EV cert | $$ | ✅ Yes | Medium |

> **There is no free, one-click way to remove the Windows Authenticode warning.** That specifically
> needs a code-signing certificate (paid, or SignPath once eligible). Everything free is either
> "wait for reputation to build" or "distribute through a trusted channel (winget)".

### Recommended path (where this project is now)
1. ✅ **Option 1 shipped** — launcher + README "Run anyway" steps handle the warning today.
2. ✅ **Option 2 shipped** — `v0.1.0` is released with CI-built Windows/macOS binaries.
3. **Let reputation accrue** — SmartScreen eases on its own as more people download + run the `.exe`
   without incident. Free, zero effort, just slow.
4. **SignPath — later.** Its free OSS programme wants an *established* project, so it's not available
   on day one. Revisit once the repo has some traction; it's the cleanest free way to kill the warning.
5. **winget — the free route that works without traction.** Avoids the download warning via a manifest
   PR to `microsoft/winget-pkgs` (no cost, no signing), using the release binary you already publish.
   Not one-click to set up, but the realistic free win when you want it.

> **Free supply-chain trust (already added):** the release workflow attaches **GitHub build
> provenance attestations** (`actions/attest-build-provenance`) to each binary — cryptographic proof
> they were built by this repo's CI. It's free and automatic, but note it does **not** affect
> SmartScreen/Gatekeeper; it's a trust/verification signal, not a signature.

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

Release packaging details (see `.github/workflows/release.yml`):

- **macOS ships as `Flatfinder-macos.zip`**, not a bare binary — a raw Mach-O loses its execute
  bit over a plain download, so double-clicking it does nothing. Zipping preserves `+x`
  (Archive Utility restores it), making *unzip → right-click → Open* work for non-technical users.
- **The release body comes from [`.github/RELEASE_NOTES.md`](.github/RELEASE_NOTES.md)** — a
  non-technical download-and-run walkthrough, so the Releases page stands alone without the README.
- **Self-update in frozen builds** (menu → Update) downloads the new platform binary from the
  latest release *next to* the running one (a running executable can't overwrite itself) and, on
  Windows, silently re-points the Desktop shortcut on the new binary's first launch.

> ⚠️ The included workflow produces **unsigned** binaries — verify the first build via the
> "Run workflow" button before relying on it, and treat SmartScreen/Gatekeeper as expected until
> you add signing (option 3+).

## macOS Gatekeeper cheatsheet (for your README/users)

- First launch: **right-click the app/`.command` → Open → Open** (not double-click).
- Or clear quarantine: `xattr -dr com.apple.quarantine "Launch Flatfinder.command"`.
- Fully removing it requires notarization (paid Apple Developer account, $99/yr).
