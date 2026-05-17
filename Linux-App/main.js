const { app, BrowserWindow, shell, ipcMain, Menu } = require("electron");
const path = require("path");
const fs = require("fs");
const axios = require("axios");
const { spawn } = require("child_process");

app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("ignore-gpu-blocklist");
app.commandLine.appendSwitch("disable-pinch");
app.commandLine.appendSwitch("overscroll-history-navigation", "0");

app.setName("bloxy");

function normalizeApiUrl(value) {
  let api = String(value || "https://bloxygame.com/").trim();

  if (!api) {
    api = "https://bloxygame.com/";
  }

  if (!api.startsWith("http://") && !api.startsWith("https://")) {
    api = "https://" + api;
  }

  if (!api.endsWith("/")) {
    api += "/";
  }

  return api;
}

const BLOXY_URL = normalizeApiUrl(process.env.BLOXY_URL || "https://bloxygame.com/");
const BLOXY_ORIGIN = new URL(BLOXY_URL).origin;
const ICON_PATH = path.join(__dirname, "logo.png");

let mainWindow = null;
let pendingProtocolUrl = null;

const gotLock = app.requestSingleInstanceLock();

if (!gotLock) {
  app.quit();
}

function isBloxyWebsiteUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.origin === BLOXY_ORIGIN;
  } catch (err) {
    return false;
  }
}

function isBloxyProtocolUrl(url) {
  return typeof url === "string" && url.startsWith("bloxy://");
}

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

function parseBloxyProtocolUrl(protocolUrl) {
  let parsed;

  try {
    parsed = new URL(protocolUrl);
  } catch (err) {
    throw new Error("Invalid bloxy protocol URL");
  }

  if (parsed.protocol !== "bloxy:") {
    throw new Error("Invalid protocol");
  }

  if (parsed.hostname !== "play") {
    throw new Error("Invalid bloxy action");
  }

  const gameId = decodeURIComponent(parsed.pathname.replace("/", "").trim());

  const ticket = parsed.searchParams.get("ticket") || "";
  const api = normalizeApiUrl(parsed.searchParams.get("api") || BLOXY_URL);
  const clientPckUrl = parsed.searchParams.get("client_pck_url") || "";
  const multiplayer = parsed.searchParams.get("multiplayer") === "true";
  const serverIp = parsed.searchParams.get("server_ip") || "";
  const serverPort = parsed.searchParams.get("server_port") || "";
  const username = parsed.searchParams.get("username") || "";
  const userId = parsed.searchParams.get("user_id") || "";

  if (!gameId) {
    throw new Error("Missing game ID");
  }

  if (!ticket) {
    throw new Error("Missing join ticket");
  }

  if (!clientPckUrl) {
    throw new Error("Missing client_pck_url");
  }

  if (multiplayer) {
    if (!serverIp) {
      throw new Error("Missing multiplayer server_ip");
    }

    if (!serverPort || Number.isNaN(Number(serverPort)) || Number(serverPort) <= 0) {
      throw new Error("Invalid multiplayer server_port");
    }
  }

  return {
    gameId,
    ticket,
    api,
    clientPckUrl,
    multiplayer,
    serverIp,
    serverPort: String(serverPort),
    username,
    userId
  };
}

async function getCookieHeader(url) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return "";
  }

  const cookieUrl = normalizeApiUrl(url);

  const cookies = await mainWindow.webContents.session.cookies.get({
    url: cookieUrl
  });

  return cookies
    .map(cookie => `${cookie.name}=${cookie.value}`)
    .join("; ");
}

async function refreshLaunchDataFromBackend(launchData) {
  const apiBase = normalizeApiUrl(launchData.api || BLOXY_URL);

  const playUrl = new URL(
    `/api/games/${encodeURIComponent(launchData.gameId)}/play`,
    apiBase
  ).toString();

  const cookieHeader = await getCookieHeader(apiBase);

  console.log("[bloxy] Refreshing from backend:", playUrl);
  console.log("[bloxy] Cookie header exists:", cookieHeader.length > 0);

  const response = await axios({
    url: playUrl,
    method: "GET",
    headers: cookieHeader ? { Cookie: cookieHeader } : {},
    validateStatus: () => true
  });

  console.log("[bloxy] Backend refresh status:", response.status);
  console.log("[bloxy] Backend refresh data:", response.data);

  if (response.status < 200 || response.status >= 300) {
    throw new Error(
      `Backend launch refresh failed: HTTP ${response.status} - ${JSON.stringify(response.data)}`
    );
  }

  const data = response.data || {};

  if (!data.success) {
    throw new Error(data.error || "Backend launch refresh failed");
  }

  const refreshed = {
    gameId: data.game_id || launchData.gameId,
    ticket: data.join_ticket || launchData.ticket,
    api: apiBase,
    clientPckUrl: data.client_pck_url || launchData.clientPckUrl,
    multiplayer: data.multiplayer === true,
    serverIp: data.server_ip || launchData.serverIp || "",
    serverPort: data.server_port
      ? String(data.server_port)
      : String(launchData.serverPort || ""),
    username: data.username || launchData.username || "",
    userId: data.user_id || launchData.userId || ""
  };

  if (!refreshed.gameId) {
    throw new Error("Backend did not return game_id");
  }

  if (!refreshed.ticket) {
    throw new Error("Backend did not return join_ticket");
  }

  if (!refreshed.clientPckUrl) {
    throw new Error("Backend did not return client_pck_url");
  }

  if (refreshed.multiplayer) {
    if (!refreshed.serverIp) {
      throw new Error("Backend did not return server_ip for multiplayer game");
    }

    if (
      !refreshed.serverPort ||
      Number.isNaN(Number(refreshed.serverPort)) ||
      Number(refreshed.serverPort) <= 0
    ) {
      throw new Error("Backend did not return valid server_port for multiplayer game");
    }
  }

  return refreshed;
}

async function downloadFile(url, destination) {
  const writer = fs.createWriteStream(destination);

  const response = await axios({
    url,
    method: "GET",
    responseType: "stream",
    validateStatus: status => status >= 200 && status < 300
  });

  response.data.pipe(writer);

  return new Promise((resolve, reject) => {
    writer.on("finish", resolve);

    writer.on("error", err => {
      try {
        writer.close();
      } catch (_) {}

      reject(err);
    });
  });
}

function sendStatus(message) {
  console.log("[bloxy]", message);

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("bloxy-launch-status", message);
  }
}

function lockZoomAndBrowserBehavior(win) {
  const wc = win.webContents;

  wc.setZoomFactor(1);

  wc.on("did-finish-load", () => {
    wc.setZoomFactor(1);

    try {
      wc.setVisualZoomLevelLimits(1, 1);
    } catch (err) {
      console.warn("[bloxy] Could not set visual zoom limits:", err.message);
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
      console.warn("[bloxy] CSS injection failed:", err.message);
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
      (ctrlOrMeta && ["+", "-", "=", "0"].includes(key)) ||
      (ctrlOrMeta && key === "r") ||
      (ctrlOrMeta && input.shift && key === "r") ||
      key === "f5" ||
      key === "f12" ||
      (ctrlOrMeta && input.shift && ["i", "j", "c"].includes(key)) ||
      (ctrlOrMeta && ["n", "t", "o"].includes(key)) ||
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

async function launchGameFromProtocol(protocolUrl) {
  let launchData = parseBloxyProtocolUrl(protocolUrl);

  sendStatus("Refreshing bloxy launch data...");
  launchData = await refreshLaunchDataFromBackend(launchData);

  console.log("[bloxy] FINAL LAUNCH DATA:", launchData);

  const {
    gameId,
    ticket,
    api,
    clientPckUrl,
    multiplayer,
    serverIp,
    serverPort,
    username,
    userId
  } = launchData;

  sendStatus("Reading bloxy launch ticket...");

  console.log("[bloxy] Game ID:", gameId);
  console.log("[bloxy] API:", api);
  console.log("[bloxy] Client PCK URL:", clientPckUrl);
  console.log("[bloxy] Multiplayer:", multiplayer);
  console.log("[bloxy] Server:", serverIp, serverPort);
  console.log("[bloxy] Username:", username);
  console.log("[bloxy] User ID:", userId);

  const gamesDir = path.join(app.getPath("userData"), "games");
  const safeGameId = String(gameId).replace(/[^a-zA-Z0-9_-]/g, "_");
  const gameDir = path.join(gamesDir, safeGameId);

  fs.mkdirSync(gameDir, { recursive: true });

  const clientPckPath = path.join(gameDir, "client.pck");
  const fullClientPckUrl = new URL(clientPckUrl, normalizeApiUrl(api)).toString();

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
    console.warn("[bloxy] Could not chmod Godot runtime:", err.message);
  }

  const args = [
    "--main-pack",
    clientPckPath,
    "--",
    "--bloxy-game-id",
    String(gameId),
    "--bloxy-ticket",
    String(ticket),
    "--bloxy-api",
    normalizeApiUrl(api),
    "--bloxy-username",
    String(username || ""),
    "--bloxy-user-id",
    String(userId || "")
  ];

  if (multiplayer) {
    args.push("--bloxy-server");
    args.push(String(serverIp));

    args.push("--bloxy-port");
    args.push(String(serverPort));
  }

  sendStatus("Launching game...");

  console.log("[bloxy] Godot path:", godotClientPath);
  console.log("[bloxy] Godot args:", args.join(" "));

  const child = spawn(godotClientPath, args, {
    cwd: gameDir,
    detached: true,
    stdio: "ignore"
  });

  child.on("error", err => {
    sendStatus("Godot launch error: " + err.message);
    console.error("[bloxy] Godot spawn error:", err);
  });

  child.unref();

  sendStatus("Game launched.");

  return true;
}

async function handleProtocolUrl(url) {
  try {
    await launchGameFromProtocol(url);
  } catch (err) {
    sendStatus("Launch error: " + err.message);
    console.error("[bloxy] Launch error:", err);
  }
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 1000,
    minHeight: 650,
    title: "bloxy",
    backgroundColor: "#050505",
    autoHideMenuBar: true,
    icon: ICON_PATH,
    frame: true,
    show: true,
    center: true,

    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
      devTools: true
    }
  });

  Menu.setApplicationMenu(null);

  lockZoomAndBrowserBehavior(mainWindow);

  mainWindow.once("ready-to-show", () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.focus();
  });

  mainWindow.webContents.on("did-fail-load", (event, errorCode, errorDescription, validatedURL) => {
    console.error("[bloxy] Failed to load:", validatedURL);
    console.error("[bloxy] Error:", errorCode, errorDescription);
  });

  mainWindow.webContents.on("did-finish-load", () => {
    console.log("[bloxy] Website loaded");
  });

  mainWindow.loadURL(BLOXY_URL);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isBloxyProtocolUrl(url)) {
      handleProtocolUrl(url);
      return { action: "deny" };
    }

    if (isBloxyWebsiteUrl(url)) {
      return { action: "allow" };
    }

    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (isBloxyWebsiteUrl(url)) {
      return;
    }

    if (isBloxyProtocolUrl(url)) {
      event.preventDefault();
      handleProtocolUrl(url);
      return;
    }

    event.preventDefault();
    shell.openExternal(url);
  });

  mainWindow.webContents.on("did-navigate", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.setZoomFactor(1);
    }
  });

  mainWindow.webContents.on("did-navigate-in-page", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.setZoomFactor(1);
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

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

  const startupProtocolUrl = process.argv.find(arg => isBloxyProtocolUrl(arg));

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
  const protocolUrl = commandLine.find(arg => isBloxyProtocolUrl(arg));

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

process.on("uncaughtException", err => {
  console.error("[bloxy] Uncaught exception:", err);
});

process.on("unhandledRejection", err => {
  console.error("[bloxy] Unhandled rejection:", err);
});
