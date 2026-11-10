export function isCurrentGeneration(requestGeneration, currentGeneration) {
  return requestGeneration === currentGeneration
}

export function shouldShowResume(detail) {
  return (
    detail?.status === 'interrupted' &&
    !(detail?.pending_questions || []).length
  )
}

export function formatAnswersAsMessage(answers) {
  return Object.entries(answers || {})
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}: ${value}`)
    .join('\n')
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
