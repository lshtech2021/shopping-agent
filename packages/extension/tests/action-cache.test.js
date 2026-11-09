const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const CACHE_JS = path.join(__dirname, '..', 'shared', 'action-cache.js')

test('action cache returns the cached observation for a duplicate action', async () => {
  const cache = await import(
    `${pathToFileURL(CACHE_JS).href}?t=${Date.now()}`
  )
  const actionId = `test-${Date.now()}`

  assert.equal(await cache.getCachedResult(actionId), null)
  await cache.setCachedResult(actionId, { status: 'ok', items: [1] })
  assert.deepEqual(await cache.getCachedResult(actionId), {
    status: 'ok',
    items: [1],
  })
})
