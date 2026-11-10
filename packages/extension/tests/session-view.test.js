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
