const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { JSDOM } = require('jsdom')

function loadPanel() {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'panel', 'sidepanel.html'),
    'utf8',
  )
  return new JSDOM(html).window.document
}

test('new session view exposes a currency selector', () => {
  const document = loadPanel()
  const select = document.querySelector('#newSession #newCurrencySelect')
  assert.ok(select)
  assert.equal(select.value, 'CNY')
  assert.deepEqual(
    [...select.options].map((option) => option.value),
    ['', 'CNY', 'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'HKD', 'SGD', 'NZD'],
  )
})

test('chat and new-session currency selectors stay in sync', () => {
  const document = loadPanel()
  const newSession = document.querySelector('#newCurrencySelect')
  const chat = document.querySelector('#chat #currencySelect')
  assert.ok(newSession)
  assert.ok(chat)
  assert.deepEqual(
    [...newSession.options].map((option) => option.value),
    [...chat.options].map((option) => option.value),
  )
})
