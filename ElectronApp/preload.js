const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("bloxy", {
  launchGame: (gameId) => ipcRenderer.invoke("launch-game", gameId),

  onStatus: (callback) => {
    ipcRenderer.on("launcher-status", (event, message) => callback(message));
  }
});
