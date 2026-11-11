;(function (root, factory) {
  const jd =
    typeof module === 'object' && module.exports
      ? require('./jd/index.js')
      : root.__saJd
  const ebay =
    typeof module === 'object' && module.exports
      ? require('./ebay/index.js')
      : root.__saEbay
  const api = factory([jd, ebay])
  if (typeof module === 'object' && module.exports) module.exports = api
  root.__saAdapters = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function (packs) {
  const adapters = new Map(
    packs.filter(Boolean).map((adapter) => [adapter.id, adapter]),
  )

  function getAdapter(site) {
    const id = String(site || '').toLowerCase()
    const adapter = adapters.get(id)
    if (!adapter) {
      const error = new Error(`adapter_missing: ${id || '(empty)'}`)
      error.code = 'adapter_missing'
      throw error
    }
    return adapter
  }

  function getAdapterByUrl(url) {
    return [...adapters.values()].find((adapter) => adapter.matchUrl(url)) || null
  }

  return { getAdapter, getAdapterByUrl }
})
