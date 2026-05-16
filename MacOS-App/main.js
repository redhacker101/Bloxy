const { app, BrowserWindow, shell, ipcMain, Menu } = require("electron");
const path = require("path");
const fs = require("fs");
const axios = require("axios");
const { spawn } = require("child_process");

/* -------------------------------
   Electron flags
-------------------------------- */

app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("ignore-gpu-blocklist");
app.commandLine.appendSwitch("disable-pinch");
app.commandLine.appendSwitch("overscroll-history-navigation", "0");

app.setName("Bloxy");

/* -------------------------------
   Config
-------------------------------- */

const BLOXY_URL = process.env.BLOXY_URL || "https://bloxygame.com/";
const ICON_PATH = path.join(__dirname, "logo.png");

let mainWindow = null;
let pendingProtocolUrl = null;

/* -------------------------------
   Single instance lock
-------------------------------- */

const gotLock = app.requestSingleInstanceLock();

if (!gotLock) {
  app.quit();
}

/* -------------------------------
   Godot runtime path
-------------------------------- */

function getGodotClientPath() {
  const platform = process.platform;

  if (app.isPackaged) {
    if (platform === "darwin") {
      return path.join(
        process.resourcesPath,
        "Godot.app",
        "Contents",
        "MacOS",
        "Godot"
      );
    }

    if (platform === "win32") {
      return path.join(process.resourcesPath, "godot-client.exe");
    }

    return path.join(process.resourcesPath, "godot-client");
  }

  if (platform === "darwin") {
    return path.join(
      __dirname,
      "Godot.app",
      "Contents",
      "MacOS",
      "Godot"
    );
  }

  if (platform === "win32") {
    return path.join(__dirname, "godot-client.exe");
  }

  return path.join(__dirname, "godot-client");
}

/* -------------------------------
   Protocol parser
-------------------------------- */

function parseBloxyProtocolUrl(protocolUrl) {
  const parsed = new URL(protocolUrl);

  if (parsed.protocol !== "bloxy:") {
    throw new Error("Invalid protocol");
  }

  if (parsed.hostname !== "play") {
    throw new Error("Invalid Bloxy action");
  }

  const gameId = decodeURIComponent(parsed.pathname.replace("/", "").trim());

  const ticket = parsed.searchParams.get("ticket");
  const api = parsed.searchParams.get("api") || BLOXY_URL;
  const clientPckUrl = parsed.searchParams.get("client_pck_url");
  const multiplayer = parsed.searchParams.get("multiplayer") === "true";
  const serverIp = parsed.searchParams.get("server_ip") || "";
  const serverPort = parsed.searchParams.get("server_port") || "";

  if (!gameId) {
    throw new Error("Missing game ID");
  }

  if (!ticket) {
    throw new Error("Missing join ticket");
  }

  if (!clientPckUrl) {
    throw new Error("Missing client_pck_url");
  }

  return {
    gameId,
    ticket,
    api,
    clientPckUrl,
    multiplayer,
    serverIp,
    serverPort
  };
}

/* -------------------------------
   Download helper
-------------------------------- */

async function downloadFile(url, destination) {
  const writer = fs.createWriteStream(destination);

  const response = await axios({
    url,
    method: "GET",
    responseType: "stream"
  });

  response.data.pipe(writer);

  return new Promise((resolve, reject) => {
    writer.on("finish", resolve);
    writer.on("error", reject);
  });
}

/* -------------------------------
   Status helper
-------------------------------- */

function sendStatus(message) {
  console.log("[Bloxy]", message);

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("bloxy-launch-status", message);
  }
}

/* -------------------------------
   App-like window hardening
-------------------------------- */

function lockZoomAndBrowserBehavior(win) {
  const wc = win.webContents;

  wc.setZoomFactor(1);

  wc.on("did-finish-load", () => {
    wc.setZoomFactor(1);

    try {
      wc.setVisualZoomLevelLimits(1, 1);
    } catch (err) {
      console.warn("[Bloxy] Could not set visual zoom limits:", err.message);
    }

    wc.insertCSS(`
      html, body {
        overscroll-behavior: none !important;
        -webkit-user-select: none;
        user-select: none;
      }

      input, textarea, [contenteditable="true"] {
        -webkit-user-select: text;
        user-select: text;
      }

      img {
        -webkit-user-drag: none;
      }

      * {
        -webkit-tap-highlight-color: transparent;
      }
    `).catch(err => {
      console.warn("[Bloxy] CSS injection failed:", err.message);
    });
  });

  wc.on("zoom-changed", event => {
    event.preventDefault();
    wc.setZoomFactor(1);
  });

  wc.on("before-input-event", (event, input) => {
    const key = String(input.key || "").toLowerCase();
    const ctrlOrMeta = input.control || input.meta;

    const blockedShortcuts =
      // Zoom
      (ctrlOrMeta && ["+", "-", "=", "0"].includes(key)) ||

      // Reload / hard reload
      (ctrlOrMeta && key === "r") ||
      (ctrlOrMeta && input.shift && key === "r") ||
      key === "f5" ||

      // DevTools
      key === "f12" ||
      (ctrlOrMeta && input.shift && ["i", "j", "c"].includes(key)) ||

      // New tab / new window / open file
      (ctrlOrMeta && ["n", "t", "o"].includes(key)) ||

      // Save page / print page
      (ctrlOrMeta && ["s", "p"].includes(key));

    if (blockedShortcuts) {
      event.preventDefault();
      wc.setZoomFactor(1);
    }
  });

  wc.on("context-menu", event => {
    event.preventDefault();
  });

  wc.on("will-attach-webview", event => {
    event.preventDefault();
  });
}

/* -------------------------------
   Launch game
-------------------------------- */

async function launchGameFromProtocol(protocolUrl) {
  const launchData = parseBloxyProtocolUrl(protocolUrl);

  const {
    gameId,
    ticket,
    api,
    clientPckUrl,
    multiplayer,
    serverIp,
    serverPort
  } = launchData;

  sendStatus("Reading Bloxy launch ticket...");

  console.log("[Bloxy] Game ID:", gameId);
  console.log("[Bloxy] API:", api);
  console.log("[Bloxy] Client PCK URL:", clientPckUrl);
  console.log("[Bloxy] Multiplayer:", multiplayer);
  console.log("[Bloxy] Server:", serverIp, serverPort);

  const gamesDir = path.join(app.getPath("userData"), "games");
  const gameDir = path.join(gamesDir, gameId);

  fs.mkdirSync(gameDir, { recursive: true });

  const clientPckPath = path.join(gameDir, "client.pck");
  const fullClientPckUrl = new URL(clientPckUrl, api).toString();

  sendStatus("Downloading game client...");
  await downloadFile(fullClientPckUrl, clientPckPath);

  const godotClientPath = getGodotClientPath();

  if (!fs.existsSync(godotClientPath)) {
    throw new Error("Godot runtime not found at: " + godotClientPath);
  }

  try {
    if (process.platform !== "win32") {
      fs.chmodSync(godotClientPath, 0o755);
    }
  } catch (err) {
    console.warn("[Bloxy] Could not chmod Godot runtime:", err.message);
  }

  const args = [
    "--main-pack",
    clientPckPath,
    "--",
    "--bloxy-game-id",
    gameId,
    "--bloxy-ticket",
    ticket,
    "--bloxy-api",
    api
  ];

  if (multiplayer) {
    args.push("--bloxy-server", serverIp || "127.0.0.1");
    args.push("--bloxy-port", String(serverPort || ""));
  }

  sendStatus("Launching game...");

  console.log("[Bloxy] Godot path:", godotClientPath);
  console.log("[Bloxy] Godot args:", args.join(" "));

  const child = spawn(godotClientPath, args, {
    cwd: gameDir,
    detached: true,
    stdio: "ignore"
  });

  child.on("error", err => {
    sendStatus("Godot launch error: " + err.message);
    console.error("[Bloxy] Godot spawn error:", err);
  });

  child.unref();

  sendStatus("Game launched.");

  return true;
}

/* -------------------------------
   Protocol handler
-------------------------------- */

async function handleProtocolUrl(url) {
  try {
    await launchGameFromProtocol(url);
  } catch (err) {
    sendStatus("Launch error: " + err.message);
    console.error("[Bloxy] Launch error:", err);
  }
}

/* -------------------------------
   Main window
-------------------------------- */

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 1000,
    minHeight: 650,

    title: "Bloxy",
    backgroundColor: "#050505",
    autoHideMenuBar: true,
    icon: ICON_PATH,

    frame: true,
    show: false,
    center: true,

    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
      devTools: false
    }
  });

  Menu.setApplicationMenu(null);

  lockZoomAndBrowserBehavior(mainWindow);

  mainWindow.once("ready-to-show", () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;

    mainWindow.show();
    mainWindow.focus();
  });

  mainWindow.loadURL(BLOXY_URL);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("bloxy://")) {
      handleProtocolUrl(url);
      return { action: "deny" };
    }

    if (url.startsWith(BLOXY_URL)) {
      return { action: "allow" };
    }

    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith(BLOXY_URL)) {
      return;
    }

    if (url.startsWith("bloxy://")) {
      event.preventDefault();
      handleProtocolUrl(url);
      return;
    }

    event.preventDefault();
    shell.openExternal(url);
  });

  mainWindow.webContents.on("did-navigate", () => {
    mainWindow.webContents.setZoomFactor(1);
  });

  mainWindow.webContents.on("did-navigate-in-page", () => {
    mainWindow.webContents.setZoomFactor(1);
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

/* -------------------------------
   Window controls IPC
-------------------------------- */

ipcMain.on("window-minimize", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.minimize();
  }
});

ipcMain.on("window-maximize", () => {
  if (!mainWindow || mainWindow.isDestroyed()) return;

  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize();
  } else {
    mainWindow.maximize();
  }
});

ipcMain.on("window-close", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.close();
  }
});

/* -------------------------------
   App lifecycle
-------------------------------- */

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);

  if (process.defaultApp) {
    if (process.argv.length >= 2) {
      app.setAsDefaultProtocolClient("bloxy", process.execPath, [
        path.resolve(process.argv[1])
      ]);
    }
  } else {
    app.setAsDefaultProtocolClient("bloxy");
  }

  const startupProtocolUrl = process.argv.find(arg => arg.startsWith("bloxy://"));

  if (startupProtocolUrl) {
    pendingProtocolUrl = startupProtocolUrl;
  }

  createMainWindow();

  if (pendingProtocolUrl) {
    handleProtocolUrl(pendingProtocolUrl);
    pendingProtocolUrl = null;
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    } else if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
});

app.on("second-instance", (event, commandLine) => {
  const protocolUrl = commandLine.find(arg => arg.startsWith("bloxy://"));

  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) {
      mainWindow.restore();
    }

    mainWindow.show();
    mainWindow.focus();
  }

  if (protocolUrl) {
    handleProtocolUrl(protocolUrl);
  }
});

app.on("open-url", (event, url) => {
  event.preventDefault();

  if (!mainWindow) {
    pendingProtocolUrl = url;
    return;
  }

  handleProtocolUrl(url);
});

app.on("browser-window-created", (event, window) => {
  window.webContents.setZoomFactor(1);
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

/* -------------------------------
   Crash logging
-------------------------------- */

process.on("uncaughtException", err => {
  console.error("[Bloxy] Uncaught exception:", err);
});

process.on("unhandledRejection", err => {
  console.error("[Bloxy] Unhandled rejection:", err);
});
