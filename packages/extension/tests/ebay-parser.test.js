const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { JSDOM } = require('jsdom')

const ebay = require('../content/ebay.js')

function loadDom(name, url) {
  const html = fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8')
  return new JSDOM(html, { url })
}

test('parseListing extracts ebay cards and skips shop on ebay', () => {
  const dom = loadDom(
    'ebay-list.html',
    'https://www.ebay.com/sch/i.html?_nkw=laptop',
  )
  const result = ebay.parseListing(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.equal(result.items.length, 2)
  assert.equal(result.items[0].source_product_id, '2001')
  assert.equal(result.items[0].currency, 'USD')
  assert.equal(result.items[0].price, 620)
  assert.equal(result.result_count, 2)
  assert.equal(result.has_next, true)
  assert.ok('rating' in result.items[0])
  assert.ok(ebay.nextPageUrl(dom.window.document, dom.window.location.href))
})

test('legitimate zero-result search is not a parse error', () => {
  const dom = new JSDOM(
    '<html><body><div class="srp-save-null-search">No exact matches found</div></body></html>',
    { url: 'https://www.ebay.com/sch/i.html?_nkw=nothing' },
  )
  const result = ebay.parseListing(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.equal(result.items.length, 0)
  assert.equal(result.no_results, true)
})

test('parseDetail extracts ebay title and attributes', () => {
  const dom = loadDom('ebay-detail.html', 'https://www.ebay.com/itm/2001')
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.match(result.title, /ThinkPad/)
  assert.equal(result.attributes['RAM Size'], '32 GB')
})

test('classifies eBay error, challenge and login pages', () => {
  const error = loadDom(
    'ebay-error.html',
    'https://www.ebay.com/sch/i.html?_nkw=laptop',
  )
  const errorResult = ebay.parseListing(
    error.window.document,
    error.window.location.href,
  )
  assert.equal(errorResult.status, 'rate_limited')
  assert.equal(errorResult.error, 'ebay_error_page')

  const challenge = loadDom(
    'ebay-challenge.html',
    'https://www.ebay.com/sch/i.html?_nkw=laptop',
  )
  assert.equal(
    ebay.detectBlock(
      challenge.window.document,
      challenge.window.location.href,
    ),
    'captcha',
  )

  const login = loadDom(
    'ebay-login.html',
    'https://www.ebay.com/sch/i.html?_nkw=laptop',
  )
  assert.equal(
    ebay.detectBlock(login.window.document, login.window.location.href),
    'login_required',
  )
})

test('matchesUrl accepts search and category pages only', () => {
  assert.equal(
    ebay.matchesUrl('https://www.ebay.com/sch/i.html?_nkw=laptop'),
    true,
  )
  assert.equal(ebay.matchesUrl('https://www.ebay.com/b/Laptops/177'), true)
  assert.equal(ebay.matchesUrl('https://www.ebay.com/itm/2001'), false)
  assert.equal(ebay.matchesUrl('https://example.com/sch/i.html'), false)
})

test('waitForListing resolves for usable cards and times out otherwise', async () => {
  const listing = loadDom(
    'ebay-list.html',
    'https://www.ebay.com/sch/i.html?_nkw=laptop',
  )
  assert.equal(
    await ebay.waitForListing(
      listing.window.document,
      listing.window.location.href,
      100,
    ),
    true,
  )

  const loading = new JSDOM(
    '<html><body><div>Loading results...</div></body></html>',
    { url: 'https://www.ebay.com/sch/i.html?_nkw=laptop' },
  )
  assert.equal(
    await ebay.waitForListing(
      loading.window.document,
      loading.window.location.href,
      80,
    ),
    false,
  )
})

test('waitForDetail resolves for a hydrated detail page', async () => {
  const detail = loadDom('ebay-detail.html', 'https://www.ebay.com/itm/2001')
  assert.equal(
    await ebay.waitForDetail(
      detail.window.document,
      detail.window.location.href,
      100,
    ),
    true,
  )
})
