const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const tmpdir = os.tmpdir;

// ── Find the bundled sorter executable ───────────────────────────────────────
function getScriptPath() {
  // In production (packaged app), resources are in process.resourcesPath
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "sorter.exe");
  }
  // In dev mode, look for sorter.exe next to main.js
  return path.join(__dirname, "sorter.exe");
}

// ── Create the main window ───────────────────────────────────────────────────
function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    titleBarStyle: "default",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    icon: path.join(__dirname, "assets", "icon.ico"),
    title: "Work Package Sorter",
    backgroundColor: "#0f1117",
  });

  win.loadFile(path.join(__dirname, "renderer", "index.html"));

  // Open devtools in dev mode
  if (process.argv.includes("--dev")) {
    win.webContents.openDevTools();
  }
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// ── IPC: Open file picker for PDF or PFXT ────────────────────────────────────
ipcMain.handle("pick-pdf", async () => {
  const result = await dialog.showOpenDialog({
    title: "Select Drawing Package (PDF or PFXT)",
    filters: [
      { name: "Drawing Files", extensions: ["pdf", "pfxt", "pfxs", "pfxa"] },
      { name: "PDF Files", extensions: ["pdf"] },
      { name: "PowerFab Exchange Files", extensions: ["pfxt", "pfxs", "pfxa"] },
    ],
    properties: ["openFile"],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

// ── IPC: Pick output directory ───────────────────────────────────────────────
ipcMain.handle("pick-output-dir", async () => {
  const result = await dialog.showOpenDialog({
    title: "Select Output Folder",
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

// ── IPC: Save CSV export ─────────────────────────────────────────────────────
ipcMain.handle("save-csv", async (event, csvContent) => {
  const result = await dialog.showSaveDialog({
    title: "Export Drawing Summary",
    defaultPath: "drawing_summary.csv",
    filters: [{ name: "CSV Files", extensions: ["csv"] }],
  });
  if (result.canceled || !result.filePath) return { success: false, reason: "cancelled" };

  try {
    fs.writeFileSync(result.filePath, csvContent, "utf8");
    return { success: true, filePath: result.filePath };
  } catch (err) {
    // Common cause: file is open in Excel
    if (err.code === "EBUSY" || err.code === "EPERM" || err.code === "EACCES") {
      return { success: false, reason: "locked", message: "That file is open in another program (e.g. Excel). Close it and try again." };
    }
    return { success: false, reason: "error", message: err.message };
  }
});

// ── IPC: Open folder in Explorer ─────────────────────────────────────────────
ipcMain.handle("open-folder", async (event, folderPath) => {
  shell.openPath(folderPath);
});

// ── IPC: Cancel the running sorter process ───────────────────────────────
ipcMain.handle("cancel-sorter", () => {
  if (global._sorterProc) {
    try {
      process.platform === "win32"
        ? require("child_process").execSync(`taskkill /PID ${global._sorterProc.pid} /T /F`)
        : global._sorterProc.kill("SIGTERM");
    } catch(_) {}
    global._sorterProc = null;
  }
});

// ── IPC: Save/load rules to user data folder ─────────────────────────────
const rulesPath = path.join(app.getPath("userData"), "custom_rules.json");

ipcMain.handle("save-rules", async (event, rules) => {
  try {
    fs.writeFileSync(rulesPath, JSON.stringify(rules, null, 2), "utf8");
    return true;
  } catch (err) {
    console.error("Failed to save rules:", err);
    return false;
  }
});

ipcMain.handle("load-rules", async () => {
  try {
    if (!fs.existsSync(rulesPath)) return null;
    const data = fs.readFileSync(rulesPath, "utf8");
    return JSON.parse(data);
  } catch (err) {
    console.error("Failed to load rules:", err);
    return null;
  }
});

// ── IPC: Run the Python sorter ───────────────────────────────────────────────
ipcMain.handle("run-sorter", async (event, { pdfPath, configPath, outputDir }) => {
  // pdfPath may now be a .pdf OR .pfxt file — name kept for backwards compat
  const inputPath = pdfPath;
  return new Promise((resolve) => {
    // ── Pre-flight checks ──────────────────────────────────────────────────
    const scriptPath = getScriptPath();
    if (!fs.existsSync(scriptPath)) {
      return resolve({
        success: false,
        error: `Sorter engine not found at: ${scriptPath}\n\nTry reinstalling the app.`,
      });
    }

    if (!fs.existsSync(inputPath)) {
      return resolve({ success: false, error: "The selected file no longer exists." });
    }

    // Ensure output dir exists
    try {
      fs.mkdirSync(outputDir, { recursive: true });
    } catch (err) {
      return resolve({ success: false, error: `Cannot create output folder: ${err.message}` });
    }

    // Check output dir is writable
    try {
      const testFile = path.join(outputDir, ".write_test");
      fs.writeFileSync(testFile, "");
      fs.unlinkSync(testFile);
    } catch (_) {
      return resolve({ success: false, error: `Output folder is not writable: ${outputDir}` });
    }

    // ── Detect file type and build args ───────────────────────────────────
    const ext = path.extname(inputPath).toLowerCase();
    const isPfxt = [".pfxt", ".pfxs", ".pfxa"].includes(ext);
    const tmpJson = path.join(os.tmpdir(), `wps_${Date.now()}.json`);

    const args = [inputPath, "--output-dir", outputDir, "--json-file", tmpJson];
    if (isPfxt) {
      args.push("--source", "pfxt");
    }
    if (configPath && fs.existsSync(configPath)) {
      args.push("--config", configPath);
    }

    // ── Spawn sorter.exe directly — no Python runtime needed ──────────────
    let stdout = "";
    let stderr = "";
    let cancelled = false;

    global._sorterProc = null;

    const proc = spawn(scriptPath, args, { cwd: path.dirname(scriptPath) });
    global._sorterProc = proc;

    proc.stdout.on("data", (chunk) => {
      // stdout not used in json-file mode — ignore
      stdout += chunk.toString();
    });

    proc.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderr += text;
      // Progress lines come from stderr — forward to renderer
      text.split("\n").forEach((line) => {
        if (line.trim()) {
          event.sender.send("sorter-progress", line);
        }
      });
    });

    proc.on("error", (err) => {
      resolve({ success: false, error: `Failed to start Python: ${err.message}` });
    });

    proc.on("close", (code) => {
      global._sorterProc = null;
      if (cancelled) {
        try { fs.unlinkSync(jsonTmpFile); } catch(_) {}
        return resolve({ success: false, error: "cancelled" });
      }
      // Filter out progress lines from stderr — only real errors start with "Traceback" or "Error"
      const errorLines = stderr.split("\n").filter(l =>
        l.includes("Traceback") || l.includes("Error:") || l.includes("Exception:")
      ).join("\n");

      if (code !== 0) {
        // Parse common Python errors into friendly messages
        let friendlyError = `Python exited with code ${code}.`;

        if (stderr.includes("UnicodeDecodeError")) {
          friendlyError = "Could not read a file — encoding issue. Make sure your rules.yaml is saved as UTF-8.";
        } else if (stderr.includes("PermissionError") || stderr.includes("EACCES")) {
          friendlyError = "Permission denied writing output files. Close any open Excel or PDF files in the output folder and try again.";
        } else if (stderr.includes("FileNotFoundError")) {
          friendlyError = "A required file was not found. Check that your PDF and config paths are correct.";
        } else if (errorLines.trim()) {
          friendlyError = errorLines.trim().split("\n").slice(-3).join("\n");
        }

        return resolve({ success: false, error: friendlyError });
      }

      // Read JSON from temp file — much more reliable than parsing stdout
      try {
        if (!fs.existsSync(tmpJson)) {
          return resolve({
            success: false,
            error: `Python finished but did not write results file.\nLooked for: ${tmpJson}\n\nStdout: ${stdout.slice(0, 200)}`,
          });
        }
        const jsonStr = fs.readFileSync(tmpJson, "utf8");
        const data = JSON.parse(jsonStr);
        // Clean up temp file
        try { fs.unlinkSync(tmpJson); } catch(_) {}
        resolve({ success: true, data, outputDir });
      } catch (parseErr) {
        resolve({
          success: false,
          error: `Could not read results file.\nError: ${parseErr.message}`,
        });
      }
    });
  });
});
