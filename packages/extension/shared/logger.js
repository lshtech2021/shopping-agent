const listeners = new Set()

function nowIso() {
  return new Date().toISOString()
}

function emit(entry) {
  for (const listener of listeners) {
    try {
      listener(entry)
    } catch (_) {
      /* ignore */
    }
  }
  const prefix = `[shopping-agent][${entry.level}] ${entry.stage}`
  const payload = entry.data ? [prefix, entry.message, entry.data] : [prefix, entry.message]
  if (entry.level === 'error') console.error(...payload)
  else if (entry.level === 'warning') console.warn(...payload)
  else console.log(...payload)
}

export function onLog(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function createJobLogger(tool) {
  const logs = []
  const started = performance.now()

  function push(level, stage, message, data) {
    const entry = {
      ts: nowIso(),
      level,
      source: 'extension',
      stage,
      message,
      data: data || undefined,
      duration_ms: Math.round(performance.now() - started),
    }
    logs.push(entry)
    emit(entry)
    return entry
  }

  return {
    info: (stage, message, data) => push('info', stage || `extension.${tool}`, message, data),
    warn: (stage, message, data) => push('warning', stage || `extension.${tool}`, message, data),
    error: (stage, message, data) => push('error', stage || `extension.${tool}`, message, data),
    debug: (stage, message, data) => push('debug', stage || `extension.${tool}`, message, data),
    entries: () => logs.slice(),
  }
}

export function logUi(stage, message, data) {
  emit({
    ts: nowIso(),
    level: 'info',
    source: 'extension-ui',
    stage,
    message,
    data,
  })
}
