const { test } = require('node:test')
const assert = require('node:assert/strict')
const {
  getAdapter,
  getAdapterByUrl,
} = require('../adapters/registry.js')

test('registry returns complete jd and ebay packs', () => {
  for (const site of ['jd', 'ebay']) {
    const adapter = getAdapter(site)
    assert.equal(adapter.id, site)
    for (const method of [
      'matchUrl',
      'detailUrl',
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

test('registry resolves adapters from pack-owned URL matching', () => {
  assert.equal(getAdapterByUrl('https://item.jd.com/123.html').id, 'jd')
  assert.equal(
    getAdapterByUrl('https://www.ebay.com/itm/123').id,
    'ebay',
  )
  assert.equal(getAdapterByUrl('https://example.com/123'), null)
})

test('packs own their detail URL shapes', () => {
  assert.equal(
    getAdapter('jd').detailUrl('123'),
    'https://item.jd.com/123.html',
  )
  assert.equal(
    getAdapter('ebay').detailUrl('456'),
    'https://www.ebay.com/itm/456',
  )
})

test('registry rejects unknown sites', () => {
  assert.throws(
    () => getAdapter('amazon'),
    (error) => error.code === 'adapter_missing',
  )
})
