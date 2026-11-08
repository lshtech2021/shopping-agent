import { createSession, sendMessage, resumeSession } from '../shared/api.js'
import { logUi } from '../shared/logger.js'

const state = {
  sessionId: null,
}

const els = {
  newSession: document.getElementById('newSession'),
  chat: document.getElementById('chat'),
  requirement: document.getElementById('requirement'),
  startBtn: document.getElementById('startBtn'),
  messages: document.getElementById('messages'),
  followup: document.getElementById('followup'),
  sendBtn: document.getElementById('sendBtn'),
  resumeBtn: document.getElementById('resumeBtn'),
  resetBtn: document.getElementById('resetBtn'),
  wsStatus: document.getElementById('wsStatus'),
  execLogs: document.getElementById('execLogs'),
  clearLogsBtn: document.getElementById('clearLogsBtn'),
}

function selectedSites() {
  return [...document.querySelectorAll('input[name="site"]:checked')].map(
    (el) => el.value,
  )
}

function appendBubble(role, text, recommendations = []) {
  const div = document.createElement('div')
  div.className = `bubble ${role}`
  div.textContent = text
  if (recommendations?.length) {
    const list = document.createElement('ul')
    list.className = 'recs'
    for (const item of recommendations) {
      const li = document.createElement('li')
      li.textContent = `${item.title || item.product_id}: ${item.reason || ''}`
      list.appendChild(li)
    }
    div.appendChild(list)
  }
  els.messages.appendChild(div)
  els.messages.scrollTop = els.messages.scrollHeight
}

function formatLogLine(entry) {
  const time = (entry.ts || '').slice(11, 19)
  const dur = entry.duration_ms != null ? ` ${entry.duration_ms}ms` : ''
  const data = entry.data ? ` ${JSON.stringify(entry.data)}` : ''
  return `[${time}] ${entry.level || 'info'} ${entry.source || '-'}/${entry.stage || '-'} ${entry.message || ''}${dur}${data}`
}

function appendLogs(entries = []) {
  if (!entries?.length) return
  const block = entries.map(formatLogLine).join('\n')
  els.execLogs.textContent = `${els.execLogs.textContent}${els.execLogs.textContent ? '\n' : ''}${block}\n`
  els.execLogs.scrollTop = els.execLogs.scrollHeight
}

function appendLocal(stage, message, data) {
  logUi(stage, message, data)
  appendLogs([
    {
      ts: new Date().toISOString(),
      level: 'info',
      source: 'ui',
      stage,
      message,
      data,
    },
  ])
}

function showChat() {
  els.newSession.hidden = true
  els.chat.hidden = false
}

function showNew() {
  state.sessionId = null
  els.messages.innerHTML = ''
  els.chat.hidden = true
  els.newSession.hidden = false
  els.resumeBtn.hidden = true
  appendLocal('ui.reset', '开始新会话视图')
}

async function refreshWsStatus() {
  try {
    const status = await chrome.runtime.sendMessage({ type: 'ws_status' })
    els.wsStatus.textContent = status?.connected ? 'WS: connected' : 'WS: disconnected'
  } catch (_) {
    els.wsStatus.textContent = 'WS: unknown'
  }
}

els.clearLogsBtn.addEventListener('click', () => {
  els.execLogs.textContent = ''
})

els.startBtn.addEventListener('click', async () => {
  const requirement = els.requirement.value.trim()
  const sites = selectedSites()
  if (!requirement || !sites.length) {
    alert('请填写需求并至少选择一个站点')
    return
  }
  els.startBtn.disabled = true
  appendLocal('ui.start', '创建会话并开始选品', { requirement, sites })
  try {
    const created = await createSession(requirement, sites)
    state.sessionId = created.session_id
    showChat()
    appendBubble('user', requirement)
    appendLocal('ui.message', '发送首轮消息', { session_id: state.sessionId })
    const result = await sendMessage(state.sessionId, requirement)
    appendBubble('assistant', result.reply, result.recommendations)
    appendLogs(result.execution_logs || [])
    els.resumeBtn.hidden = !result.interrupted
  } catch (error) {
    appendLocal('ui.error', String(error.message || error))
    alert(String(error.message || error))
  } finally {
    els.startBtn.disabled = false
  }
})

els.sendBtn.addEventListener('click', async () => {
  const content = els.followup.value.trim()
  if (!content || !state.sessionId) return
  els.followup.value = ''
  appendBubble('user', content)
  appendLocal('ui.followup', '发送追问', { content })
  try {
    const result = await sendMessage(state.sessionId, content)
    appendBubble('assistant', result.reply, result.recommendations)
    appendLogs(result.execution_logs || [])
    els.resumeBtn.hidden = !result.interrupted
  } catch (error) {
    appendBubble('assistant', `错误：${error.message || error}`)
    appendLocal('ui.error', String(error.message || error))
  }
})

els.resumeBtn.addEventListener('click', async () => {
  if (!state.sessionId) return
  appendLocal('ui.resume', '继续中断会话')
  try {
    const result = await resumeSession(state.sessionId, 'user continued')
    appendBubble('assistant', result.reply, result.recommendations)
    appendLogs(result.execution_logs || [])
    els.resumeBtn.hidden = !result.interrupted
  } catch (error) {
    appendBubble('assistant', `错误：${error.message || error}`)
    appendLocal('ui.error', String(error.message || error))
  }
})

els.resetBtn.addEventListener('click', showNew)

refreshWsStatus()
setInterval(refreshWsStatus, 3000)
