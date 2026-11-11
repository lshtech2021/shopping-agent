export function isCurrentGeneration(requestGeneration, currentGeneration) {
  return requestGeneration === currentGeneration
}

export function shouldShowResume(detail) {
  return (
    ['interrupted', 'blocked'].includes(detail?.status) &&
    !(detail?.pending_questions || []).length
  )
}

export function formatAnswersAsMessage(answers) {
  return Object.entries(answers || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}: ${value}`)
    .join('\n')
}

export function formatSessionSettings(settings = {}) {
  const site = settings.site === 'ebay' ? 'eBay' : '京东'
  return `站点: ${site} · 货币: ${settings.currency || '待检测'}`
}

export function findCurrencyMismatch(
  result = {},
  settings = result.settings || {},
) {
  if (!['interrupted', 'blocked'].includes(result.status)) return null

  const currentCurrency = String(settings.currency || '').toUpperCase()
  const currentMismatch = (value) => {
    if (value?.error !== 'currency_mismatch') return null
    const pageCurrency = String(value.page_currency || '').toUpperCase()
    const sessionCurrency = String(value.session_currency || '').toUpperCase()
    if (
      !currentCurrency ||
      sessionCurrency !== currentCurrency ||
      pageCurrency === currentCurrency
    ) {
      return null
    }
    return {
      pageCurrency: pageCurrency || null,
      sessionCurrency: sessionCurrency || null,
    }
  }

  if (result.error === 'currency_mismatch') return currentMismatch(result)

  const logs = result.execution_logs || []
  for (let index = logs.length - 1; index >= 0; index -= 1) {
    const value = logs[index]?.data?.result
    if (!value || (!value.status && !value.error)) continue
    return currentMismatch(value)
  }
  return null
}

export function progressText(detail, events = []) {
  const fromEvents = events.map((row) => row?.event).filter(Boolean)
  const logs = fromEvents.length
    ? fromEvents
    : detail?.execution_logs || []
  const last = logs[logs.length - 1]
  const stage = last?.stage || '准备中'
  const message = last?.message ? ` · ${last.message}` : ''
  return `执行中… 商品 ${detail?.products?.length || 0}，阶段 ${stage}${message}`
}
