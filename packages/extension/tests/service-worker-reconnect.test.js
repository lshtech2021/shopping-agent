const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const SERVICE_WORKER_JS = path.join(
  __dirname,
  '..',
  'background',
  'service-worker.js',
)

test('saving options does not create a reconnect loop', async () => {
  const reconnectTimers = new Map()
  let nextTimer = 1
  const realSetTimeout = globalThis.setTimeout
  const realClearTimeout = globalThis.clearTimeout
  const runtimeListeners = []

  class FakeWebSocket {
    static instances = []

    constructor(url) {
      this.url = url
      this.readyState = 0
      this.listeners = {}
      FakeWebSocket.instances.push(this)
    }

    addEventListener(type, fn) {
      ;(this.listeners[type] ||= []).push(fn)
    }

    send() {}

    close() {
      this.readyState = 3
      for (const fn of this.listeners.close || []) fn()
    }
  }
  FakeWebSocket.OPEN = 1

  globalThis.setTimeout = (fn, ms) => {
    if (ms === 1000) {
      const id = nextTimer
      nextTimer += 1
      reconnectTimers.set(id, fn)
      return id
    }
    return realSetTimeout(fn, ms)
  }
  globalThis.clearTimeout = (id) => {
    if (reconnectTimers.delete(id)) return
    realClearTimeout(id)
  }
  globalThis.WebSocket = FakeWebSocket
  globalThis.chrome = {
    runtime: {
      onInstalled: { addListener() {} },
      onMessage: { addListener(fn) { runtimeListeners.push(fn) } },
    },
    storage: {
      local: { async get(defaults) { return defaults }, async set() {} },
      session: { async get() { return {} }, async set() {} },
    },
  }

  try {
    await import(
      `${pathToFileURL(SERVICE_WORKER_JS).href}?t=${Date.now()}`
    )
    await new Promise((resolve) => realSetTimeout(resolve, 0))
    assert.equal(FakeWebSocket.instances.length, 1)

    const onMessage = runtimeListeners[0]
    onMessage({ type: 'reconnect_ws' }, {}, () => {})
    await new Promise((resolve) => realSetTimeout(resolve, 0))

    assert.equal(FakeWebSocket.instances.length, 2)
    assert.equal(
      reconnectTimers.size,
      0,
      'the superseded socket must not schedule a reconnect',
    )
  } finally {
    globalThis.setTimeout = realSetTimeout
    globalThis.clearTimeout = realClearTimeout
    delete globalThis.WebSocket
    delete globalThis.chrome
  }
})
