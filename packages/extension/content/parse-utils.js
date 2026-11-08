;(function (root, factory) {
  const api = factory()
  if (typeof module === 'object' && module.exports) module.exports = api
  else root.__saParse = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function parsePrice(text) {
    if (!text) return null
    const match = String(text).match(/\d[\d,]*(?:\.\d+)?/)
    if (!match) return null
    const value = Number(match[0].replace(/,/g, ''))
    return Number.isFinite(value) ? value : null
  }

  function parseSales(text) {
    if (!text) return null
    const value = String(text)
    const wan = value.match(/([\d.]+)\s*万/)
    if (wan) return Math.round(Number(wan[1]) * 10000)
    const plain = value.match(/(\d[\d,]*)/)
    if (!plain) return null
    const parsed = Number(plain[1].replace(/,/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }

  return { parsePrice, parseSales }
})
