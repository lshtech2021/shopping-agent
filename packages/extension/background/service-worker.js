import { loadConfig } from '../shared/config.js'
import { runBrowserJob } from './browser-jobs.js'
import { createJobLogger } from '../shared/logger.js'
import { buildWebSocketUrl } from '../shared/ws-url.js'

let socket = null
let reconnectTimer = null
let keepAliveTimer = null

const PENDING_RESULTS_KEY = 'pendingJobResults'
const MAX_PENDING_RESULTS = 50

async function queueJobResult(envelope) {
  try {
    const stored = await chrome.storage.local.get({
      [PENDING_RESULTS_KEY]: [],
    })
    const queue = Array.isArray(stored?.[PENDING_RESULTS_KEY])
      ? stored[PENDING_RESULTS_KEY]
      : []
    queue.push(envelope)
    await chrome.storage.local.set({
      [PENDING_RESULTS_KEY]: queue.slice(-MAX_PENDING_RESULTS),
    })
  } catch (_) {
    /* best effort: the backend will time out and retry the run */
  }
}

async function flushPendingResults() {
  if (socket?.readyState !== WebSocket.OPEN) return
  try {
    const stored = await chrome.storage.local.get({
      [PENDING_RESULTS_KEY]: [],
    })
    const queue = Array.isArray(stored?.[PENDING_RESULTS_KEY])
      ? stored[PENDING_RESULTS_KEY]
      : []
    if (!queue.length) return
    let sent = 0
    for (const envelope of queue) {
      if (socket?.readyState !== WebSocket.OPEN) break
      socket.send(JSON.stringify(envelope))
      sent += 1
    }
    await chrome.storage.local.set({
      [PENDING_RESULTS_KEY]: queue.slice(sent),
    })
  } catch (_) {
    /* retry on the next reconnect */
  }
}

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
  const { backendUrl, extensionToken } = await loadConfig()
  const wsUrl = buildWebSocketUrl(backendUrl, extensionToken)
  if (socket) {
    try {
      socket.close()
    } catch (_) {
      /* ignore */
    }
  }
  socket = new WebSocket(wsUrl)
  socket.addEventListener('open', async () => {
    log.info('extension.ws', 'WebSocket 已连接', { wsUrl })
    socket.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
    startKeepAlive()
    await flushPendingResults()
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
      let result
      try {
        result = await handleJob(data.tool, {
          ...(data.args || {}),
          job_id: data.job_id,
        })
      } catch (error) {
        result = {
          status: 'blocked',
          error: String(error?.message || error),
          job_id: data.job_id ?? null,
          extension_logs: [],
        }
      }
      const envelope = {
        type: 'job_result',
        job_id: data.job_id,
        result,
      }
      try {
        if (socket?.readyState !== WebSocket.OPEN) {
          throw new Error('socket_not_open')
        }
        socket.send(JSON.stringify(envelope))
      } catch (_) {
        await queueJobResult(envelope)
        log.warn('extension.ws', 'WebSocket 未连接，任务结果已排队', {
          job_id: data.job_id,
        })
      }
      log.info('extension.ws', `回传任务结果 ${data.tool}=${result?.status}`, {
        job_id: data.job_id,
        log_count: (result?.extension_logs || []).length,
      })
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
