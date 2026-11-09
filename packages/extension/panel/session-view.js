export function isCurrentGeneration(requestGeneration, currentGeneration) {
  return requestGeneration === currentGeneration
}

export function shouldShowResume(detail) {
  return (
    detail?.status === 'interrupted' &&
    !(detail?.pending_questions || []).length
  )
}

export function progressText(detail) {
  const logs = detail?.execution_logs || []
  const last = logs[logs.length - 1]
  return `执行中… 商品 ${detail?.products?.length || 0}，阶段 ${last?.stage || '准备中'}`
}
