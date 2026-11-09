import { loadConfig } from './config.js'

async function request(path, options = {}) {
  const { backendUrl } = await loadConfig()
  const response = await fetch(`${backendUrl}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
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

export function sendMessage(sessionId, content, answers) {
  return request(`/api/v1/sessions/${sessionId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content, answers }),
  })
}

export function getSession(sessionId) {
  return request(`/api/v1/sessions/${sessionId}`)
}

export function resumeSession(sessionId, note = 'continue') {
  return request(`/api/v1/sessions/${sessionId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ note }),
  })
}
