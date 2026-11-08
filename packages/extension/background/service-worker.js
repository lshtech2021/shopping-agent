import { loadConfig } from '../shared/config.js'
import { runBrowserJob } from './browser-jobs.js'

let socket = null
let reconnectTimer = null

chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel?.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
  }
})

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'ws_status') {
    sendResponse({
      connected: Boolean(socket && socket.readyState === WebSocket.OPEN),
    })
    return true
  }
  if (message?.type === 'reconnect_ws') {
    connectWs()
    sendResponse({ ok: true })
    return true
  }
  return false
})

async function connectWs() {
  const { backendUrl } = await loadConfig()
  const wsUrl = backendUrl.replace(/^http/, 'ws') + '/ws/extension'
  if (socket) {
    try {
      socket.close()
    } catch (_) {
      /* ignore */
    }
  }
  socket = new WebSocket(wsUrl)
  socket.addEventListener('open', () => {
    console.log('[shopping-agent] ws open', wsUrl)
    socket.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
  })
  socket.addEventListener('message', async (event) => {
    let data
    try {
      data = JSON.parse(event.data)
    } catch (_) {
      return
    }
    if (data.type === 'pong') {
      console.log('[shopping-agent] ws pong')
      return
    }
    if (data.type === 'job') {
      const result = await handleJob(data.tool, data.args || {})
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(
          JSON.stringify({
            type: 'job_result',
            job_id: data.job_id,
            result,
          }),
        )
      }
    }
  })
  socket.addEventListener('close', () => {
    console.log('[shopping-agent] ws closed; retry in 3s')
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connectWs, 3000)
  })
  socket.addEventListener('error', (err) => {
    console.warn('[shopping-agent] ws error', err)
  })
}

async function handleJob(tool, args) {
  console.log('[shopping-agent] job', tool, args)
  return runBrowserJob(tool, args)
}

connectWs()
