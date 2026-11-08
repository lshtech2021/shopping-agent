const DEFAULTS = {
  backendUrl: 'http://127.0.0.1:8100',
}

export async function loadConfig() {
  const stored = await chrome.storage.local.get(DEFAULTS)
  return {
    backendUrl: String(stored.backendUrl || DEFAULTS.backendUrl).replace(/\/+$/, ''),
  }
}

export async function saveConfig(partial) {
  const current = await loadConfig()
  const next = { ...current, ...partial }
  await chrome.storage.local.set(next)
  return next
}
