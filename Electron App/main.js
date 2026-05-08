const { app, BrowserWindow, shell } = require("electron");
const path = require("path");

app.setName("Bloxy");

const BLOXY_URL = process.env.BLOXY_URL || "http://localhost:5000";
const ICON_PATH = path.join(__dirname, "logo.png");

let mainWindow = null;

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

    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(BLOXY_URL)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
