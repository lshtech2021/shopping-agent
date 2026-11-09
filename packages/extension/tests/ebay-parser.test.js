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

test('parseDetail extracts ebay title and attributes', () => {
  const dom = loadDom('ebay-detail.html', 'https://www.ebay.com/itm/2001')
  const result = ebay.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.match(result.title, /ThinkPad/)
  assert.equal(result.attributes['RAM Size'], '32 GB')
})
