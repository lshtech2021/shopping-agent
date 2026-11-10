import { loadConfig } from './config.js'

async function request(path, options = {}) {
  const { backendUrl, extensionToken } = await loadConfig()
  const response = await fetch(`${backendUrl}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(extensionToken ? { 'X-Extension-Token': extensionToken } : {}),
      ...(options.headers || {}),
    },
  })
  const text = await response.text()
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${text.slice(0, 300)}`)
  }
  return text ? JSON.parse(text) : null
}

export function createSession(requirement, sites) {
  return request('/api/v1/sessions', {
    method: 'POST',
    body: JSON.stringify({ requirement, sites }),
  })
}

export function sendMessage(
  sessionId,
  content,
  clientMessageId,
) {
  return request(`/api/v1/sessions/${sessionId}/messages`, {
    method: 'POST',
    body: JSON.stringify({
      content,
      client_message_id: clientMessageId,
    }),
  })
}

export function getSession(sessionId) {
  return request(`/api/v1/sessions/${sessionId}`)
}

export function getSessionEvents(sessionId, after = 0, limit = 100) {
  const params = new URLSearchParams({
    after: String(after || 0),
    limit: String(limit || 100),
  })
  return request(`/api/v1/sessions/${sessionId}/events?${params}`)
}

export function resumeSession(
  sessionId,
  note = 'continue',
  clientMessageId,
) {
  return request(`/api/v1/sessions/${sessionId}/resume`, {
    method: 'POST',
    body: JSON.stringify({
      note,
      client_message_id: clientMessageId,
    }),
  })
}
