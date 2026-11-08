const state = {
  selectedId: null,
}

const els = {
  health: document.getElementById('health'),
  refresh: document.getElementById('refresh'),
  sessionList: document.getElementById('sessionList'),
  sessionDetail: document.getElementById('sessionDetail'),
}

async function api(path) {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
  return response.json()
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

async function loadHealth() {
  try {
    const data = await api('/health')
    els.health.textContent = `API ok · sessions ${data.session_count} · mock ${data.mock_browser} · ext ${data.extension_online}`
  } catch (error) {
    els.health.textContent = `API offline: ${error.message}`
  }
}

async function loadSessions() {
  const sessions = await api('/api/v1/sessions?limit=50')
  if (!sessions.length) {
    els.sessionList.innerHTML = '<p class="empty">暂无会话。用 Side Panel 跑一轮选品后刷新。</p>'
    return
  }
  els.sessionList.innerHTML = sessions
    .map(
      (item) => `
      <button class="item ${item.session_id === state.selectedId ? 'active' : ''}" data-id="${item.session_id}" type="button">
        <div class="title">${escapeHtml(item.requirement).slice(0, 80)}</div>
        <div class="sub">${escapeHtml(item.status)} · ${escapeHtml((item.sites || []).join(','))} · 商品 ${item.product_count} · 推荐 ${item.recommendation_count}</div>
        <div class="sub">${escapeHtml(item.updated_at)}</div>
      </button>
    `,
    )
    .join('')

  for (const button of els.sessionList.querySelectorAll('.item')) {
    button.addEventListener('click', () => {
      state.selectedId = button.dataset.id
      loadSessions()
      loadDetail(state.selectedId)
    })
  }
}

async function loadDetail(sessionId) {
  const data = await api(`/api/v1/sessions/${sessionId}`)
  const recs = (data.recommendations || [])
    .map(
      (item) => `
      <article class="card">
        <strong>${escapeHtml(item.title)}</strong>
        <div class="muted">${escapeHtml(item.reason)}</div>
        <div class="muted">${escapeHtml(item.currency || '')} ${escapeHtml(item.price ?? '')} · ${escapeHtml(item.url || '')}</div>
      </article>
    `,
    )
    .join('')

  const products = (data.products || [])
    .slice(0, 30)
    .map(
      (item) => `
      <article class="card">
        <strong>${escapeHtml(item.title)}</strong>
        <div class="muted">${escapeHtml(item.source)} · ${escapeHtml(item.currency || '')} ${escapeHtml(item.price ?? '')} · CNY ${escapeHtml(item.price_cny ?? '')}</div>
      </article>
    `,
    )
    .join('')

  const messages = (data.messages || [])
    .map(
      (item) => `
      <div class="bubble ${escapeHtml(item.role)}">${escapeHtml(item.content)}</div>
    `,
    )
    .join('')

  const logs = (data.execution_logs || [])
    .map((item) => {
      const time = escapeHtml((item.ts || '').slice(11, 19))
      const dur = item.duration_ms != null ? ` ${escapeHtml(item.duration_ms)}ms` : ''
      const dataText = item.data ? ` ${escapeHtml(JSON.stringify(item.data))}` : ''
      return `<div class="log-line level-${escapeHtml(item.level || 'info')}"><span class="t">${time}</span> <span class="s">${escapeHtml(item.source)}/${escapeHtml(item.stage)}</span> ${escapeHtml(item.message)}${dur}${dataText}</div>`
    })
    .join('')

  els.sessionDetail.innerHTML = `
    <h3>${escapeHtml(data.requirement)}</h3>
    <p class="muted">${escapeHtml(data.status)} · sites ${escapeHtml((data.sites || []).join(','))} · searched ${escapeHtml((data.searched_sites || []).join(',') || '-')}</p>
    <div class="block">
      <h3>执行日志（${(data.execution_logs || []).length}）</h3>
      <div class="log-stream">${logs || '<p class="empty">暂无执行日志</p>'}</div>
    </div>
    <div class="block">
      <h3>推荐</h3>
      <div class="cards">${recs || '<p class="empty">无推荐</p>'}</div>
    </div>
    <div class="block">
      <h3>对话</h3>
      <div class="messages">${messages || '<p class="empty">无消息</p>'}</div>
    </div>
    <div class="block">
      <h3>商品缓存（最多 30）</h3>
      <div class="cards">${products || '<p class="empty">无商品</p>'}</div>
    </div>
  `
}

async function refresh() {
  await loadHealth()
  await loadSessions()
  if (state.selectedId) {
    await loadDetail(state.selectedId)
  }
}

els.refresh.addEventListener('click', () => {
  refresh().catch((error) => {
    els.sessionDetail.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`
  })
})

refresh().catch((error) => {
  els.sessionDetail.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`
})
