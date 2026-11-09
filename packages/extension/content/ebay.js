;(function (root, factory) {
  const parseUtils =
    typeof module === 'object' && module.exports
      ? require('./parse-utils.js')
      : root.__saParse
  const api = factory(parseUtils)
  if (typeof module === 'object' && module.exports) module.exports = api
  else root.__saEbay = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function (parseUtils) {
  const EBAY_ORIGIN = 'https://www.ebay.com'
  const ITEM_SELECTORS = 'li.s-item, li.s-card'

  function clean(value) {
    return String(value || '')
      .replace(/\s+/g, ' ')
      .trim()
  }

  function text(node, selectors) {
    for (const selector of selectors) {
      const element = node.querySelector(selector)
      if (!element) continue
      const value = clean(element.textContent)
      if (value) return value
    }
    return null
  }

  function categoryOf(root) {
    const links = [...root.querySelectorAll('.breadcrumbs a, .se-crumb a')]
    if (!links.length) return null
    return clean(links[links.length - 1].textContent) || null
  }

  function extractItemId(value) {
    if (!value) return null
    const byPath = String(value).match(/\/itm\/(\d+)/)
    if (byPath) return byPath[1]
    const byQuery = String(value).match(/[?&]item=(\d+)/)
    return byQuery ? byQuery[1] : null
  }

  function detectBlock(root = document, url = location.href) {
    try {
      const parsed = new URL(url)
      if (
        parsed.hostname.includes('signin.ebay.com') ||
        parsed.pathname.startsWith('/signin')
      ) {
        return 'login_required'
      }
      if (
        parsed.pathname.includes('/splashui/challenge') ||
        parsed.pathname.includes('/challenge')
      ) {
        return 'captcha'
      }
    } catch (_) {
      /* ignore */
    }
    const hasListing = Boolean(root.querySelector(ITEM_SELECTORS))
    const hasDetail = Boolean(
      root.querySelector(
        'h1.x-item-title__mainTitle, .x-item-title__mainTitle, .x-price-primary',
      ),
    )
    if (hasListing || hasDetail) return null

    const bodyText = root.body?.innerText || root.body?.textContent || ''
    if (!bodyText.trim()) return 'rate_limited'
    if (/pardon our interruption|verify you are a human|security measure/i.test(bodyText)) {
      return 'captcha'
    }
    return null
  }

  function parseListing(root = document, baseUrl = location.href) {
    const blocked = detectBlock(root, baseUrl)
    if (blocked) return { status: blocked, items: [] }

    const category = categoryOf(root)
    const items = []
    for (const node of root.querySelectorAll(ITEM_SELECTORS)) {
      const link =
        node.querySelector('a.s-item__link[href*="/itm/"]') ||
        node.querySelector('a.su-link[href*="/itm/"]') ||
        node.querySelector('a[href*="/itm/"]')
      const href = link?.getAttribute('href') || ''
      const sourceProductId = extractItemId(href)
      const title = clean(
        text(node, ['.s-item__title', '.s-card__title']),
      ).replace(/\s*Opens in a new window.*$/i, '')
      if (!sourceProductId || !title || /^shop on ebay$/i.test(title)) continue
      const priceText = text(node, ['.s-item__price', '.s-card__price'])
      const priceRange = parseUtils.parsePriceRange(priceText)
      const shopName = text(node, ['.s-item__seller-info-text'])
      const ratingText = text(node, [
        '.x-star-rating',
        '.s-item__reviews',
        '[class*="star-rating"]',
      ])
      const salesText = text(node, [
        '.s-item__hotness',
        '.s-item__additionalItemHotness',
      ])
      const shipping = text(node, [
        '.s-item__shipping',
        '.s-item__logisticsCost',
      ])
      const condition = text(node, ['.s-item__subtitle', '.SECONDARY_INFO'])
      items.push({
        source_product_id: String(sourceProductId),
        url: `${EBAY_ORIGIN}/itm/${sourceProductId}`,
        title,
        price: priceRange ? priceRange.min : parseUtils.parsePrice(priceText),
        price_max: priceRange ? priceRange.max : null,
        price_is_range: Boolean(priceRange),
        currency: parseUtils.parseCurrency(priceText, 'USD'),
        shop_name: shopName,
        sales: parseUtils.parseSales(salesText),
        rating: parseUtils.parseRating(ratingText),
        sponsored: Boolean(
          node.querySelector('.s-item__etrs-text, .s-item__ad-badge'),
        ),
        shipping,
        condition,
        badges: condition ? [condition] : [],
        category,
      })
    }
    const bodyText = root.body?.innerText || root.body?.textContent || ''
    if (
      !items.length &&
      /no exact matches found|0 results|did not match any|no results found/i.test(
        bodyText,
      )
    ) {
      return {
        status: 'ok',
        items: [],
        page: currentPageNumber(baseUrl),
        result_count: 0,
        has_next: false,
        no_results: true,
      }
    }
    return {
      status: 'ok',
      items,
      page: currentPageNumber(baseUrl),
      result_count: items.length,
      has_next: hasNextPage(root, baseUrl),
    }
  }

  function parseDetail(root = document, url = location.href) {
    const blocked = detectBlock(root, url)
    if (blocked) return { status: blocked }

    const title = text(root, [
      'h1.x-item-title__mainTitle',
      '.x-item-title__mainTitle',
    ])
    const priceText = text(root, [
      '.x-price-primary',
      '[data-testid="x-price-primary"]',
    ])
    const attributes = {}
    for (const row of root.querySelectorAll('.ux-labels-values')) {
      const label = clean(
        row.querySelector('.ux-labels-values__labels')?.textContent,
      ).replace(/[：:]\s*$/, '')
      const value = clean(
        row.querySelector('.ux-labels-values__values')?.textContent,
      )
      if (label && value) attributes[label] = value
    }
    if (!title && !Object.keys(attributes).length) {
      return { status: 'parse_error', error: 'detail_not_found' }
    }
    return {
      status: 'ok',
      title,
      price: parseUtils.parsePrice(priceText),
      currency: parseUtils.parseCurrency(priceText, 'USD'),
      attributes,
    }
  }

  function nextPageUrl(root = document, url = location.href) {
    const link = root.querySelector('a.pagination__next[href]')
    const href = link?.getAttribute('href')
    if (!href || href.startsWith('javascript:')) return null
    try {
      return new URL(href, EBAY_ORIGIN).toString()
    } catch (_) {
      return null
    }
  }

  function hasNextPage(root = document, url = location.href) {
    return Boolean(nextPageUrl(root, url))
  }

  function currentPageNumber(url = location.href) {
    try {
      const value = Number(new URL(url).searchParams.get('_pgn'))
      return Number.isFinite(value) && value > 0 ? Math.trunc(value) : 1
    } catch (_) {
      return 1
    }
  }

  async function clickNextPage() {
    // eBay pagination is URL-based; background should navigate nextUrl.
    return false
  }

  function buildSearchUrl(query) {
    return `${EBAY_ORIGIN}/sch/i.html?_nkw=${encodeURIComponent(query || '')}&_ipg=60`
  }

  return {
    site: 'ebay',
    detectBlock,
    parseListing,
    parseDetail,
    hasNextPage,
    nextPageUrl,
    clickNextPage,
    currentPageNumber,
    buildSearchUrl,
  }
})
