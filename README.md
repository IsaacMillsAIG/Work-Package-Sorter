# Work Package Sorter — Desktop App

Steel Fabrication Drawing Sequencer — Electron desktop app wrapping the Python PDF sorter.

---

## Folder Structure

```
work-package-sorter-app/
├── main.js                  ← Electron main process (window, Python runner)
├── preload.js               ← Secure bridge between UI and system
├── package.json             ← Node dependencies + build config
├── work_package_sorter.py   ← Python backend (copy yours here!)
├── renderer/
│   ├── index.html           ← App entry point
│   └── App.jsx              ← React UI
├── assets/
│   └── icon.ico             ← App icon (replace with your own)
├── BUILD.bat                ← Double-click to build the .exe installer
└── RUN_DEV.bat              ← Double-click to run in dev/test mode
```

---

## Setup (One-Time)

### Prerequisites
1. **Python** — already installed (you've been using it)
2. **Node.js** — download from https://nodejs.org (LTS version)

### Steps

1. **Copy your Python script** into this folder:
   ```
   work_package_sorter.py   ← must be in the same folder as main.js
   ```

2. **Run `BUILD.bat`** — double-click it. It will:
   - Install Node dependencies
   - Package everything into a `.exe` installer
   - Open the `dist/` folder when done

3. **Share the installer** — send the `.exe` from the `dist/` folder to your coworkers. They just double-click and install, no Python or Node required.

---

## Testing Before Building

Double-click `RUN_DEV.bat` to open the app in dev mode (with browser DevTools for debugging).

Python must be installed and in your PATH for this to work.

---

## App Features

- **Drag & drop** a PDF directly onto the app window
- **Progress log** streamed live from Python while processing
- **Overview tab** — donut chart, bar chart, stat cards
- **Rule Editor tab** — view and modify classification rules
- **Drawing List tab** — filterable table of all drawings
- **Export CSV** — saves drawing summary, with friendly error if file is open in Excel
- **Open Output Folder** — opens the split PDF output in Windows Explorer
- **Error handling** — friendly messages for: Python not found, missing packages, locked files, encoding issues, bad PDFs

---

## Updating the App

To update the UI or fix bugs:
1. Edit files in `renderer/` or `main.js`
2. Run `RUN_DEV.bat` to test
3. Run `BUILD.bat` to rebuild the installer
4. Re-share the new `.exe`
