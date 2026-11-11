const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const VIEW_JS = path.join(__dirname, '..', 'panel', 'session-view.js')

async function load() {
  return import(`${pathToFileURL(VIEW_JS).href}?t=${Date.now()}`)
}

test('stale panel responses are ignored by generation', async () => {
  const view = await load()
  assert.equal(view.isCurrentGeneration(1, 2), false)
  assert.equal(view.isCurrentGeneration(2, 2), true)
})

test('resume is only shown for an interrupted session without pending questions', async () => {
  const view = await load()
  assert.equal(
    view.shouldShowResume({ status: 'interrupted', pending_questions: [] }),
    true,
  )
  assert.equal(
    view.shouldShowResume({
      status: 'interrupted',
      pending_questions: [{ id: 'budget' }],
    }),
    false,
  )
  assert.equal(
    view.shouldShowResume({ status: 'blocked', pending_questions: [] }),
    true,
  )
  assert.equal(view.shouldShowResume({ status: 'done' }), false)
})

test('progress text includes product count and current stage', async () => {
  const view = await load()
  assert.match(
    view.progressText({
      products: [1, 2],
      execution_logs: [{ stage: 'llm.tool.search' }],
    }),
    /商品 2.*llm\.tool\.search/,
  )
  assert.match(
    view.progressText(
      { products: [1] },
      [{ event: { stage: 'tool.end', message: 'scrape ok' } }],
    ),
    /商品 1.*tool\.end.*scrape ok/,
  )
})

test('clarify answers are formatted as ordinary message content', async () => {
  const view = await load()
  assert.equal(
    view.formatAnswersAsMessage({
      budget: '5000',
      category: '笔记本',
      empty: '',
    }),
    'budget: 5000\ncategory: 笔记本',
  )
})

test('session settings are rendered as one site and one currency', async () => {
  const view = await load()
  assert.equal(
    view.formatSessionSettings({ site: 'ebay', currency: 'USD' }),
    '站点: eBay · 货币: USD',
  )
  assert.equal(
    view.formatSessionSettings({ site: 'jd' }),
    '站点: 京东 · 货币: 待检测',
  )
})

test('currency mismatch is extracted from a tool result log', async () => {
  const view = await load()
  assert.deepEqual(
    view.findCurrencyMismatch({
      status: 'interrupted',
      settings: { currency: 'CNY' },
      execution_logs: [
        {
          stage: 'llm.tool.search',
          data: {
            result: {
              status: 'blocked',
              error: 'currency_mismatch',
              page_currency: 'USD',
              session_currency: 'CNY',
            },
          },
        },
      ],
    }),
    { pageCurrency: 'USD', sessionCurrency: 'CNY' },
  )
  assert.equal(view.findCurrencyMismatch({ execution_logs: [] }), null)
})

test('a newer successful tool result clears an old currency mismatch', async () => {
  const view = await load()
  assert.equal(
    view.findCurrencyMismatch({
      status: 'interrupted',
      settings: { currency: 'CNY' },
      execution_logs: [
        {
          data: {
            result: {
              status: 'blocked',
              error: 'currency_mismatch',
              page_currency: 'USD',
            },
          },
        },
        { data: { result: { status: 'ok' } } },
      ],
    }),
    null,
  )
})

test('currency mismatch is ignored after current settings match the page', async () => {
  const view = await load()
  const execution_logs = [
    {
      data: {
        result: {
          status: 'blocked',
          error: 'currency_mismatch',
          page_currency: 'USD',
          session_currency: 'CNY',
        },
      },
    },
  ]

  assert.equal(
    view.findCurrencyMismatch({
      status: 'interrupted',
      settings: { currency: 'USD' },
      execution_logs,
    }),
    null,
  )
  assert.equal(
    view.findCurrencyMismatch({
      status: 'interrupted',
      settings: { currency: 'EUR' },
      execution_logs,
    }),
    null,
  )
  assert.equal(
    view.findCurrencyMismatch({
      status: 'done',
      settings: { currency: 'CNY' },
      execution_logs,
    }),
    null,
  )
})
