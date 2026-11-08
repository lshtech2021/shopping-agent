import { createSession, sendMessage, resumeSession } from '../shared/api.js'

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
}

async function refreshWsStatus() {
  try {
    const status = await chrome.runtime.sendMessage({ type: 'ws_status' })
    els.wsStatus.textContent = status?.connected ? 'WS: connected' : 'WS: disconnected'
  } catch (_) {
    els.wsStatus.textContent = 'WS: unknown'
  }
}

els.startBtn.addEventListener('click', async () => {
  const requirement = els.requirement.value.trim()
  const sites = selectedSites()
  if (!requirement || !sites.length) {
    alert('请填写需求并至少选择一个站点')
    return
  }
  els.startBtn.disabled = true
  try {
    const created = await createSession(requirement, sites)
    state.sessionId = created.session_id
    showChat()
    appendBubble('user', requirement)
    const result = await sendMessage(state.sessionId, requirement)
    appendBubble('assistant', result.reply, result.recommendations)
    els.resumeBtn.hidden = !result.interrupted
  } catch (error) {
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
  try {
    const result = await sendMessage(state.sessionId, content)
    appendBubble('assistant', result.reply, result.recommendations)
    els.resumeBtn.hidden = !result.interrupted
  } catch (error) {
    appendBubble('assistant', `错误：${error.message || error}`)
  }
})

els.resumeBtn.addEventListener('click', async () => {
  if (!state.sessionId) return
  try {
    const result = await resumeSession(state.sessionId, 'user continued')
    appendBubble('assistant', result.reply, result.recommendations)
    els.resumeBtn.hidden = !result.interrupted
  } catch (error) {
    appendBubble('assistant', `错误：${error.message || error}`)
  }
})

els.resetBtn.addEventListener('click', showNew)

refreshWsStatus()
setInterval(refreshWsStatus, 3000)
