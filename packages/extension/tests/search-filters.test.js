const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const BROWSER_JOBS_JS = path.join(
  __dirname,
  '..',
  'background',
  'browser-jobs.js',
)

async function load() {
  return import(`${pathToFileURL(BROWSER_JOBS_JS).href}?t=${Date.now()}`)
}

test('buildSearchUrl applies eBay category, price, exclusions and sort', async () => {
  const { buildSearchUrl } = await load()
  assert.equal(
    buildSearchUrl('ebay', 'laptop', {
      categoryId: '177',
      maxPrice: 694.44,
      excludeTerms: ['-book', '-books'],
      sort: 'price_asc',
    }),
    'https://www.ebay.com/sch/i.html?_nkw=laptop%20-book%20-books&_sacat=177&_udhi=694.44&_sop=15&_ipg=60',
  )
})

test('buildSearchUrl keeps the plain eBay URL without filters', async () => {
  const { buildSearchUrl } = await load()
  assert.equal(
    buildSearchUrl('ebay', 'laptop computer'),
    'https://www.ebay.com/sch/i.html?_nkw=laptop%20computer&_ipg=60',
  )
})
