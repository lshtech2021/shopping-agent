import { loadConfig } from '../shared/config.js'

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
  // Phase 0/1: mock execution in extension; Phase 2 wires real parsers.
  console.log('[shopping-agent] job', tool, args)
  if (tool === 'search') {
    return {
      status: 'ok',
      site: args.site,
      query: args.query,
      url: args.site === 'jd'
        ? `https://search.jd.com/Search?keyword=${encodeURIComponent(args.query || '')}`
        : `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(args.query || '')}`,
      mode: 'extension-mock',
    }
  }
  if (tool === 'scrape_listing' || tool === 'next_page') {
    return {
      status: 'ok',
      items: [],
      mode: 'extension-mock',
      note: 'real scrape lands in phase2; use API mock_browser for closed loop',
    }
  }
  if (tool === 'fetch_detail') {
    return {
      status: 'ok',
      product_id: args.product_id,
      attributes: {},
      mode: 'extension-mock',
    }
  }
  return { status: 'parse_error', error: `unknown tool ${tool}` }
}

connectWs()
