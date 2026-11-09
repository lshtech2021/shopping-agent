import { loadConfig } from '../shared/config.js'
import { runBrowserJob } from './browser-jobs.js'
import { createJobLogger } from '../shared/logger.js'

let socket = null
let reconnectTimer = null
let keepAliveTimer = null

// MV3 suspends an idle service worker after ~30s, which would silently drop the
// WebSocket. Traffic on an open socket keeps the worker alive, so ping regularly.
const KEEPALIVE_MS = 20000

function startKeepAlive() {
  stopKeepAlive()
  keepAliveTimer = setInterval(() => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
    }
  }, KEEPALIVE_MS)
}

function stopKeepAlive() {
  clearInterval(keepAliveTimer)
  keepAliveTimer = null
}

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
  const log = createJobLogger('ws')
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
    log.info('extension.ws', 'WebSocket 已连接', { wsUrl })
    socket.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
    startKeepAlive()
  })
  socket.addEventListener('message', async (event) => {
    let data
    try {
      data = JSON.parse(event.data)
    } catch (_) {
      log.warn('extension.ws', '收到非 JSON 消息')
      return
    }
    if (data.type === 'pong') {
      log.debug('extension.ws', 'pong')
      return
    }
    if (data.type === 'job') {
      log.info('extension.ws', `收到任务 ${data.tool}`, {
        job_id: data.job_id,
        args: data.args,
      })
      const result = await handleJob(data.tool, data.args || {})
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(
          JSON.stringify({
            type: 'job_result',
            job_id: data.job_id,
            result,
          }),
        )
        log.info('extension.ws', `回传任务结果 ${data.tool}=${result?.status}`, {
          job_id: data.job_id,
          log_count: (result?.extension_logs || []).length,
        })
      }
    }
  })
  socket.addEventListener('close', () => {
    log.warn('extension.ws', 'WebSocket 断开，3s 后重连')
    stopKeepAlive()
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connectWs, 3000)
  })
  socket.addEventListener('error', (err) => {
    log.error('extension.ws', 'WebSocket 错误', { error: String(err) })
  })
}

async function handleJob(tool, args) {
  return runBrowserJob(tool, args)
}

connectWs()
