import {
  createSession,
  getSession,
  sendMessage,
  resumeSession,
} from '../shared/api.js'
import { logUi } from '../shared/logger.js'
import {
  isCurrentGeneration,
  progressText,
  shouldShowResume,
} from './session-view.js'

const state = {
  sessionId: null,
  requestGeneration: 0,
  clarifyCards: [],
  pollTimer: null,
  busy: false,
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
  turnStatus: document.getElementById('turnStatus'),
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

// Structured clarify card: one row per question — option buttons + free input.
function appendClarify(questions = []) {
  if (!questions.length) return
  const card = document.createElement('div')
  card.className = 'bubble assistant clarify'
  card.dataset.clarify = '1'
  const getters = []

  for (const q of questions) {
    const row = document.createElement('div')
    row.className = 'clarify-q'
    const label = document.createElement('div')
    label.className = 'clarify-q-text'
    label.textContent = q.question
    row.appendChild(label)

    let picked = null
    const clearPicked = () => {
      for (const b of row.querySelectorAll('.clarify-opt')) b.classList.remove('picked')
      picked = null
    }
    for (const opt of q.options || []) {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'clarify-opt'
      btn.textContent = opt
      btn.addEventListener('click', () => {
        clearPicked()
        btn.classList.add('picked')
        picked = opt
        free.value = ''
      })
      row.appendChild(btn)
    }
    const free = document.createElement('input')
    free.type = 'text'
    free.placeholder = '或输入你自己的答案'
    free.addEventListener('input', () => {
      if (free.value.trim()) clearPicked()
    })
    row.appendChild(free)
    getters.push([q.id, () => free.value.trim() || picked || ''])
    card.appendChild(row)
  }

  const actions = document.createElement('div')
  actions.className = 'clarify-actions'
  const submit = document.createElement('button')
  submit.type = 'button'
  submit.textContent = '提交回答'
  submit.addEventListener('click', async () => {
    const answers = {}
    for (const [qid, get] of getters) {
      const value = get()
      if (value) answers[qid] = value
    }
    if (!Object.keys(answers).length) {
      alert('请至少回答一个问题，或点「跳过」')
      return
    }
    const generation = beginRequest()
    submit.disabled = true
    appendLocal('ui.clarify', '提交澄清回答', answers)
    try {
      startPolling(state.sessionId, generation)
      handleResult(
        await sendMessage(
          state.sessionId,
          '',
          answers,
          state.clientId,
        ),
        generation,
      )
      card.remove()
    } catch (error) {
      if (isCurrentGeneration(generation, state.requestGeneration)) {
        appendLocal('ui.error', String(error.message || error))
      }
    } finally {
      if (isCurrentGeneration(generation, state.requestGeneration)) {
        setBusy(false)
      }
    }
  })
  const skip = document.createElement('button')
  skip.type = 'button'
  skip.className = 'linkish'
  skip.textContent = '跳过，直接开始'
  skip.addEventListener('click', async () => {
    const generation = beginRequest()
    skip.disabled = true
    try {
      startPolling(state.sessionId, generation)
      handleResult(
        await sendMessage(
          state.sessionId,
          '跳过澄清，直接开始',
          undefined,
          state.clientId,
        ),
        generation,
      )
      card.remove()
    } catch (error) {
      if (isCurrentGeneration(generation, state.requestGeneration)) {
        appendLocal('ui.error', String(error.message || error))
      }
    } finally {
      if (isCurrentGeneration(generation, state.requestGeneration)) {
        setBusy(false)
      }
    }
  })
  actions.appendChild(submit)
  actions.appendChild(skip)
  card.appendChild(actions)
  els.messages.appendChild(card)
  state.clarifyCards.push(card)
  els.messages.scrollTop = els.messages.scrollHeight
}

function clearClarifyCards() {
  for (const card of state.clarifyCards) card.remove()
  state.clarifyCards = []
}

function setBusy(busy) {
  state.busy = busy
  els.sendBtn.disabled = busy
  els.resumeBtn.disabled = busy
  els.startBtn.disabled = busy
  els.followup.disabled = busy
  if (!busy) {
    els.turnStatus.hidden = true
    stopPolling()
  }
}

function stopPolling() {
  clearInterval(state.pollTimer)
  state.pollTimer = null
}

function startPolling(sessionId, generation) {
  stopPolling()
  if (!sessionId) return
  state.pollTimer = setInterval(async () => {
    if (
      !isCurrentGeneration(generation, state.requestGeneration) ||
      !state.busy
    ) {
      stopPolling()
      return
    }
    try {
      const detail = await getSession(sessionId)
      els.turnStatus.hidden = false
      els.turnStatus.textContent = progressText(detail)
    } catch (_) {
      /* keep the current status */
    }
  }, 2000)
}

async function persistSession(sessionId) {
  try {
    await chrome.storage.local.set({ sessionId })
  } catch (_) {
    /* best effort */
  }
}

async function clearPersistedSession() {
  try {
    await chrome.storage.local.remove('sessionId')
  } catch (_) {
    /* best effort */
  }
}

function renderHistory(detail) {
  els.messages.innerHTML = ''
  state.clarifyCards = []
  for (const message of detail.messages || []) {
    if (!message?.content) continue
    appendBubble(message.role === 'user' ? 'user' : 'assistant', message.content)
  }
  if (detail.recommendations?.length) {
    appendBubble('assistant', '已恢复的推荐', detail.recommendations)
  }
  appendLogs(detail.execution_logs || [])
  els.resumeBtn.hidden = !shouldShowResume(detail)
  if (detail.pending_questions?.length) {
    appendClarify(detail.pending_questions)
  }
}

async function restoreSession() {
  const generation = state.requestGeneration + 1
  state.requestGeneration = generation
  let sessionId = null
  try {
    const stored = await chrome.storage.local.get({ sessionId: null })
    sessionId = stored.sessionId
  } catch (_) {
    return
  }
  if (!sessionId) return
  try {
    const detail = await getSession(sessionId)
    if (!isCurrentGeneration(generation, state.requestGeneration)) return
    state.sessionId = sessionId
    showChat()
    renderHistory(detail)
    if (detail.status === 'running') {
      setBusy(true)
      startPolling(sessionId, generation)
    }
    appendLocal('ui.restore', '恢复上次会话', { session_id: sessionId })
  } catch (error) {
    if (!isCurrentGeneration(generation, state.requestGeneration)) return
    appendLocal('ui.error', `恢复会话失败：${error.message || error}`)
    await clearPersistedSession()
  }
}

function handleResult(result, generation = state.requestGeneration) {
  if (!isCurrentGeneration(generation, state.requestGeneration)) return
  appendBubble('assistant', result.reply, result.recommendations)
  appendLogs(result.execution_logs || [])
  const hasClarify = Boolean(result.clarify?.length)
  els.resumeBtn.hidden = !result.interrupted || hasClarify
  if (hasClarify) appendClarify(result.clarify)
  setBusy(false)
}

function showChat() {
  els.newSession.hidden = true
  els.chat.hidden = false
}

function showNew() {
  state.requestGeneration += 1
  state.sessionId = null
  state.clarifyCards = []
  stopPolling()
  setBusy(false)
  els.messages.innerHTML = ''
  els.chat.hidden = true
  els.newSession.hidden = false
  els.resumeBtn.hidden = true
  clearPersistedSession()
  appendLocal('ui.reset', '开始新会话视图')
}

function newClientId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `cm-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function beginRequest() {
  state.requestGeneration += 1
  state.clientId = newClientId()
  clearClarifyCards()
  setBusy(true)
  return state.requestGeneration
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
  const generation = beginRequest()
  appendLocal('ui.start', '创建会话并开始选品', { requirement, sites })
  try {
    const created = await createSession(requirement, sites)
    if (!isCurrentGeneration(generation, state.requestGeneration)) return
    state.sessionId = created.session_id
    await persistSession(state.sessionId)
    showChat()
    appendBubble('user', requirement)
    appendLocal('ui.message', '发送首轮消息', { session_id: state.sessionId })
    startPolling(state.sessionId, generation)
    const result = await sendMessage(
      state.sessionId,
      requirement,
      undefined,
      state.clientId,
    )
    handleResult(result, generation)
  } catch (error) {
    if (generation === state.requestGeneration) {
      appendLocal('ui.error', String(error.message || error))
      alert(String(error.message || error))
    }
  } finally {
    if (generation === state.requestGeneration) setBusy(false)
  }
})

els.sendBtn.addEventListener('click', async () => {
  const content = els.followup.value.trim()
  if (!content || !state.sessionId || state.busy) return
  const generation = beginRequest()
  els.followup.value = ''
  appendBubble('user', content)
  appendLocal('ui.followup', '发送追问', { content })
  try {
    startPolling(state.sessionId, generation)
    handleResult(
      await sendMessage(
        state.sessionId,
        content,
        undefined,
        state.clientId,
      ),
      generation,
    )
  } catch (error) {
    if (generation === state.requestGeneration) {
      appendBubble('assistant', `错误：${error.message || error}`)
      appendLocal('ui.error', String(error.message || error))
    }
  } finally {
    if (generation === state.requestGeneration) setBusy(false)
  }
})

els.resumeBtn.addEventListener('click', async () => {
  if (!state.sessionId || state.busy) return
  const generation = beginRequest()
  appendLocal('ui.resume', '继续中断会话')
  try {
    startPolling(state.sessionId, generation)
    handleResult(
      await resumeSession(
        state.sessionId,
        'user continued',
        state.clientId,
      ),
      generation,
    )
  } catch (error) {
    if (generation === state.requestGeneration) {
      appendBubble('assistant', `错误：${error.message || error}`)
      appendLocal('ui.error', String(error.message || error))
    }
  } finally {
    if (generation === state.requestGeneration) setBusy(false)
  }
})

els.resetBtn.addEventListener('click', showNew)

refreshWsStatus()
setInterval(refreshWsStatus, 3000)
restoreSession()
