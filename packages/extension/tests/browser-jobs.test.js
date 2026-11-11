const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { JSDOM } = require('jsdom')

const BROWSER_JOBS_JS = path.join(
  __dirname,
  '..',
  'background',
  'browser-jobs.js',
)

async function loadBrowserJobs() {
  return import(`${require('node:url').pathToFileURL(BROWSER_JOBS_JS).href}?t=${Date.now()}`)
}

function installChrome({
  getTab,
  sendMessage,
  createTab,
  sessionData = {},
} = {}) {
  const calls = { sendMessage: 0, click: 0 }
  globalThis.chrome = {
    tabs: {
      async get(tabId) {
        return getTab ? getTab(tabId) : { id: tabId, url: '', status: 'complete' }
      },
      async create({ url }) {
        return createTab ? createTab(url) : { id: 1, url }
      },
      async update(tabId, patch) {
        return { id: tabId, ...patch }
      },
      async sendMessage(tabId, payload) {
        calls.sendMessage += 1
        if (payload.type === 'click_next_page') calls.click += 1
        return sendMessage(tabId, payload)
      },
    },
    storage: {
      local: { async get(defaults) { return defaults } },
      session: {
        async get() { return sessionData },
        async set() {},
      },
    },
  }
  return calls
}

test('parallel fetch_detail opens a distinct tab per product', async () => {
  const created = []
  installChrome({
    getTab: (tabId) => {
      const hit = created.find((tab) => tab.id === tabId)
      return hit
        ? { id: hit.id, url: hit.url, status: 'complete' }
        : { id: tabId, url: '', status: 'complete' }
    },
    createTab: (url) => {
      const tab = { id: created.length + 10, url }
      created.push(tab)
      return tab
    },
    sendMessage: (_tabId, payload) => {
      if (payload.type !== 'parse_detail') return { ok: true }
      return {
        ok: true,
        document_url: created.find((tab) => tab.id === _tabId)?.url,
        result: { status: 'ok', title: 'ok', price: 1, attributes: {} },
      }
    },
  })
  const { runBrowserJob } = await loadBrowserJobs()

  await Promise.all([
    runBrowserJob('fetch_detail', {
      site: 'jd',
      run_id: 'run-1',
      source_product_id: '111',
      url: 'https://item.jd.com/111.html',
    }),
    runBrowserJob('fetch_detail', {
      site: 'jd',
      run_id: 'run-1',
      source_product_id: '222',
      url: 'https://item.jd.com/222.html',
    }),
  ])

  assert.equal(created.length, 2)
  assert.deepEqual(
    created.map((tab) => tab.url).sort(),
    ['https://item.jd.com/111.html', 'https://item.jd.com/222.html'],
  )
})

test('fetch_detail rejects a stale document from the reused tab', async () => {
  const oldUrl = 'https://item.jd.com/OLD.html'
  installChrome({
    getTab: () => ({ id: 1, url: 'https://item.jd.com/NEW.html', status: 'complete' }),
    sendMessage: () => ({
      ok: true,
      document_url: oldUrl,
      result: { status: 'ok', title: '旧商品', price: 1, attributes: {} },
    }),
  })
  const { runBrowserJob } = await loadBrowserJobs()

  const result = await runBrowserJob('fetch_detail', {
    site: 'jd',
    source_product_id: 'NEW',
    url: 'https://item.jd.com/NEW.html',
  })

  assert.equal(result.status, 'parse_error')
  assert.equal(result.error, 'stale_document')
})

test('click_next_page is not retried after a navigation closes the port', async () => {
  let clickCalls = 0
  installChrome({
    getTab: () => ({ id: 1, url: 'https://search.jd.com/Search?keyword=x', status: 'complete' }),
    sendMessage: (_tabId, payload) => {
      if (payload.type === 'detect_block') return { ok: true, block: null }
      if (payload.type === 'has_next_page') {
        return { ok: true, hasNext: true, nextUrl: null }
      }
      if (payload.type === 'click_next_page') {
        clickCalls += 1
        throw new Error('message port closed')
      }
      return { ok: true, document_url: 'https://search.jd.com/Search?keyword=x' }
    },
  })
  const { runBrowserJob } = await loadBrowserJobs()
  await runBrowserJob('search', { site: 'jd', query: 'x' })

  const result = await runBrowserJob('next_page', { site: 'jd' })

  assert.equal(result.status, 'blocked')
  assert.equal(result.error, 'click_failed')
  assert.equal(clickCalls, 1)
})

test('next_page does not accept a stale page when only the page parameter changes', async () => {
  const pageOne = 'https://search.jd.com/Search?keyword=x&page=1'
  installChrome({
    getTab: () => ({ id: 1, url: pageOne, status: 'complete' }),
    sendMessage: (_tabId, payload) => {
      if (payload.type === 'detect_block') {
        return { ok: true, block: null, document_url: pageOne }
      }
      if (payload.type === 'has_next_page') {
        return {
          ok: true,
          hasNext: true,
          nextUrl: 'https://search.jd.com/Search?keyword=x&page=2',
        }
      }
      return {
        ok: true,
        document_url: pageOne,
        result: {
          status: 'ok',
          items: [{ source_product_id: '1', title: '商品', price: 10 }],
          result_count: 1,
        },
      }
    },
  })
  const { runBrowserJob } = await loadBrowserJobs()
  await runBrowserJob('search', { site: 'jd', query: 'x' })

  const result = await runBrowserJob('next_page', {
    site: 'jd',
    job_timeout_s: 0.5,
  })

  assert.equal(result.status, 'blocked')
  assert.equal(result.error, 'tab_load_timeout')
})

test('restored tab from the wrong host is discarded', async () => {
  installChrome({
    getTab: () => ({ id: 7, url: 'https://evil.example/item/1', status: 'complete' }),
    sendMessage: () => ({ ok: true }),
    sessionData: { siteTabs: { jd: 7 } },
  })
  const { runBrowserJob } = await loadBrowserJobs()

  const result = await runBrowserJob('scrape_listing', { site: 'jd' })

  assert.equal(result.status, 'blocked')
  assert.equal(result.error, 'no_active_search_tab')
})

test('zero-item listing is a parse error, not a successful empty page', async () => {
  installChrome({
    getTab: () => ({ id: 1, url: 'https://search.jd.com/Search?keyword=x', status: 'complete' }),
    sendMessage: (_tabId, payload) => {
      if (payload.type === 'detect_block') return { ok: true, block: null }
      return {
        ok: true,
        document_url: 'https://search.jd.com/Search?keyword=x',
        result: { status: 'ok', items: [], result_count: 0 },
      }
    },
  })
  const { runBrowserJob } = await loadBrowserJobs()
  await runBrowserJob('search', { site: 'jd', query: 'x' })

  const result = await runBrowserJob('scrape_listing', { site: 'jd', page: 1 })

  assert.equal(result.status, 'parse_error')
  assert.equal(result.error, 'empty_listing')
})

test('scrape_listing retries a transient listing_not_ready result', async () => {
  const url = 'https://www.ebay.com/sch/i.html?_nkw=laptop'
  let parseCalls = 0
  const calls = installChrome({
    getTab: () => ({ id: 1, url, status: 'complete' }),
    sendMessage: (_tabId, payload) => {
      if (payload.type === 'detect_block') {
        return { ok: true, block: null, document_url: url }
      }
      if (payload.type !== 'parse_listing') return { ok: true }
      parseCalls += 1
      if (parseCalls < 3) {
        return {
          ok: true,
          document_url: url,
          result: {
            status: 'parse_error',
            error: 'listing_not_ready',
            items: [],
          },
        }
      }
      return {
        ok: true,
        document_url: url,
        result: {
          status: 'ok',
          items: [
            { source_product_id: '1', title: 'Laptop', price: 620 },
          ],
          result_count: 1,
          page: 1,
        },
      }
    },
  })
  const { runBrowserJob } = await loadBrowserJobs()
  await runBrowserJob('search', { site: 'ebay', query: 'laptop' })

  const result = await runBrowserJob('scrape_listing', { site: 'ebay' })

  assert.equal(result.status, 'ok')
  assert.equal(result.items.length, 1)
  assert.equal(parseCalls, 3)
  assert.equal(calls.sendMessage, 4)
})

test('job results carry job_id, document_url and page', async () => {
  const documentUrl = 'https://search.jd.com/Search?keyword=x'
  installChrome({
    getTab: () => ({ id: 1, url: documentUrl, status: 'complete' }),
    sendMessage: (_tabId, payload) => {
      if (payload.type === 'detect_block') {
        return { ok: true, block: null, document_url: documentUrl }
      }
      return {
        ok: true,
        document_url: documentUrl,
        result: {
          status: 'ok',
          items: [{ source_product_id: '1', title: '商品', price: 10 }],
          result_count: 1,
        },
      }
    },
  })
  const { runBrowserJob } = await loadBrowserJobs()
  await runBrowserJob('search', {
    site: 'jd',
    query: 'x',
    job_id: 'job-search',
  })

  const result = await runBrowserJob('scrape_listing', {
    site: 'jd',
    page: 2,
    job_id: 'job-scrape',
  })

  assert.equal(result.job_id, 'job-scrape')
  assert.equal(result.document_url, documentUrl)
  assert.equal(result.page, 2)
})

test('currency and price ranges are parsed explicitly', () => {
  const parseUtils = require('../content/parse-utils.js')
  assert.equal(parseUtils.parseCurrency('C $620.00', 'USD'), 'CAD')
  assert.deepEqual(parseUtils.parsePriceRange('$620.00 to $850.00'), {
    min: 620,
    max: 850,
  })
})

test('block detection ignores product-description words when title/price exist', () => {
  const jd = require('../content/jd.js')
  const html = `
    <html><body>
      <div class="sku-name">安全摄像头</div>
      <div class="summary-price"><span class="price">¥199</span></div>
      <div class="p-parameter">商品描述里提到验证码，但这不是验证页。</div>
    </body></html>
  `
  const dom = new JSDOM(html, { url: 'https://item.jd.com/1001.html' })

  assert.equal(
    jd.detectBlock(dom.window.document, dom.window.location.href),
    null,
  )
})
