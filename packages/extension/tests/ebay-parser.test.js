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
  assert.equal(first.shipping_cost, 0)
  assert.equal(first.shipping_currency, null)
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

test('parseListing reads repeated SRP attribute rows', () => {
  const dom = loadDom(
    'ebay-list-srp-card.html',
    'https://www.ebay.com/sch/i.html?_nkw=laptop',
  )
  const result = ebay.parseListing(dom.window.document, dom.window.location.href)

  assert.equal(result.status, 'ok')
  assert.equal(result.items.length, 1)
  const item = result.items[0]
  assert.equal(item.source_product_id, '277676672443')
  assert.equal(
    item.title,
    'Dell Latitude 7420 // Intel Core i7-1185G7, 16GB RAM, 512GB SSD, LTE',
  )
  assert.equal(item.url, 'https://www.ebay.com/itm/277676672443')
  assert.equal(
    item.image_url,
    'https://i.ebayimg.com/images/g/uG0AAeSwxYtpgcgw/s-l1600.webp',
  )
  assert.equal(item.price, 2228.97)
  assert.equal(item.currency, 'CNY')
  assert.equal(item.condition, 'Pre-Owned')
  assert.equal(item.shipping, 'RMB 633.65 shipping')
  assert.equal(item.shipping_cost, 633.65)
  assert.equal(item.shipping_currency, 'CNY')
  assert.equal(item.listing_type, 'buy_it_now')
  assert.equal(item.watchers, 45)
  assert.equal(item.sales, null)
  assert.equal(item.location, 'Germany')
  assert.equal(item.shop_name, 'compicool')
  assert.equal(item.sponsored, true)
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
  assert.equal(result.shipping_currency, null)
  assert.equal(result.shipping_approx_cost, null)
  assert.equal(result.shipping_approx_currency, null)
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

test('brand subtitles are not treated as item condition', () => {
  const dom = new JSDOM(
    `<html><body><ul><li class="s-card">
      <a class="su-link" href="/itm/123"></a>
      <div class="s-card__title">Dell Latitude</div>
      <span class="s-card__price">$500.00</span>
      <span class="s-card__subtitle">Dell</span>
    </li></ul></body></html>`,
    { url: 'https://www.ebay.com/sch/i.html?_nkw=laptop' },
  )
  const result = ebay.parseListing(dom.window.document, dom.window.location.href)
  assert.equal(result.items[0].condition, null)
})

test('parseDetail keeps primary and approximate prices separately', () => {
  const dom = new JSDOM(
    `<html><body>
      <h1 class="x-item-title__mainTitle">ThinkPad</h1>
      <div class="x-bin-price">
        <div class="x-price-primary"><span class="ux-textspans">EUR 299.00</span></div>
        <div class="x-price-approx">
          <span>Approximately</span>
          <span class="ux-textspans ux-textspans--BOLD">RMB 2,228.97</span>
        </div>
      </div>
      <div class="vim d-shipping-minview" data-testid="d-shipping-minview">
        <div class="ux-layout-section ux-layout-section--shipping">
          <div class="ux-labels-values col-12 ux-labels-values--shipping">
            <div class="ux-labels-values__labels">
              <div><span class="ux-textspans">Shipping:</span></div>
            </div>
            <div class="ux-labels-values__values">
              <div class="ux-labels-values__values-content"><div>
                <span class="ux-textspans ux-textspans--BOLD">EUR 85.00 </span>
                <span class="ux-textspans ux-textspans--SECONDARY ux-textspans--BOLD">(approx RMB 633.65)</span>
                <span class="ux-textspans">&nbsp;</span>
                <span class="ux-textspans">DHL Paket International</span>
                <span class="ux-textspans">. </span>
              </div></div>
            </div>
          </div>
        </div>
      </div>
    </body></html>`,
    { url: 'https://www.ebay.com/itm/123' },
  )
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.price, 299)
  assert.equal(result.currency, 'EUR')
  assert.equal(result.approx_price, 2228.97)
  assert.equal(result.approx_currency, 'CNY')
  assert.equal(result.shipping_cost, 85)
  assert.equal(result.shipping_currency, 'EUR')
  assert.equal(result.shipping_approx_cost, 633.65)
  assert.equal(result.shipping_approx_currency, 'CNY')
})

test('parseDetail splits both shipping amounts inside one span', () => {
  const dom = new JSDOM(
    `<html><body>
      <h1 class="x-item-title__mainTitle">Latitude 5520</h1>
      <div class="x-price-primary"><span class="ux-textspans">US $290.00</span></div>
      <div class="ux-labels-values ux-labels-values--shipping">
        <div class="ux-labels-values__labels">Shipping</div>
        <div class="ux-labels-values__values">
          <div class="ux-labels-values__values-content"><div>
            <span class="ux-textspans">US $399.73 (approx. RMB 2,669.20) delivery in 1-3 days</span>
          </div></div>
        </div>
      </div>
    </body></html>`,
    { url: 'https://www.ebay.com/itm/123' },
  )
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.shipping_cost, 399.73)
  assert.equal(result.shipping_currency, 'USD')
  assert.equal(result.shipping_approx_cost, 2669.2)
  assert.equal(result.shipping_approx_currency, 'CNY')
})

test('parseDetail splits a plain-text shipping row with an approximate price', () => {
  const dom = new JSDOM(
    `<html><body>
      <h1 class="x-item-title__mainTitle">ThinkPad</h1>
      <div class="x-price-primary"><span class="ux-textspans">EUR 299.00</span></div>
      <div class="ux-labels-values ux-labels-values--shipping">
        <div class="ux-labels-values__labels">Shipping</div>
        <div class="ux-labels-values__values">
          <div class="ux-labels-values__values-content">
            EUR 85.00 (approx RMB 633.65) DHL Paket International
          </div>
        </div>
      </div>
    </body></html>`,
    { url: 'https://www.ebay.com/itm/123' },
  )
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.shipping_cost, 85)
  assert.equal(result.shipping_currency, 'EUR')
  assert.equal(result.shipping_approx_cost, 633.65)
  assert.equal(result.shipping_approx_currency, 'CNY')
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

test('parseDetail ignores a brand-like condition element', () => {
  const dom = new JSDOM(
    `<html><body>
      <h1 class="x-item-title__mainTitle">Dell Latitude</h1>
      <div class="x-price-primary">US $500.00</div>
      <div class="x-item-condition"><span class="ux-textspans">Dell</span></div>
    </body></html>`,
    { url: 'https://www.ebay.com/itm/123' },
  )
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.equal(result.condition, null)
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
      1000,
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

test('waitForListing waits for the card grid to stop growing', async () => {
  const dom = new JSDOM(
    `<html><body><ul id="list">
      <li class="s-item">
        <a class="s-item__link" href="/itm/1001"></a>
        <div class="s-item__title">Laptop one</div>
        <span class="s-item__price">$500.00</span>
      </li>
    </ul></body></html>`,
    { url: 'https://www.ebay.com/sch/i.html?_nkw=laptop' },
  )
  setTimeout(() => {
    const list = dom.window.document.querySelector('#list')
    const item = dom.window.document.createElement('li')
    item.className = 's-item'
    item.innerHTML = `
      <a class="s-item__link" href="/itm/1002"></a>
      <div class="s-item__title">Laptop two</div>
      <span class="s-item__price">$550.00</span>
    `
    list.appendChild(item)
  }, 250)

  assert.equal(
    await ebay.waitForListing(
      dom.window.document,
      dom.window.location.href,
      1500,
    ),
    true,
  )
  const result = ebay.parseListing(
    dom.window.document,
    dom.window.location.href,
  )
  assert.equal(result.items.length, 2)
})

test('zero-result fallback cards are not ingested', async () => {
  const dom = loadDom(
    'ebay-list-zero-results.html',
    'https://www.ebay.com/sch/i.html?_nkw=laptop+for+software+development',
  )
  assert.equal(
    await ebay.waitForListing(
      dom.window.document,
      dom.window.location.href,
      1500,
    ),
    true,
  )
  const result = ebay.parseListing(
    dom.window.document,
    dom.window.location.href,
  )
  assert.equal(result.status, 'ok')
  assert.deepEqual(result.items, [])
  assert.equal(result.result_count, 0)
  assert.equal(result.page_item_count, 0)
  assert.equal(result.fallback_item_count, 1)
  assert.equal(result.no_results, true)
  assert.equal(result.has_next, false)
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
