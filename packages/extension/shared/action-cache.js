const DB_NAME = 'shopping-agent-actions'
const STORE = 'results'
const memory = new Map()

function hasIndexedDb() {
  return typeof indexedDB !== 'undefined'
}

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'action_id' })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export async function getCachedResult(actionId) {
  if (!actionId) return null
  if (!hasIndexedDb()) return memory.get(actionId) || null
  try {
    const db = await openDb()
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly')
      const request = tx.objectStore(STORE).get(actionId)
      request.onsuccess = () => resolve(request.result?.result || null)
      request.onerror = () => reject(request.error)
    })
  } catch (_) {
    return memory.get(actionId) || null
  }
}

export async function setCachedResult(actionId, result) {
  if (!actionId) return
  memory.set(actionId, result)
  if (!hasIndexedDb()) return
  try {
    const db = await openDb()
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite')
      tx.objectStore(STORE).put({ action_id: actionId, result })
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch (_) {
    /* memory fallback is enough for the current worker */
  }
}
