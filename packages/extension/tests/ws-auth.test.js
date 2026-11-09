const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const WS_URL_JS = path.join(__dirname, '..', 'shared', 'ws-url.js')

async function load() {
  return import(`${pathToFileURL(WS_URL_JS).href}?t=${Date.now()}`)
}

test('buildWebSocketUrl appends the extension token', async () => {
  const { buildWebSocketUrl } = await load()
  assert.equal(
    buildWebSocketUrl('http://127.0.0.1:8100', 'secret'),
    'ws://127.0.0.1:8100/ws/extension?token=secret',
  )
})

test('buildWebSocketUrl omits an empty token', async () => {
  const { buildWebSocketUrl } = await load()
  assert.equal(
    buildWebSocketUrl('https://example.test/', ''),
    'wss://example.test/ws/extension',
  )
})
