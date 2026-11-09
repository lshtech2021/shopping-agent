import { loadConfig } from '../shared/config.js'
import { runBrowserJob } from './browser-jobs.js'
import { createJobLogger } from '../shared/logger.js'
import { getCachedResult, setCachedResult } from '../shared/action-cache.js'
import { buildWebSocketUrl } from '../shared/ws-url.js'

let socket = null
let socketGeneration = 0
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
  clearTimeout(reconnectTimer)
  reconnectTimer = null
  const generation = ++socketGeneration
  const log = createJobLogger('ws')
  const { backendUrl, extensionToken } = await loadConfig()
  if (generation !== socketGeneration) return
  const wsUrl = buildWebSocketUrl(backendUrl, extensionToken)
  if (socket) {
    try {
      socket.close()
    } catch (_) {
      /* ignore */
    }
  }
  const ws = new WebSocket(wsUrl)
  socket = ws
  ws.addEventListener('open', () => {
    if (generation !== socketGeneration) return
    log.info('extension.ws', 'WebSocket 已连接', {
      endpoint: new URL(wsUrl).host,
    })
    ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
    startKeepAlive()
  })
  ws.addEventListener('message', async (event) => {
    if (generation !== socketGeneration) return
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
        action_id: data.action_id,
        args: data.args,
      })
      const actionId = data.action_id || data.job_id
      let result = actionId ? await getCachedResult(actionId) : null
      if (result) {
        log.info('extension.ws', '复用已缓存的任务结果', {
          job_id: data.job_id,
          action_id: actionId,
        })
      } else {
        try {
          result = await handleJob(data.tool, {
            ...(data.args || {}),
            job_id: data.job_id,
            action_id: actionId,
          })
        } catch (error) {
          result = {
            status: 'blocked',
            error: String(error?.message || error),
            job_id: data.job_id ?? null,
            extension_logs: [],
          }
        }
        if (actionId) await setCachedResult(actionId, result)
      }
      const envelope = {
        type: 'job_result',
        job_id: data.job_id,
        action_id: actionId,
        result,
      }
      if (
        generation === socketGeneration &&
        ws.readyState === WebSocket.OPEN
      ) {
        ws.send(JSON.stringify(envelope))
        log.info(
          'extension.ws',
          `回传任务结果 ${data.tool}=${result?.status}`,
          {
            job_id: data.job_id,
            log_count: (result?.extension_logs || []).length,
          },
        )
      } else {
        log.warn('extension.ws', 'WebSocket 已断开，任务结果无法回传', {
          job_id: data.job_id,
        })
      }
    }
  })
  ws.addEventListener('close', () => {
    if (generation !== socketGeneration) return
    log.warn('extension.ws', 'WebSocket 断开，3s 后重连')
    stopKeepAlive()
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connectWs, 3000)
  })
  ws.addEventListener('error', (err) => {
    log.error('extension.ws', 'WebSocket 错误', { error: String(err) })
  })
}

async function handleJob(tool, args) {
  return runBrowserJob(tool, args)
}

connectWs()
