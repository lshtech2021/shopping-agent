;(function (root, factory) {
  const registry =
    typeof module === 'object' && module.exports
      ? require('../adapters/registry.js')
      : root.__saAdapters
  const api = factory(registry)
  if (typeof module === 'object' && module.exports) module.exports = api
  root.__saBrowseGuards = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function (registry) {
  function normalizeCurrency(value) {
    const currency = String(value || '').trim().toUpperCase()
    return currency || null
  }

  function assertBrowseAllowed({
    sessionSite,
    sessionCurrency,
    tabUrl,
    pageCurrency,
  }) {
    let adapter
    try {
      adapter = registry.getAdapter(sessionSite)
    } catch (error) {
      return {
        ok: false,
        error: error.code || 'adapter_missing',
        session_site: sessionSite || null,
      }
    }

    if (!adapter.matchUrl(tabUrl || '')) {
      return {
        ok: false,
        error: 'site_mismatch',
        session_site: adapter.id,
        tab_url: tabUrl || null,
      }
    }

    const expected = normalizeCurrency(sessionCurrency)
    const detected = normalizeCurrency(pageCurrency)
    if (!expected) {
      return {
        ok: false,
        error: 'currency_mismatch',
        page_currency: detected,
        session_currency: null,
      }
    }
    if (!detected) {
      return {
        ok: false,
        error: 'currency_unknown',
        page_currency: null,
        session_currency: expected,
      }
    }
    if (expected !== detected) {
      return {
        ok: false,
        error: 'currency_mismatch',
        page_currency: detected,
        session_currency: expected,
      }
    }
    return { ok: true }
  }

  return { assertBrowseAllowed }
})
