const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { JSDOM } = require('jsdom')

const parseUtils = require('../content/parse-utils.js')
const jd = require('../content/jd.js')

function loadDom(name) {
  const html = fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8')
  return new JSDOM(html, { url: 'https://search.jd.com/Search?keyword=notebook' })
}

test('parseListing extracts jd cards', () => {
  const dom = loadDom('jd-list.html')
  const result = jd.parseListing(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.equal(result.items.length, 2)
  assert.equal(result.items[0].source_product_id, '1001')
  assert.equal(result.items[0].price, 4899)
  assert.match(result.items[0].title, /ThinkPad/)
  assert.ok(jd.nextPageUrl(dom.window.document, dom.window.location.href))
})

test('parseDetail extracts title price attributes', () => {
  const html = fs.readFileSync(
    path.join(__dirname, 'fixtures', 'jd-detail.html'),
    'utf8',
  )
  const dom = new JSDOM(html, { url: 'https://item.jd.com/1001.html' })
  const result = jd.parseDetail(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'ok')
  assert.match(result.title, /ThinkPad/)
  assert.equal(result.price, 4899)
  assert.equal(result.attributes['内存'], '32GB')
})

test('blank page is rate_limited', () => {
  const dom = loadDom('jd-blank.html')
  const result = jd.parseListing(dom.window.document, dom.window.location.href)
  assert.equal(result.status, 'rate_limited')
})

test('parsePrice helper', () => {
  assert.equal(parseUtils.parsePrice('¥4,899.00'), 4899)
})
