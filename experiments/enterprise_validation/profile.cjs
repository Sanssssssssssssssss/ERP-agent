// Run with desktop/node_modules/electron/dist/electron.exe. Each role gets its own profile.
const { app, safeStorage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const data = path.join(root, '.runtime/enterprise-validation-20260922');
const role = process.argv[2] || 'manager';
const accounts = JSON.parse(fs.readFileSync(path.join(data, 'accounts.json'), 'utf8'));
if (!/^[a-z_]+$/.test(role) || role === 'admin' || !accounts[role]) throw new Error('Choose a business role');
const profile = path.join(data, 'profiles', role);
fs.mkdirSync(profile, { recursive: true });
app.setPath('userData', profile);
app.whenReady().then(() => {
  if (!safeStorage.isEncryptionAvailable()) throw new Error('OS secure storage unavailable');
  const protect = value => value ? 'safe:' + safeStorage.encryptString(value).toString('base64') : '';
  const target = path.join(profile, 'settings.json');
  if (process.argv.includes('--check')) {
    const stored = JSON.parse(fs.readFileSync(target, 'utf8'));
    const key = safeStorage.decryptString(Buffer.from(stored.odoo_key.slice(5), 'base64'));
    if (key !== accounts[role].api_key || stored.odoo_username !== accounts[role].login || stored.odoo_db !== 'erp_harness_enterprise_v1' || stored.odoo_url !== 'http://127.0.0.1:18079' || stored.long_term_memory !== false) throw new Error('Profile identity mismatch');
    if (!stored.model_key.startsWith('safe:') || !safeStorage.decryptString(Buffer.from(stored.model_key.slice(5), 'base64'))) throw new Error('Model key unavailable');
    console.log('Verified encrypted enterprise profile: ' + role);
    app.quit();
    return;
  }
  if (fs.existsSync(target)) throw new Error('Existing profile is never overwritten');
  const connection = JSON.parse(fs.readFileSync(path.join(data, 'connection.json'), 'utf8'));
  const settings = {
    model: 'deepseek/deepseek-v4-flash', base_url: process.env.LLM_BASE_URL || 'https://api.commandcode.ai/provider/v1',
    odoo_url: connection.ODOO_URL, odoo_db: connection.ODOO_DB, odoo_username: accounts[role].login,
    model_key: protect(process.env.LLM_API_KEY), odoo_key: protect(accounts[role].api_key), long_term_memory: false,
  };
  fs.writeFileSync(target, JSON.stringify(settings, null, 2), { flag: 'wx' });
  console.log('Created encrypted enterprise profile: ' + role);
  app.quit();
}).catch(error => { console.error(error.message); app.exit(1); });
