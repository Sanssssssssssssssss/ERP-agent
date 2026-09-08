// Local design preview only: no preload, settings, Python host or ERP connection.
const { app, BrowserWindow, session } = require('electron');
const path = require('node:path');
const fs = require('node:fs');

app.setName('Odoo Workbench Prototype');
const profilePath = path.resolve(__dirname, '../../../.runtime/workbench-prototype/profile');
fs.mkdirSync(profilePath, { recursive: true });
app.setPath('userData', profilePath);
app.setPath('sessionData', profilePath);
let prototypeWindow;

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (_details, callback) => callback({ cancel: true }));
  const window = new BrowserWindow({
    title: 'Odoo Workbench · 交互原型',
    width: 1600,
    height: 1000,
    minWidth: 1000,
    minHeight: 700,
    backgroundColor: '#f7f9fb',
    show: false,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  });
  prototypeWindow = window;
  window.on('closed', () => { prototypeWindow = null; });
  window.removeMenu();
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.once('ready-to-show', () => window.show());
  await window.loadFile(path.join(__dirname, 'index.html'));
  window.show();
}).catch(error => {
  console.error(error.message);
  app.exit(1);
});

app.on('window-all-closed', () => app.quit());
