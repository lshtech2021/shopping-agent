import { loadConfig, saveConfig } from '../shared/config.js'

const input = document.getElementById('backendUrl')
const status = document.getElementById('status')

loadConfig().then((cfg) => {
  input.value = cfg.backendUrl
})

document.getElementById('save').addEventListener('click', async () => {
  const backendUrl = input.value.trim().replace(/\/+$/, '')
  await saveConfig({ backendUrl })
  try {
    await chrome.runtime.sendMessage({ type: 'reconnect_ws' })
  } catch (_) {
    /* service worker may be idle */
  }
  status.textContent = '已保存'
})
