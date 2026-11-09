export function buildWebSocketUrl(backendUrl, token = '') {
  const base = String(backendUrl || '').replace(/\/+$/, '')
  if (!base) throw new Error('backendUrl is required')
  const url = new URL(`${base.replace(/^http/, 'ws')}/ws/extension`)
  if (token) url.searchParams.set('token', token)
  return url.toString()
}
