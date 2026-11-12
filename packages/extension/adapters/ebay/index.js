;(function (root, factory) {
  const parseUtils =
    typeof module === 'object' && module.exports
      ? require('../../content/parse-utils.js')
      : root.__saParse
  const api = factory(parseUtils)
  if (typeof module === 'object' && module.exports) module.exports = api
  root.__saEbay = api
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

  /**
   * Return every text matching the first selector that yields content. Card
   * layouts repeat the same row class for price, options, shipping, location
   * and signals, so reading only the first row loses most of the card.
   */
  function allTexts(node, selectors) {
    for (const selector of selectors) {
      const values = [...node.querySelectorAll(selector)]
        .map((element) => clean(element.textContent))
        .filter(Boolean)
      if (values.length) return values
    }
    return []
  }

  const DETAIL_TITLE_SELECTORS = [
    'h1.x-item-title__mainTitle',
    'h1.x-item-title',
    '.x-item-title__mainTitle',
  ]
  const DETAIL_PRICE_SELECTORS = [
    '.x-price-primary',
    '[data-testid="x-price-primary"]',
  ]
  const CONDITION_LABELS = new Set([
    'new',
    'brand new',
    'pre-owned',
    'used',
    'refurbished',
    'seller refurbished',
    'manufacturer refurbished',
    'certified refurbished',
    'open box',
    'new other',
    'for parts',
    'for parts or not working',
    'parts only',
    'not working',
    'used - good',
    'used - very good',
    'used - acceptable',
  ])

  function knownCondition(value) {
    const textValue = clean(value)
    if (!textValue) return null
    const first = clean(textValue.split('·')[0].split('|')[0])
    return CONDITION_LABELS.has(first.toLowerCase()) ? first : null
  }

  function itemLink(node) {
    return (
      node.querySelector('a.s-item__link[href*="/itm/"]') ||
      node.querySelector('a.su-link[href*="/itm/"]') ||
      node.querySelector('a[href*="/itm/"]')
    )
  }

  function hasListingCards(root) {
    return Boolean(root?.querySelector?.(ITEM_SELECTORS))
  }

  function usableCard(node) {
    const link = itemLink(node)
    const sourceProductId = extractItemId(link?.getAttribute('href'))
    const title = clean(text(node, ['.s-item__title', '.s-card__title']))
    if (!sourceProductId || !title || /^shop on ebay$/i.test(title)) {
      return false
    }
    return Boolean(text(node, ['.s-item__price', '.s-card__price']))
  }

  function countUsableCards(root) {
    return [...root.querySelectorAll(ITEM_SELECTORS)].filter(usableCard).length
  }

  function hasUsableListing(root) {
    return countUsableCards(root) > 0
  }

  function hasDetailSignal(root) {
    return Boolean(
      text(root, DETAIL_TITLE_SELECTORS) ||
        text(root, DETAIL_PRICE_SELECTORS) ||
        root.querySelector('.ux-labels-values, .x-item-condition') ||
        text(root, ['.x-item-status']),
    )
  }

  function isDetailReady(root) {
    const title = text(root, DETAIL_TITLE_SELECTORS)
    const price = text(root, DETAIL_PRICE_SELECTORS)
    const signal =
      root.querySelector('.ux-labels-values, .x-item-condition, .x-sellercard-atf') ||
      text(root, ['.x-item-status'])
    return Boolean(title && (price || signal))
  }

  function bodyTextOf(root) {
    return root.body?.innerText || root.body?.textContent || ''
  }

  function hasNoResults(root) {
    const bodyText = bodyTextOf(root)
    return /no exact matches found|no results found|did not match any|(?<![\d,])0 results/i.test(
      bodyText,
    )
  }

  function blockedByUrl(url) {
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
        parsed.pathname.includes('/challenge') ||
        parsed.pathname.includes('/captcha')
      ) {
        return 'captcha'
      }
    } catch (_) {
      /* ignore */
    }
    return null
  }

  /**
   * Classify why a page has no usable content. Keeping the status and the
   * diagnostic code separate lets callers preserve the existing risk contract
   * while still logging a precise cause.
   */
  function classifyPage(root = document, url = location.href) {
    const byUrl = blockedByUrl(url)
    if (byUrl) return { status: byUrl, error: byUrl }
    if (hasListingCards(root) || hasDetailSignal(root)) {
      return { status: null, error: null }
    }

    const bodyText = clean(bodyTextOf(root))
    if (!bodyText) return { status: 'rate_limited', error: 'empty_body' }
    if (
      /pardon our interruption|verify you are a human|security measure|checking your browser|just a moment|unusual activity|verify yourself/i.test(
        bodyText,
      )
    ) {
      return { status: 'captcha', error: 'challenge' }
    }
    if (/access denied|request blocked|too many requests|rate limit|blocked by/i.test(bodyText)) {
      return { status: 'rate_limited', error: 'access_denied' }
    }
    if (
      /error page\s*\|\s*ebay|something went wrong on our end|please go back and try again|technical error/i.test(
        bodyText,
      )
    ) {
      return { status: 'rate_limited', error: 'ebay_error_page' }
    }
    if (
      /please sign in to continue|you must sign in|sign in to (?:ebay|continue)/i.test(
        bodyText,
      ) ||
      root.querySelector('form[action*="signin"], #signin, .signin')
    ) {
      return { status: 'login_required', error: 'login' }
    }
    return { status: null, error: null }
  }

  function waitUntil(check, timeoutMs, intervalMs = 100) {
    return new Promise((resolve) => {
      const started = Date.now()
      const poll = () => {
        let ready = false
        try {
          ready = Boolean(check())
        } catch (_) {
          ready = false
        }
        if (ready) {
          resolve(true)
          return
        }
        if (Date.now() - started >= timeoutMs) {
          resolve(false)
          return
        }
        setTimeout(poll, intervalMs)
      }
      poll()
    })
  }

  function upgradeImage(url) {
    if (!url) return null
    const absolute = url.startsWith('//') ? `https:${url}` : url
    return absolute.replace(/s-l\d+\./, 's-l1600.')
  }

  function imageUrlOf(node) {
    const image = node.querySelector(
      'img.s-item__image-img, img.s-card__image, img[src*="ebayimg"], img[data-src*="ebayimg"]',
    )
    const src = image?.getAttribute('src')
    const raw =
      src && !src.startsWith('data:') ? src : image?.getAttribute('data-src')
    return upgradeImage(raw)
  }

  /**
   * Keep the variation parameters that select a specific price while dropping
   * eBay tracking parameters.
   */
  function normalizeItemUrl(href, sourceProductId) {
    try {
      const parsed = new URL(href, EBAY_ORIGIN)
      const itemId = sourceProductId || extractItemId(parsed.pathname)
      if (!itemId) return null
      const keep = new URLSearchParams()
      for (const key of ['var', 'epid']) {
        const value = parsed.searchParams.get(key)
        if (value) keep.set(key, value)
      }
      const query = keep.toString()
      return `${EBAY_ORIGIN}/itm/${itemId}${query ? `?${query}` : ''}`
    } catch (_) {
      return sourceProductId ? `${EBAY_ORIGIN}/itm/${sourceProductId}` : null
    }
  }

  function ratingOf(node) {
    const element = node.querySelector(
      '.x-star-rating, [class*="star-rating"], [aria-label*="out of 5 stars"]',
    )
    if (!element) return null
    return parseUtils.parseRating(
      element.getAttribute('aria-label') ||
        element.getAttribute('title') ||
        element.textContent,
    )
  }

  function attributeRowsOf(node) {
    return allTexts(node, [
      '.s-card__attribute-row',
      '.s-item__details-row',
      '.s-item__detail',
    ])
  }

  function conditionOf(node, rows = []) {
    for (const subtitle of allTexts(node, [
      '.s-item__subtitle',
      '.s-card__subtitle',
      '.SECONDARY_INFO',
    ])) {
      const condition = knownCondition(subtitle)
      if (condition) return condition
    }
    for (const row of rows) {
      const condition = knownCondition(row)
      if (condition) return condition
    }
    return null
  }

  function shippingOf(node, rows = [], currency = null) {
    const direct = text(node, [
      '.s-item__shipping',
      '.s-item__logisticsCost',
      '.s-card__shipping',
    ])
    const candidates = direct ? [direct] : []
    for (const row of rows) {
      const part = row
        .split(/[·|]/)
        .map(clean)
        .find((value) => /shipping|delivery|postage/i.test(value))
      if (part) candidates.push(part)
    }
    const raw = candidates.find(Boolean)
    if (!raw) {
      return { shipping: null, shipping_cost: null, shipping_currency: null }
    }
    const shipping = clean(raw.replace(/^\+\s*/, ''))
    if (/free/i.test(shipping)) {
      return { shipping, shipping_cost: 0, shipping_currency: null }
    }
    return {
      shipping,
      shipping_cost: parseUtils.parsePrice(shipping),
      shipping_currency: parseUtils.parseCurrency(
        shipping,
        currency || null,
      ),
    }
  }

  function listingTypeOf(node, rows = [], buyItNowOnly = false) {
    const direct = allTexts(node, [
      '.s-item__purchase-options-with-icon',
      '.s-item__purchase-options',
      '.s-card__purchase-options',
    ])
    const values = [...direct, ...rows]
    if (
      values.some((value) =>
        /place bid|bid now|auction|\bbids?\b/i.test(value),
      )
    ) {
      return 'auction'
    }
    if (values.some((value) => /buy it now/i.test(value))) return 'buy_it_now'
    // LH_BIN=1 constrains the whole result page to Buy It Now listings even
    // when a card does not render a purchase-option row.
    return buyItNowOnly ? 'buy_it_now' : null
  }

  function shopNameOf(node) {
    const direct = text(node, [
      '.s-item__seller-info-text',
      '.s-card__seller',
      '[data-testid="seller-info"]',
    ])
    if (direct) return direct
    const row = node.querySelector(
      '.su-card-container__attributes__secondary .s-card__attribute-row',
    )
    const names = [
      ...(row?.querySelectorAll('.su-styled-text, span') || []),
    ]
      .map((element) => clean(element.textContent))
      .filter(Boolean)
    const name = names.find(
      (value) => !/guaranteed by|positive|feedback/i.test(value),
    )
    return name || null
  }

  function locationOf(node, rows = []) {
    const direct = text(node, [
      '.s-item__location',
      '.s-item__itemLocation',
      '[data-testid="item-location"]',
      '.s-card__location',
    ])
    if (direct) {
      return clean(direct.replace(/^(?:from|located in:?)\s*/i, ''))
    }
    for (const row of rows) {
      const match =
        row.match(/^from\s+(.+)$/i) ||
        row.match(/^located in:?\s*(.+)$/i)
      if (match) return clean(match[1])
    }
    return null
  }

  function hotnessOf(node, rows = []) {
    return [
      ...allTexts(node, [
        '.s-item__hotness',
        '.s-item__additionalItemHotness',
      ]),
      ...rows,
    ].join(' · ')
  }

  function sponsoredOf(node) {
    const badge = node.querySelector(
      '.s-item__ad-badge, .s-card__ad-badge, [data-testid="s-card__ad-badge"]',
    )
    if (badge && /sponsored/i.test(clean(badge.textContent))) return true
    // eBay renders the sponsored footer label with its text reversed in the
    // DOM and paints the real label as a background image. Require the
    // dedicated separator element and either the reversed label or an SVG
    // whose decoded text says "sponsored"; an unrelated background image is
    // not enough.
    const separator = node.querySelector('.s-card__footer .s-card__sep b')
    if (separator) {
      const text = clean(separator.textContent)
      if (/derosnops/i.test(text)) return true
      const style = separator.getAttribute('style') || ''
      const encoded = style.match(
        /data:image\/svg\+xml;base64,([^"')]+)/i,
      )
      if (encoded) {
        try {
          if (/ponsor/i.test(atob(encoded[1]))) return true
        } catch (_) {
          /* ignore malformed inline images */
        }
      }
    }
    // Fallback only for elements explicitly labelled as sponsorship. Do not
    // scan every leaf node: listing cards can contain hidden/decorative
    // "Sponsored" text, which previously marked every result as sponsored.
    const labelled = node.querySelector(
      '[class*="sponsor" i], [data-testid*="sponsor" i], ' +
        '[aria-label*="sponsor" i]',
    )
    return Boolean(
      labelled && /^sponsored$/i.test(clean(labelled.textContent)),
    )
  }

  function totalResultCount(root) {
    const raw = text(root, [
      '.srp-controls__count-heading',
      '.result-count__count-heading',
      '[data-testid="srp-controls__count-heading"]',
    ])
    if (!raw) return null
    const match =
      raw.match(/\bof\s+([\d,]+)/i) ||
      raw.match(/([\d,]+)\+?\s+results?/i) ||
      raw.match(/^([\d,]+)/)
    if (!match) return null
    const value = Number(match[1].replace(/,/g, ''))
    return Number.isFinite(value) ? value : null
  }

  function pageSizeOf(url) {
    try {
      const value = Number(new URL(url).searchParams.get('_ipg'))
      return Number.isFinite(value) && value > 0 ? value : 60
    } catch (_) {
      return 60
    }
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
    return classifyPage(root, url).status
  }

  function parseListing(root = document, baseUrl = location.href) {
    const problem = classifyPage(root, baseUrl)
    if (problem.status) {
      return {
        status: problem.status,
        error: problem.error,
        items: [],
        diagnostics: {
          url: baseUrl,
          card_count: root.querySelectorAll(ITEM_SELECTORS).length,
        },
      }
    }

    const category = categoryOf(root)
    let buyItNowOnly = false
    try {
      buyItNowOnly = new URL(baseUrl).searchParams.get('LH_BIN') === '1'
    } catch (_) {
      /* ignore malformed document URLs */
    }
    const items = []
    for (const node of root.querySelectorAll(ITEM_SELECTORS)) {
      const link = itemLink(node)
      const href = link?.getAttribute('href') || ''
      const sourceProductId = extractItemId(href)
      const title = clean(
        text(node, [
          '.s-item__title',
          '.s-card__title .su-styled-text.primary',
          '.s-card__title',
        ]),
      )
        .replace(/^(?:New Listing|Sponsored)\s*/i, '')
        .replace(/\s*Opens in a new window.*$/i, '')
        .trim()
      if (!sourceProductId || !title || /^shop on ebay$/i.test(title)) continue

      const rows = attributeRowsOf(node)
      const priceText = text(node, [
        '.s-item__price',
        '.s-card__price',
        '[data-testid="item-price"]',
        '.s-card__price-primary',
      ])
      const priceRange = parseUtils.parsePriceRange(priceText)
      const currency = parseUtils.parseCurrency(priceText, 'USD')
      const hotnessText = hotnessOf(node, rows)
      const condition = conditionOf(node, rows)
      const shipping = shippingOf(node, rows, currency)
      items.push({
        source_product_id: String(sourceProductId),
        url: normalizeItemUrl(href, sourceProductId),
        title,
        price: priceRange ? priceRange.min : parseUtils.parsePrice(priceText),
        price_max: priceRange ? priceRange.max : null,
        price_is_range: Boolean(priceRange),
        currency,
        image_url: imageUrlOf(node),
        shop_name: shopNameOf(node),
        sales: parseUtils.parseSales(hotnessText, { requireSold: true }),
        watchers: parseUtils.parseWatchers(hotnessText),
        rating: ratingOf(node),
        sponsored: sponsoredOf(node),
        shipping: shipping.shipping,
        shipping_cost: shipping.shipping_cost,
        shipping_currency: shipping.shipping_currency,
        condition,
        badges: condition ? [condition] : [],
        category,
        location: locationOf(node, rows),
        listing_type: listingTypeOf(node, rows, buyItNowOnly),
      })
    }

    const page = currentPageNumber(baseUrl)
    const resultCount = totalResultCount(root)
    if (hasNoResults(root) || resultCount === 0) {
      // eBay often shows "results matching fewer words" fallback cards when
      // the exact query has no matches. Never ingest those as real results.
      return {
        status: 'ok',
        items: [],
        page,
        result_count: 0,
        page_item_count: 0,
        fallback_item_count: items.length,
        has_next: false,
        next_url: null,
        no_results: true,
      }
    }
    return {
      status: 'ok',
      items,
      page,
      result_count: resultCount ?? items.length,
      page_item_count: items.length,
      fallback_item_count: 0,
      has_next: hasNextPage(root, baseUrl),
      next_url: nextPageUrl(root, baseUrl),
    }
  }

  function attributesOf(root) {
    const attributes = {}
    for (const row of root.querySelectorAll('.ux-labels-values')) {
      const label = clean(
        row.querySelector('[class*="ux-labels-values__labels"]')?.textContent,
      ).replace(/[：:]\s*$/, '')
      const value = clean(
        row.querySelector('[class*="ux-labels-values__values"]')?.textContent,
      )
      if (label && value && !(label in attributes)) attributes[label] = value
    }
    return attributes
  }

  function conditionFrom(root, attributes) {
    const candidates = [
      attributes.Condition,
      attributes['Item condition'],
      text(root, [
        '.x-item-condition-text',
        '.x-item-condition .ux-textspans',
        '.x-item-condition',
      ]),
    ]
    for (const candidate of candidates) {
      const condition = knownCondition(candidate)
      if (condition) return condition
    }
    return null
  }

  function sellerFrom(root) {
    const name = text(root, [
      '.x-sellercard-atf__info__about-seller a .ux-textspans',
      '.x-sellercard-atf__info__about-seller a',
      '.x-sellercard-atf__about-seller a',
      '[data-testid="x-sellercard-atf"] a',
      '.ux-seller-section__item--seller a',
    ])
    const feedbackText = text(root, [
      '.x-sellercard-atf__data-item',
      '.x-sellercard-atf__info__feedback',
      '.ux-seller-section__item--feedback',
    ])
    const match = feedbackText?.match(/(\d{1,3}(?:\.\d+)?)\s*%\s*positive/i)
    if (!name && !match) return null
    return {
      name: name || null,
      feedback_percent: match ? Number(match[1]) : null,
    }
  }

  /**
   * Split a shipping row that has no inner span markup into its primary and
   * approximate amounts, e.g. "EUR 85.00 (approx RMB 633.65) DHL". This only
   * separates the two page-rendered values; it never converts a currency.
   */
  function splitApproxAmounts(raw) {
    const lower = String(raw || '').toLowerCase()
    const approxIndex = lower.indexOf('approx')
    if (approxIndex === -1) return { primaryText: raw, approxText: null }
    const open = raw.lastIndexOf('(', approxIndex)
    const close = raw.indexOf(')', approxIndex)
    if (open !== -1 && close > open) {
      return {
        primaryText: clean(raw.slice(0, open)),
        approxText: clean(raw.slice(open + 1, close)),
      }
    }
    return {
      primaryText: clean(raw.slice(0, approxIndex)),
      approxText: clean(raw.slice(approxIndex)),
    }
  }

  function shippingFrom(root, attributes) {
    const row =
      root.querySelector('.ux-labels-values--shipping') ||
      root.querySelector('.ux-layout-section--shipping .ux-labels-values')
    const values =
      row?.querySelector('.ux-labels-values__values-content') ||
      row?.querySelector('.ux-labels-values__values')
    const raw =
      clean(values?.textContent) ||
      attributes.Shipping ||
      attributes['Shipping cost'] ||
      attributes.Delivery ||
      text(root, ['.d-shipping-minview', '[data-testid="d-shipping-minview"]'])
    if (!raw) {
      return {
        shipping: null,
        shipping_cost: null,
        shipping_currency: null,
        shipping_approx_cost: null,
        shipping_approx_currency: null,
      }
    }
    if (/free/i.test(raw)) {
      return {
        shipping: raw,
        shipping_cost: 0,
        shipping_currency: null,
        shipping_approx_cost: null,
        shipping_approx_currency: null,
      }
    }

    let primaryText = null
    let approxText = null
    if (values) {
      for (const span of values.querySelectorAll('.ux-textspans')) {
        const spanText = clean(span.textContent)
        if (!spanText || parseUtils.parsePrice(spanText) === null) continue
        if (/approx/i.test(spanText)) {
          // Some layouts put both amounts in one span, e.g.
          // "US $399.73 (approx. RMB 2,669.20) delivery...".
          const split = splitApproxAmounts(spanText)
          if (!primaryText && split.primaryText) {
            primaryText = split.primaryText
          }
          if (!approxText && split.approxText) {
            approxText = split.approxText
          }
        } else if (!primaryText) {
          primaryText = spanText
        }
      }
    }
    if (!primaryText && !approxText) {
      const split = splitApproxAmounts(raw)
      primaryText = split.primaryText
      approxText = split.approxText
    }
    const shippingCost = primaryText
      ? parseUtils.parsePrice(primaryText)
      : null
    const approxCost = approxText
      ? parseUtils.parsePrice(approxText)
      : null
    return {
      shipping: raw,
      shipping_cost: shippingCost,
      shipping_currency:
        shippingCost !== null
          ? parseUtils.parseCurrency(primaryText, null)
          : null,
      shipping_approx_cost: approxCost,
      shipping_approx_currency:
        approxCost !== null
          ? parseUtils.parseCurrency(approxText, null)
          : null,
    }
  }

  function importChargesFrom(root, attributes = {}) {
    const row =
      root.querySelector('.ux-labels-values--importCharges') ||
      root.querySelector('[data-testid="import-charges"]') ||
      root.querySelector('.d-import-charges')
    const attributeKey = Object.keys(attributes).find((key) =>
      /import\s*charges?/i.test(key),
    )
    const raw =
      clean(row?.textContent) ||
      (attributeKey ? clean(attributes[attributeKey]) : null)
    const body = clean(root.body?.textContent || '')
    const customsMayApply =
      /customs processing|import duties|customs duties|additional charges/i.test(
        body,
      )
    if (!raw) {
      return {
        import_charges: null,
        import_charges_currency: null,
        customs_may_apply: customsMayApply,
      }
    }
    const importCharges = /free|not applicable|no import/i.test(raw)
      ? 0
      : parseUtils.parsePrice(raw)
    return {
      import_charges: importCharges,
      import_charges_currency:
        importCharges !== null
          ? parseUtils.parseCurrency(raw, null)
          : null,
      customs_may_apply: customsMayApply,
    }
  }

  function quantityFrom(root) {
    const raw = text(root, [
      '.x-quantity__availability',
      '[data-testid="x-quantity__availability"]',
      '.d-quantity__availability',
    ])
    if (!raw) return { quantity_available: null, sold: null }
    const available = raw.match(/([\d,]+)\s*available/i)
    const sold = raw.match(/([\d,]+)\s*sold/i)
    return {
      quantity_available: available
        ? Number(available[1].replace(/,/g, ''))
        : null,
      sold: sold ? Number(sold[1].replace(/,/g, '')) : null,
    }
  }

  function availabilityFrom(root) {
    if (
      root.querySelector(
        '.x-item-status--ended, .x-item-status--unavailable, .x-item-status--sold',
      )
    ) {
      return 'unavailable'
    }
    const status = text(root, ['.x-item-status'])
    if (status && /ended|no longer available|out of stock|unavailable/i.test(status)) {
      return 'unavailable'
    }
    return 'available'
  }

  function listingTypeFromDetail(root) {
    if (root.querySelector('.x-bid-action, [data-testid="x-bid-action"]')) {
      return 'auction'
    }
    if (root.querySelector('.x-bin-action, [data-testid="x-bin-action"]')) {
      return 'buy_it_now'
    }
    const raw = text(root, ['.x-item-cta', '.vi-atf-buy-box'])
    if (!raw) return null
    if (/place bid|bid now|auction/i.test(raw)) return 'auction'
    if (/buy it now/i.test(raw)) return 'buy_it_now'
    return null
  }

  function variationsFrom(root) {
    const menu = root.querySelector('.x-msku, [data-testid="x-msku"]')
    if (!menu) return { has_variations: false, variations: [] }
    const labels = [
      ...root.querySelectorAll(
        '.x-msku__label, .x-msku__select-label, .x-msku__label-text',
      ),
    ]
      .map((element) => clean(element.textContent))
      .filter(Boolean)
    return { has_variations: true, variations: [...new Set(labels)].slice(0, 10) }
  }

  function highlightsFrom(root) {
    const nodes = root.querySelectorAll(
      '.x-about-this-item li, .ux-layout-section--features li, .x-about-this-item .ux-list-item, [data-testid="x-about-this-item"] li',
    )
    return [...new Set([...nodes].map((node) => clean(node.textContent)).filter(Boolean))]
      .slice(0, 10)
  }

  function imagesFrom(root) {
    const nodes = root.querySelectorAll(
      '.ux-image-carousel-item img, #icImg, .x-photo-viewer img, [data-testid="ux-image-carousel"] img',
    )
    const images = []
    for (const image of nodes) {
      const src = image.getAttribute('src')
      const raw = src && !src.startsWith('data:') ? src : image.getAttribute('data-src')
      const url = upgradeImage(raw)
      if (url && !images.includes(url)) images.push(url)
      if (images.length >= 5) break
    }
    return images
  }

  function parseDetail(root = document, url = location.href) {
    const problem = classifyPage(root, url)
    if (problem.status) {
      return { status: problem.status, error: problem.error }
    }

    const title = text(root, DETAIL_TITLE_SELECTORS)
    const primaryPriceText = text(root, DETAIL_PRICE_SELECTORS)
    const approxPriceText = text(root, [
      '.x-price-approx',
      '[data-testid="x-price-approx"]',
    ])
    const priceText = primaryPriceText || approxPriceText
    const priceRange = parseUtils.parsePriceRange(priceText)
    const attributes = Object.fromEntries(
      Object.entries(attributesOf(root)).slice(0, 40),
    )
    if (!title && !Object.keys(attributes).length) {
      return {
        status: 'parse_error',
        error: 'detail_not_found',
        diagnostics: {
          url,
          title_found: false,
          price_found: Boolean(primaryPriceText || approxPriceText),
          attribute_count: 0,
        },
      }
    }

    const condition = conditionFrom(root, attributes)
    const seller = sellerFrom(root)
    const shipping = shippingFrom(root, attributes)
    const importCharges = importChargesFrom(root, attributes)
    const quantity = quantityFrom(root)
    const variations = variationsFrom(root)
    const bidsText = text(root, ['.x-bid-count', '.vi-bid-count'])
    const bidsMatch = bidsText?.match(/([\d,]+)/)
    const watchText = text(root, [
      '.x-watch__watchers',
      '[data-testid="x-watch__watchers"]',
    ])

    return {
      status: 'ok',
      title,
      diagnostics: {
        title_found: Boolean(title),
        price_found: Boolean(primaryPriceText || approxPriceText),
        attribute_count: Object.keys(attributes).length,
      },
      price: priceRange ? priceRange.min : parseUtils.parsePrice(priceText),
      price_max: priceRange ? priceRange.max : null,
      price_is_range: Boolean(priceRange),
      currency: parseUtils.parseCurrency(priceText, 'USD'),
      approx_price: parseUtils.parsePrice(approxPriceText),
      approx_currency: approxPriceText
        ? parseUtils.parseCurrency(approxPriceText, 'USD')
        : null,
      attributes,
      condition,
      seller,
      shipping: shipping.shipping,
      shipping_cost: shipping.shipping_cost,
      shipping_currency: shipping.shipping_currency,
      shipping_approx_cost: shipping.shipping_approx_cost,
      shipping_approx_currency: shipping.shipping_approx_currency,
      import_charges: importCharges.import_charges,
      import_charges_currency: importCharges.import_charges_currency,
      customs_may_apply: importCharges.customs_may_apply,
      returns:
        attributes.Returns ||
        attributes['Return policy'] ||
        text(root, ['.d-returns-title', '[data-testid="d-returns-title"]']),
      location:
        attributes['Item location'] ||
        attributes.Location ||
        text(root, ['.d-loc']),
      quantity_available: quantity.quantity_available,
      sold: quantity.sold,
      watchers: parseUtils.parseWatchers(watchText),
      availability: availabilityFrom(root),
      listing_type: listingTypeFromDetail(root),
      best_offer: Boolean(
        root.querySelector(
          '.x-best-offer, [data-testid="x-best-offer"], .x-best-offer-action',
        ),
      ),
      bids: bidsMatch ? Number(bidsMatch[1].replace(/,/g, '')) : null,
      has_variations: variations.has_variations,
      variations: variations.variations,
      highlights: highlightsFrom(root),
      images: imagesFrom(root),
      item_number:
        attributes['eBay item number'] ||
        attributes['Item number'] ||
        text(root, ['#descItemNumber']),
    }
  }

  function matchesUrl(url = '') {
    try {
      const parsed = new URL(url)
      const host = parsed.hostname
      const isEbay = host === 'ebay.com' || host.endsWith('.ebay.com')
      return (
        isEbay &&
        (parsed.pathname === '/sch' ||
          parsed.pathname.startsWith('/sch/') ||
          parsed.pathname.startsWith('/b/'))
      )
    } catch (_) {
      return false
    }
  }

  function waitForListing(
    root = document,
    url = location.href,
    timeoutMs = 8000,
  ) {
    let lastCount = -1
    let stablePolls = 0
    return waitUntil(() => {
      const problem = classifyPage(root, url)
      if (problem.status) return true
      if (hasNoResults(root)) return true

      const count = countUsableCards(root)
      const total = totalResultCount(root)
      if (total === 0) {
        // The header may briefly say 0 while the grid is still hydrating.
        stablePolls = count === lastCount ? stablePolls + 1 : 0
        lastCount = count
        return stablePolls >= 5
      }
      if (count === 0) return false
      if (count === lastCount) {
        stablePolls += 1
      } else {
        lastCount = count
        stablePolls = 0
      }
      return stablePolls >= 3
    }, timeoutMs)
  }

  function waitForDetail(
    root = document,
    url = location.href,
    timeoutMs = 6000,
  ) {
    return waitUntil(() => {
      if (isDetailReady(root)) return true
      return Boolean(classifyPage(root, url).status)
    }, timeoutMs)
  }

  function nextPageUrl(root = document, url = location.href) {
    const link = root.querySelector('a.pagination__next[href]')
    const href = link?.getAttribute('href')
    if (href && href !== '#' && !href.startsWith('javascript:')) {
      try {
        return new URL(href, EBAY_ORIGIN).toString()
      } catch (_) {
        /* fall through to the URL-based fallback */
      }
    }

    const page = currentPageNumber(url)
    const total = totalResultCount(root)
    const pageSize = pageSizeOf(url)
    if (total && page * pageSize < total) {
      try {
        const parsed = new URL(url, EBAY_ORIGIN)
        parsed.searchParams.set('_pgn', String(page + 1))
        return parsed.toString()
      } catch (_) {
        return null
      }
    }
    return null
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

  function normalizeExcludeTerms(value) {
    const entries = Array.isArray(value) ? value : [value]
    const terms = []
    for (const entry of entries) {
      for (const term of String(entry || '').split(/[\s,]+/).filter(Boolean)) {
        const text = term.replace(/^-+/, '')
        if (text) terms.push(`-${text}`)
      }
    }
    return [...new Set(terms)].join(' ')
  }

  function buildSearchUrl(query, filters = {}) {
    const baseQuery = String(query || '').trim()
    const exclusions = normalizeExcludeTerms(filters.excludeTerms)
    const keywordText = exclusions ? `${baseQuery} ${exclusions}` : baseQuery
    let url = `${EBAY_ORIGIN}/sch/i.html?_nkw=${encodeURIComponent(keywordText)}`
    if (filters.categoryId) {
      url += `&_sacat=${encodeURIComponent(filters.categoryId)}`
    }
    if (filters.maxPrice != null) {
      url += `&_udhi=${encodeURIComponent(filters.maxPrice)}`
    }
    if (filters.sort === 'price_asc') {
      url += '&_sop=15'
    } else if (filters.sort === 'price_desc') {
      url += '&_sop=16'
    }
    if (filters.buyItNowOnly) {
      url += '&LH_BIN=1'
    }
    return `${url}&_ipg=60`
  }

  function matchUrl(url = '') {
    try {
      const host = new URL(url).hostname
      return host === 'ebay.com' || host.endsWith('.ebay.com')
    } catch (_) {
      return false
    }
  }

  function detailUrl(sourceProductId) {
    return sourceProductId
      ? `${EBAY_ORIGIN}/itm/${sourceProductId}`
      : null
  }

  function detectCurrency(root = document) {
    const explicitSelectors = [
      '[data-testid="currency-selector"]',
      '[aria-label*="currency" i]',
      '.currency-selector',
    ]
    for (const selector of explicitSelectors) {
      for (const node of [...root.querySelectorAll(selector)].slice(0, 3)) {
        const detected = parseUtils.parseCurrency(clean(node.textContent), null)
        if (detected) return detected
      }
    }

    // Price text is the most reliable signal on a listing page. A bare "$"
    // on ebay.com is USD; RMB/CNY, C$, €, £ and ¥ stay explicit.
    //
    // `.x-price-primary` belongs to detail pages. A listing page can contain
    // that class in a hidden carousel or template, so do not let it compete
    // with the actual result-card prices.
    const listingCards = root.querySelector('.s-card, .s-item')
    const priceSelectors = listingCards
      ? [
          { selector: '.s-card__price', weight: 2 },
          { selector: '.s-item__price', weight: 1 },
        ]
      : [
          { selector: '.x-price-primary', weight: 1 },
          { selector: '.s-item__price', weight: 1 },
          { selector: '.s-card__price', weight: 1 },
        ]
    const counts = new Map()
    for (const { selector, weight } of priceSelectors) {
      for (const node of [...root.querySelectorAll(selector)].slice(0, 8)) {
        const detected = parseUtils.parseCurrency(clean(node.textContent), 'USD')
        if (!detected) continue
        counts.set(detected, (counts.get(detected) || 0) + weight)
      }
    }
    if (counts.size) {
      return [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0]
    }

    const geo = clean(root.querySelector('#gh-eb-Geo')?.textContent)
    return geo ? parseUtils.parseCurrency(geo, null) : null
  }

  return {
    id: 'ebay',
    site: 'ebay',
    matchUrl,
    detectCurrency,
    detectBlock,
    classifyPage,
    matchesUrl,
    waitForListing,
    waitForDetail,
    parseListing,
    parseDetail,
    hasNextPage,
    nextPageUrl,
    clickNextPage,
    currentPageNumber,
    buildSearchUrl,
    detailUrl,
  }
})
