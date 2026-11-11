const { test } = require('node:test')
const assert = require('node:assert/strict')
const { getAdapter } = require('../adapters/registry.js')

test('registry returns complete jd and ebay packs', () => {
  for (const site of ['jd', 'ebay']) {
    const adapter = getAdapter(site)
    assert.equal(adapter.id, site)
    for (const method of [
      'matchUrl',
      'detectCurrency',
      'buildSearchUrl',
      'parseListing',
      'parseDetail',
      'nextPageUrl',
      'detectBlock',
    ]) {
      assert.equal(typeof adapter[method], 'function', `${site}.${method}`)
    }
  }
})

test('registry rejects unknown sites', () => {
  assert.throws(
    () => getAdapter('amazon'),
    (error) => error.code === 'adapter_missing',
  )
})
