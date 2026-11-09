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

  function parseRating(text) {
    if (!text) return null
    const match = String(text).match(/(\d+(?:\.\d+)?)/)
    if (!match) return null
    const value = Number(match[1])
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

  function parseCurrency(text, fallback = 'USD') {
    const value = String(text || '').toUpperCase()
    if (/RMB|CNY|人民币/.test(value)) return 'CNY'
    if (/USD|US\s*\$/.test(value)) return 'USD'
    if (/(?:^|\s)(?:C|CA)\s*\$/.test(value)) return 'CAD'
    if (/(?:^|\s)AU\s*\$/.test(value)) return 'AUD'
    if (/(?:^|\s)HK\s*\$/.test(value)) return 'HKD'
    if (/(?:^|\s)SG\s*\$/.test(value)) return 'SGD'
    if (/(?:^|\s)NZ\s*\$/.test(value)) return 'NZD'
    if (/EUR|€/.test(value)) return 'EUR'
    if (/GBP|£/.test(value)) return 'GBP'
    if (/\$/.test(value)) return fallback
    return fallback
  }

  function parsePriceRange(text) {
    if (!text) return null
    const value = String(text)
    if (!/\d\s*(?:-|–|—|to|~)\s*[$€£¥]?\s*\d/i.test(value)) {
      return null
    }
    const numbers = [...value.matchAll(/\d[\d,]*(?:\.\d+)?/g)]
    if (numbers.length < 2) return null
    const min = Number(numbers[0][0].replace(/,/g, ''))
    const max = Number(numbers[1][0].replace(/,/g, ''))
    if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) {
      return null
    }
    return { min, max }
  }

  return {
    parsePrice,
    parseRating,
    parseSales,
    parseCurrency,
    parsePriceRange,
  }
})
