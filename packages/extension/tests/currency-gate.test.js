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

test('ebay detects currency from geo control before listing prices', () => {
  const dom = new JSDOM(`
    <div id="gh-eb-Geo">Ship to Canada · CAD</div>
    <span class="s-item__price">US $12.00</span>
  `)

  assert.equal(getAdapter('ebay').detectCurrency(dom.window.document), 'CAD')
})

test('jd always detects CNY', () => {
  assert.equal(getAdapter('jd').detectCurrency(), 'CNY')
})
