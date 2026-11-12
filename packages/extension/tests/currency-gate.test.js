const test = require('node:test')
const assert = require('node:assert/strict')
const { JSDOM } = require('jsdom')
const { assertBrowseAllowed } = require('../background/browse-guards.js')
const { getAdapter } = require('../adapters/registry.js')

test('currency_mismatch when page differs', () => {
  const result = assertBrowseAllowed({
    sessionSite: 'ebay',
    sessionCurrency: 'CNY',
    tabUrl: 'https://www.ebay.com/sch/i.html',
    pageCurrency: 'USD',
  })

  assert.deepEqual(result, {
    ok: false,
    error: 'currency_mismatch',
    page_currency: 'USD',
    session_currency: 'CNY',
  })
})

test('site_mismatch when tab URL does not match the session adapter', () => {
  const result = assertBrowseAllowed({
    sessionSite: 'jd',
    sessionCurrency: 'CNY',
    tabUrl: 'https://www.ebay.com/sch/i.html',
    pageCurrency: 'CNY',
  })

  assert.equal(result.ok, false)
  assert.equal(result.error, 'site_mismatch')
})

test('allows when site and currencies match', () => {
  const result = assertBrowseAllowed({
    sessionSite: 'ebay',
    sessionCurrency: 'USD',
    tabUrl: 'https://www.ebay.com/sch/i.html',
    pageCurrency: 'USD',
  })

  assert.deepEqual(result, { ok: true })
})

test('ebay price samples win over the geo control', () => {
  const dom = new JSDOM(`
    <div id="gh-eb-Geo">Ship to Canada · CAD</div>
    <span class="s-item__price">US $12.00</span>
  `)

  assert.equal(getAdapter('ebay').detectCurrency(dom.window.document), 'USD')
})

test('ebay detects CNY, bare dollar and geo fallback', () => {
  const cny = new JSDOM(
    '<span class="s-item__price">RMB 88.00</span>',
  )
  assert.equal(getAdapter('ebay').detectCurrency(cny.window.document), 'CNY')

  const usd = new JSDOM('<span class="s-item__price">$12.00</span>')
  assert.equal(getAdapter('ebay').detectCurrency(usd.window.document), 'USD')

  const geo = new JSDOM('<div id="gh-eb-Geo">Ship to Canada · CAD</div>')
  assert.equal(getAdapter('ebay').detectCurrency(geo.window.document), 'CAD')
})

test('ebay listing cards outvote a hidden detail-page price', () => {
  const dom = new JSDOM(`
    <div class="s-card">
      <span class="x-price-primary">US $99.00</span>
      <span class="s-card__price">RMB 688.00</span>
    </div>
  `)

  assert.equal(getAdapter('ebay').detectCurrency(dom.window.document), 'CNY')
})

test('ebay returns null when an empty page has no currency signal', () => {
  const dom = new JSDOM('<html><body></body></html>')

  assert.equal(getAdapter('ebay').detectCurrency(dom.window.document), null)
})

test('jd always detects CNY', () => {
  assert.equal(getAdapter('jd').detectCurrency(), 'CNY')
})
