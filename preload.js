const { contextBridge, ipcRenderer } = require("electron");

// Expose a safe, limited API to the renderer (React UI)
contextBridge.exposeInMainWorld("electronAPI", {
  // File picking
  pickPdf: () => ipcRenderer.invoke("pick-pdf"),
  pickOutputDir: () => ipcRenderer.invoke("pick-output-dir"),

  // Run the Python backend
  runSorter: (args) => ipcRenderer.invoke("run-sorter", args),

  // Listen for progress lines streamed from Python
  onProgress: (callback) => {
    ipcRenderer.on("sorter-progress", (_event, line) => callback(line));
  },
  removeProgressListeners: () => {
    ipcRenderer.removeAllListeners("sorter-progress");
  },

  // Export
  saveCsv: (csvContent) => ipcRenderer.invoke("save-csv", csvContent),

  // Open output folder in Windows Explorer
  openFolder: (folderPath) => ipcRenderer.invoke("open-folder", folderPath),

  // Persist rules across sessions
  saveRules: (rules) => ipcRenderer.invoke("save-rules", rules),
  loadRules: () => ipcRenderer.invoke("load-rules"),

  // Cancel a running sort
  cancelSorter: () => ipcRenderer.invoke("cancel-sorter"),
});
