# Building the Rock Head Gradient Analyzer Installer

This guide turns the Streamlit app into a Windows installer (`RockHeadApp_Setup.exe`) that anyone can install on a Windows PC **without needing Python**.

## What you end up with

A single file: **`RockHeadApp_Setup.exe`** (~150–200 MB).

A user double-clicks it → Next, Next, Install → done. They get a Start Menu entry and (optionally) a desktop icon. Double-click the icon → app opens in their browser. Uninstall via Windows Settings like any other program.

## Two-stage build

1. **Stage 1 — PyInstaller** turns Python + Streamlit + your script into a folder of files including `RockHeadApp.exe`.
2. **Stage 2 — Inno Setup** wraps that folder into a single `RockHeadApp_Setup.exe` installer.

You can stop after Stage 1 if you just want to zip the folder and email it. Stage 2 is for a polished installer experience.

---

## What you need on your Windows PC

| Tool | Where to get it | Required for |
|---|---|---|
| Python 3.10+ | https://www.python.org/downloads/ (tick "Add to PATH") | Stage 1 |
| Inno Setup | https://jrsoftware.org/isdl.php (free) | Stage 2 |

That's it.

## File checklist

Put **all of these** in one folder before you start:

- `rockhead_gradient_app.py` — the Streamlit app
- `launcher.py` — wrapper that PyInstaller bundles
- `requirements.txt` — Python dependencies
- `config.toml` — Streamlit config (silences first-run prompts)
- `sample_pile_data.csv` — sample data shipped with the app
- `build_exe.bat` — Stage 1 build script
- `installer.iss` — Stage 2 Inno Setup script

---

## Stage 1 — Build the .exe

1. Open the folder in File Explorer.
2. Double-click **`build_exe.bat`**.
3. A terminal window opens. It will:
   - Check Python is installed
   - Install dependencies + PyInstaller
   - Build the .exe (takes 3–10 minutes depending on your PC)
4. When it finishes you'll see `=== BUILD COMPLETE ===`.
5. Test it: open `dist\RockHeadApp\` and double-click `RockHeadApp.exe`. After ~10 seconds your browser should open with the app.

If this works, **you already have a distributable app** — just zip the entire `dist\RockHeadApp\` folder, send it to someone, they unzip and double-click `RockHeadApp.exe`. No Python needed on their end. You can stop here.

If you want a real installer with Start Menu entries, continue to Stage 2.

## Stage 2 — Wrap into an installer

1. Install Inno Setup from https://jrsoftware.org/isdl.php (default options are fine).
2. Open `installer.iss` by right-clicking it → **Open with → Inno Setup Compiler**.
3. (Optional) Edit the top of the file:
   - Change `MyAppPublisher` to your name or company
   - Change `MyAppVersion` if you bump the version
4. Press **F9** (or Build → Compile).
5. Inno Setup creates `Output\RockHeadApp_Setup.exe`.

That's your installer. Send it to anyone. They double-click → next → next → done.

---

## Troubleshooting

### Stage 1 errors

**`'python' is not recognized`**
Python isn't installed or wasn't added to PATH. Reinstall Python and tick "Add Python to PATH" on the first installer screen.

**`ERROR: Failed building wheel for ...`**
Some package needs a C compiler. Update pip first: `python -m pip install --upgrade pip`, then re-run.

**Build finishes but `.exe` crashes immediately when run**
Open a terminal in `dist\RockHeadApp\` and run `RockHeadApp.exe` from there — you'll see the actual error. Most common cause: a missing module that PyInstaller didn't auto-detect. Add it to `build_exe.bat` as `--hidden-import=name_of_module` and rebuild.

**App opens but shows "ModuleNotFoundError: scipy" or similar**
Add `--collect-all scipy` (or whichever module) to `build_exe.bat` and rebuild.

### Stage 2 errors

**`File not found: dist\RockHeadApp\*`**
You haven't run Stage 1 yet, or it failed. Re-run `build_exe.bat`.

**Installer runs but app fails on user's PC**
Test on a clean Windows PC (or VM) without Python installed. If it works on yours but not theirs, you likely have a system DLL dependency. Add `--collect-binaries` flags to `build_exe.bat`, or accept that very old Windows versions may not be supported.

### Antivirus flags the .exe

PyInstaller bundles trigger heuristic detection on some antivirus software (Windows Defender usually fine; some corporate AVs aren't). Two mitigations:
- **Code-sign the .exe** — requires a paid code-signing certificate (~$100–300/year). Eliminates most AV warnings. Worth it for distribution within a firm.
- **Submit a false-positive report** to the AV vendor. Free but slow.

For a single-firm internal tool, the easiest fix is to ask IT to whitelist the file hash.

---

## Updating the app

When you change `rockhead_gradient_app.py`:

1. Re-run `build_exe.bat` (rebuilds Stage 1)
2. Re-run Inno Setup compile on `installer.iss` (rebuilds Stage 2)
3. Bump `MyAppVersion` in `installer.iss` so users can see what version they have

The installer will overwrite the old install cleanly.

---

## Size expectations

- `dist\RockHeadApp\` folder: ~400–600 MB (Streamlit + pandas + scipy + plotly are heavy)
- `RockHeadApp_Setup.exe`: ~150–250 MB (Inno Setup compresses with LZMA2)

This is normal for Python apps bundled this way. There's no realistic way to get it smaller without rewriting in another language.
