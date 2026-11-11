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

test('parseListing enriches modern eBay cards', () => {
  const dom = loadDom(
    'ebay-list-modern.html',
    'https://www.ebay.com/sch/i.html?_nkw=thinkpad&_ipg=60',
  )
  const result = ebay.parseListing(dom.window.document, dom.window.location.href)

  assert.equal(result.status, 'ok')
  assert.equal(result.items.length, 2)
  const [first, second] = result.items

  assert.equal(first.title, 'Lenovo ThinkPad T14')
  assert.equal(first.url, 'https://www.ebay.com/itm/111222333444?var=555')
  assert.equal(
    first.image_url,
    'https://i.ebayimg.com/images/g/abc/s-l1600.jpg',
  )
  assert.equal(first.price, 620)
  assert.equal(first.price_max, 780)
  assert.equal(first.price_is_range, true)
  assert.equal(first.currency, 'USD')
  assert.equal(first.condition, 'Pre-Owned')
  assert.equal(first.shipping, 'Free delivery')
  assert.equal(first.sales, 12)
  assert.equal(first.watchers, null)
  assert.equal(first.rating, 4.6)
  assert.equal(first.sponsored, true)
  assert.equal(first.category, 'Laptops')

  assert.equal(second.price, 12800)
  assert.equal(second.currency, 'JPY')
  assert.equal(second.sales, null)
  assert.equal(second.watchers, 10)
  assert.equal(second.rating, 4.5)
  assert.equal(second.image_url, 'https://i.ebayimg.com/images/g/jpy/s-l1600.jpg')

  assert.equal(result.result_count, 1234)
  assert.equal(result.page_item_count, 2)
  assert.equal(result.page, 1)
  assert.equal(result.has_next, true)
  assert.equal(
    result.next_url,
    'https://www.ebay.com/sch/i.html?_nkw=thinkpad&_ipg=60&_pgn=2',
  )
})

test('nextPageUrl falls back to the result count without a next anchor', () => {
  const dom = new JSDOM(
    '<html><body><div class="srp-controls__count-heading">1,234 results</div></body></html>',
    {
      url: 'https://www.ebay.com/sch/i.html?_nkw=x&_ipg=60&_pgn=2',
    },
  )
  const next = ebay.nextPageUrl(dom.window.document, dom.window.location.href)
  const parsed = new URL(next)
  assert.equal(parsed.searchParams.get('_pgn'), '3')
  assert.equal(parsed.searchParams.get('_nkw'), 'x')
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

test('parseDetail extracts condition, seller, shipping and availability', () => {
  const dom = loadDom(
    'ebay-detail-rich.html',
    'https://www.ebay.com/itm/111222333444',
  )
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)

  assert.equal(result.status, 'ok')
  assert.match(result.title, /ThinkPad T14/)
  assert.equal(result.price, 620)
  assert.equal(result.price_max, 700)
  assert.equal(result.price_is_range, true)
  assert.equal(result.currency, 'USD')
  assert.equal(result.attributes['RAM Size'], '32 GB')
  assert.equal(result.condition, 'Pre-Owned')
  assert.deepEqual(result.seller, {
    name: 'refurb-dealer',
    feedback_percent: 99.2,
  })
  assert.equal(result.shipping, 'Free International Shipping')
  assert.equal(result.shipping_cost, 0)
  assert.match(result.returns, /30 day returns/)
  assert.equal(result.location, 'Shenzhen, China')
  assert.equal(result.quantity_available, 10)
  assert.equal(result.sold, 25)
  assert.equal(result.watchers, 8)
  assert.equal(result.availability, 'available')
  assert.equal(result.listing_type, 'buy_it_now')
  assert.equal(result.best_offer, true)
  assert.equal(result.has_variations, true)
  assert.deepEqual(result.variations, ['RAM', 'Storage'])
  assert.deepEqual(result.highlights, ['14-inch display', '32GB RAM'])
  assert.deepEqual(result.images, [
    'https://i.ebayimg.com/images/g/abc/s-l1600.jpg',
    'https://i.ebayimg.com/images/g/def/s-l1600.jpg',
  ])
  assert.equal(result.item_number, '111222333444')
})

test('parseDetail marks an ended listing unavailable', () => {
  const dom = loadDom(
    'ebay-detail-ended.html',
    'https://www.ebay.com/itm/123',
  )
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.match(result.title, /Dell Latitude/)
  assert.equal(result.attributes.Brand, 'Dell')
  assert.equal(result.availability, 'unavailable')
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
