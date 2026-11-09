import { loadConfig, saveConfig } from '../shared/config.js'

const input = document.getElementById('backendUrl')
const tokenInput = document.getElementById('extensionToken')
const status = document.getElementById('status')

loadConfig().then((cfg) => {
  input.value = cfg.backendUrl
  tokenInput.value = cfg.extensionToken || ''
})

document.getElementById('save').addEventListener('click', async () => {
  const backendUrl = input.value.trim().replace(/\/+$/, '')
  const extensionToken = tokenInput.value.trim()
  await saveConfig({ backendUrl, extensionToken })
  try {
    await chrome.runtime.sendMessage({ type: 'reconnect_ws' })
  } catch (_) {
    /* service worker may be idle */
  }
  status.textContent = '已保存'
})
