const { app, BrowserWindow, shell } = require("electron");
app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("ignore-gpu-blocklist");
const path = require("path");
const fs = require("fs");
const axios = require("axios");
const { spawn } = require("child_process");

app.setName("Bloxy");

const BLOXY_URL = process.env.BLOXY_URL || "http://localhost:5000";
const ICON_PATH = path.join(__dirname, "logo.png");

let mainWindow = null;
let pendingProtocolUrl = null;

const gotLock = app.requestSingleInstanceLock();

if (!gotLock) {
  app.quit();
}

function getGodotClientPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "godot-client");
  }

  return path.join(__dirname, "godot-client");
}

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

function sendStatus(message) {
  console.log("[Bloxy]", message);

  if (mainWindow) {
    mainWindow.webContents.send("bloxy-launch-status", message);
  }
}

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
    throw new Error("godot-client not found at: " + godotClientPath);
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
  args.push("--bloxy-port", String(serverPort));
}   
  sendStatus("Launching game...");

  console.log("[Bloxy] Godot path:", godotClientPath);
  console.log("[Bloxy] Godot args:", args.join(" "));

  const child = spawn(godotClientPath, args, {
    cwd: gameDir,
    detached: true,
    stdio: "ignore"
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
  }
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 1200,
    minWidth: 1000,
    minHeight: 650,
    title: "Bloxy",
    backgroundColor: "#050505",
    autoHideMenuBar: true,
    icon: ICON_PATH,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true
    }
  });

  mainWindow.loadURL(BLOXY_URL);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(BLOXY_URL)) {
      return { action: "allow" };
    }

    if (url.startsWith("bloxy://")) {
      handleProtocolUrl(url);
      return { action: "deny" };
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

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  app.setAsDefaultProtocolClient("bloxy");

  createMainWindow();

  if (pendingProtocolUrl) {
    handleProtocolUrl(pendingProtocolUrl);
    pendingProtocolUrl = null;
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("second-instance", (event, commandLine) => {
  const protocolUrl = commandLine.find(arg => arg.startsWith("bloxy://"));

  if (mainWindow) {
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

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
