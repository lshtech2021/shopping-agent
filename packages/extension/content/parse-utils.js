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

  function parseCount(text) {
    if (!text) return null
    const match = String(text).match(/([\d][\d,.]*)\s*([KkMm])?/)
    if (!match) return null
    const base = Number(match[1].replace(/,/g, ''))
    if (!Number.isFinite(base)) return null
    const suffix = (match[2] || '').toLowerCase()
    const multiplier = suffix === 'k' ? 1000 : suffix === 'm' ? 1000000 : 1
    return Math.round(base * multiplier)
  }

  /**
   * Parse a star rating only. Review counts such as "1,234 product ratings"
   * must return null; eBay cards expose both values close to each other.
   */
  function parseRating(text) {
    if (text === null || text === undefined) return null
    const value = String(text).trim()
    if (!value) return null
    const match =
      value.match(/(\d(?:\.\d+)?)\s*(?:out of\s*5|stars?|\/\s*5)/i) ||
      value.match(/^\s*(\d(?:\.\d+)?)\s*$/)
    if (!match) return null
    const rating = Number(match[1])
    return Number.isFinite(rating) && rating >= 0 && rating <= 5 ? rating : null
  }

  /**
   * Parse a sales count. Watchers are intentionally excluded: callers that
   * need eBay sales must pass {requireSold: true}.
   */
  function parseSales(text, options = {}) {
    if (!text) return null
    const value = String(text)
    const sold = value.match(/([\d.,]+\s*[KkMm]?)\+?\s*sold/i)
    if (sold) return parseCount(sold[1])
    if (options.requireSold) return null

    const wan = value.match(/([\d.]+)\s*万/)
    if (wan) return Math.round(Number(wan[1]) * 10000)
    return parseCount(value)
  }

  function parseWatchers(text) {
    if (!text) return null
    const match = String(text).match(
      /([\d.,]+\s*[KkMm]?)\+?\s*(?:watchers?|watching)/i,
    )
    return match ? parseCount(match[1]) : null
  }

  function parseCurrency(text, fallback = 'USD') {
    const value = String(text || '').toUpperCase()
    if (/RMB|CNY|人民币/.test(value)) return 'CNY'
    if (/\bUSD\b|US\s*\$/.test(value)) return 'USD'
    if (/\bCAD\b|(?:^|\s)(?:C|CA)\s*\$/.test(value)) return 'CAD'
    if (/\bAUD\b|(?:^|\s)AU\s*\$/.test(value)) return 'AUD'
    if (/\bHKD\b|(?:^|\s)HK\s*\$/.test(value)) return 'HKD'
    if (/\bSGD\b|(?:^|\s)SG\s*\$/.test(value)) return 'SGD'
    if (/\bNZD\b|(?:^|\s)NZ\s*\$/.test(value)) return 'NZD'
    if (/\bGBP\b|£/.test(value)) return 'GBP'
    if (/\bEUR\b|€/.test(value)) return 'EUR'
    // On eBay a bare ¥ is normally JPY; CNY listings show RMB/CNY.
    if (/\bJPY\b|円|¥/.test(value)) return 'JPY'
    if (/\$/.test(value)) return fallback
    return fallback
  }

  function parsePriceRange(text) {
    if (!text) return null
    const value = String(text)
    if (
      !/\d\s*(?:-|–|—|~|to)\s*(?:[A-Z]{2,3}\s*)?[$€£¥]?\s*\d/i.test(value)
    ) {
      return null
    }
    const numbers = [...value.matchAll(/\d[\d,]*(?:\.\d+)?/g)].map(
      (match) => Number(match[0].replace(/,/g, '')),
    )
    if (numbers.length < 2) return null
    const min = numbers[0]
    const max = numbers[1]
    if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) {
      return null
    }
    return { min, max }
  }

  return {
    parsePrice,
    parseRating,
    parseSales,
    parseWatchers,
    parseCurrency,
    parsePriceRange,
  }
})
