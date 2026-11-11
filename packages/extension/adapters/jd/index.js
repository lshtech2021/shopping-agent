;(function (root, factory) {
  const parseUtils =
    typeof module === 'object' && module.exports
      ? require('../../content/parse-utils.js')
      : root.__saParse
  const api = factory(parseUtils)
  if (typeof module === 'object' && module.exports) module.exports = api
  root.__saJd = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function (parseUtils) {
  const ITEM_SELECTORS = '#J_goodsList li.gl-item, li[data-sku], div[data-sku]'

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

  function extractSku(value) {
    if (!value) return null
    const match = String(value).match(/item\.jd\.com\/(\d+)\.html/)
    return match ? match[1] : null
  }

  function detectBlock(root = document, url = location.href) {
    try {
      const parsed = new URL(url)
      if (parsed.hostname.includes('passport.jd.com')) return 'login_required'
      if (
        parsed.hostname === 'verify.jd.com' ||
        parsed.hostname.includes('captcha') ||
        parsed.pathname.includes('verify')
      ) {
        return 'captcha'
      }
    } catch (_) {
      /* ignore */
    }
    const hasListing = Boolean(root.querySelector(ITEM_SELECTORS))
    const hasDetail = Boolean(
      root.querySelector('.sku-name, #name h1, .summary-price .p-price'),
    )
    if (hasListing || hasDetail) return null

    const bodyText = root.body?.innerText || root.body?.textContent || ''
    if (!bodyText.trim()) return 'rate_limited'
    if (root.querySelector('#JDJRV, .JDJRV, [class*="captcha"]')) return 'captcha'
    if (/验证码|安全验证|滑动验证/i.test(bodyText)) return 'captcha'
    if (/访问出错|频繁|请稍后再试|网络繁忙/i.test(bodyText)) {
      return 'rate_limited'
    }
    return null
  }

  function parseListing(root = document, baseUrl = location.href) {
    const blocked = detectBlock(root, baseUrl)
    if (blocked) return { status: blocked, items: [] }

    const items = []
    const nodes = [...root.querySelectorAll(ITEM_SELECTORS)].filter(
      (node) => !node.parentElement?.closest('[data-sku]'),
    )
    for (const node of nodes) {
      const link = node.querySelector(
        '.p-name a[href*="item.jd.com"], a[href*="item.jd.com"]',
      )
      const href = link?.getAttribute('href') || ''
      const sourceProductId = node.getAttribute('data-sku') || extractSku(href)
      const title =
        clean(
          node.querySelector('[class*="_goods_title_container"] span[title]')?.getAttribute(
            'title',
          ),
        ) || text(node, ['.p-name em', '.p-name', '[class*="_goods_title"]'])
      if (!sourceProductId || !title) continue
      const priceText = text(node, ['.p-price', '[class*="_price_"]', '.p-price i'])
      const priceRange = parseUtils.parsePriceRange(priceText)
      const shopName = text(node, ['[class*="_limit_"]', '.p-shop a', '.p-shop'])
      const ratingText = text(node, [
        '.p-commit',
        '[class*="_rate_"]',
        '[class*="_comment_"]',
      ])
      const salesText = text(node, ['.p-commit', '[class*="_sales_"]'])
      items.push({
        source_product_id: String(sourceProductId),
        url: `https://item.jd.com/${sourceProductId}.html`,
        title,
        price: priceRange ? priceRange.min : parseUtils.parsePrice(priceText),
        price_max: priceRange ? priceRange.max : null,
        price_is_range: Boolean(priceRange),
        currency: 'CNY',
        shop_name: shopName,
        sales: parseUtils.parseSales(salesText),
        rating: parseUtils.parseRating(ratingText),
        sponsored: Boolean(
          node.querySelector('[class*="sponsor"], [class*="_ad_"]'),
        ),
        shipping: null,
        condition: null,
        badges: [],
        category: null,
      })
    }

    const bodyText = root.body?.innerText || root.body?.textContent || ''
    if (
      !items.length &&
      /抱歉，没有找到|没有找到相关|没有相关商品|暂无商品/i.test(bodyText)
    ) {
      return {
        status: 'ok',
        items: [],
        page: currentPageNumber(root, baseUrl),
        result_count: 0,
        has_next: false,
        no_results: true,
      }
    }
    if (!items.length && !root.querySelector(ITEM_SELECTORS)) {
      // Empty DOM often means soft block / incomplete render.
      if (!bodyText.trim()) {
        return { status: 'rate_limited', items: [] }
      }
    }

    return {
      status: 'ok',
      items,
      page: currentPageNumber(root, baseUrl),
      result_count: items.length,
      has_next: hasNextPage(root),
    }
  }

  function metaContent(root, selectors) {
    for (const selector of selectors) {
      const node = root.querySelector(selector)
      const value = clean(node?.getAttribute('content'))
      if (value) return value
    }
    return null
  }

  function titleFromDocument(root) {
    const raw =
      metaContent(root, [
        'meta[property="og:title"]',
        'meta[name="keywords"]',
      ]) || clean(root.querySelector('title')?.textContent)
    if (!raw) return null
    return clean(raw.replace(/\s*[-_|].*(京东|JD).*$/i, '')) || raw
  }

  function attributesFromScripts(root) {
    const attributes = {}
    const scripts = [...root.querySelectorAll('script')].map(
      (node) => node.textContent || '',
    )
    for (const script of scripts) {
      const match = script.match(
        /pageConfig\s*=\s*(\{[\s\S]*?\})\s*;?\s*(?:<\/script>|$)/,
      )
      if (!match) continue
      try {
        // pageConfig is not always strict JSON; pull a few known keys loosely.
        const name = script.match(/name\s*:\s*['"]([^'"]+)['"]/)
        if (name?.[1]) attributes['商品名称'] = clean(name[1])
      } catch (_) {
        /* ignore */
      }
    }
    return attributes
  }

  function parseDetail(root = document, url = location.href) {
    const blocked = detectBlock(root, url)
    if (blocked) return { status: blocked }

    const title =
      text(root, [
        '.sku-name',
        '#itemName',
        '#name h1',
        '.itemInfo-wrap .sku-name',
        '.sku-name-title',
        '[class*="skuName"]',
        '.product-intro .sku-name',
        'div.sku-name',
      ]) || titleFromDocument(root)
    const priceText = text(root, [
      '.summary-price .p-price .price',
      '.summary-price .p-price',
      '.price .p-price .price',
      '.price .p-price',
      '#jd-price',
      '.p-price .price',
      '[class*="price"] .p-price',
    ])
    const attributes = attributesFromScripts(root)
    const rows = root.querySelectorAll(
      [
        '.p-parameter ul.p-parameter-list li',
        '#detail .parameter2 li',
        '.parameter2 li',
        '[class*="parameter"] li',
        '.goods-base li',
      ].join(', '),
    )
    for (const row of rows) {
      const labelNode = row.querySelector('.label')
      let label = clean(labelNode?.textContent).replace(/[：:]\s*$/, '')
      let value = ''
      if (labelNode) {
        value = clean(clean(row.textContent).replace(clean(labelNode.textContent), ''))
      } else {
        const parts = clean(row.textContent).split(/[：:]/)
        if (parts.length >= 2) {
          label = clean(parts.shift())
          value = clean(parts.join('：'))
        }
      }
      if (label && value) attributes[label] = value
    }

    if (!title && !Object.keys(attributes).length) {
      return { status: 'parse_error', error: 'detail_not_found' }
    }

    return {
      status: 'ok',
      title,
      price: parseUtils.parsePrice(priceText),
      currency: 'CNY',
      attributes,
    }
  }

  function nextButton(root = document) {
    return root.querySelector('[class*="_pagination_next"]')
  }

  function isDisabledButton(button) {
    return Boolean(button && String(button.className).includes('_disabled_'))
  }

  function hasNextPage(root = document) {
    const spa = nextButton(root)
    if (spa) return !isDisabledButton(spa)
    const link =
      root.querySelector('#J_bottomPage a.pn-next[href]') ||
      root.querySelector('a.pn-next[href]')
    if (!link) return false
    const href = link.getAttribute('href') || ''
    return Boolean(href) && !href.startsWith('javascript:') && !link.classList.contains('disabled')
  }

  function nextPageUrl(root = document, baseUrl = location.href) {
    const link =
      root.querySelector('#J_bottomPage a.pn-next[href]') ||
      root.querySelector('a.pn-next[href]')
    if (!link) return null
    const href = link.getAttribute('href')
    if (!href || href.startsWith('javascript:') || link.classList.contains('disabled')) {
      return null
    }
    try {
      return new URL(href, baseUrl).toString()
    } catch (_) {
      return null
    }
  }

  function currentPageNumber(root = document, url = location.href) {
    const active = root.querySelector('[class*="_pagination_item"][class*="_active"]')
    if (active) {
      const value = Number(clean(active.textContent))
      if (Number.isFinite(value) && value > 0) return Math.trunc(value)
    }
    try {
      const value = Number(new URL(url).searchParams.get('page'))
      return Number.isFinite(value) && value > 0 ? Math.trunc(value) : 1
    } catch (_) {
      return 1
    }
  }

  function skuSignature(root = document) {
    return [...root.querySelectorAll('[data-sku]')]
      .slice(0, 5)
      .map((el) => el.getAttribute('data-sku') || '')
      .join(',')
  }

  async function clickNextPage(root = document, timeoutMs = 15000) {
    const button = nextButton(root)
    if (!button || isDisabledButton(button)) return false
    const before = skuSignature(root)
    button.click()
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 300))
      const current = skuSignature(root)
      if (current && current !== before) return true
    }
    return false
  }

  function buildSearchUrl(query) {
    return `https://search.jd.com/Search?keyword=${encodeURIComponent(query || '')}&enc=utf-8`
  }

  function detailUrl(sourceProductId) {
    return sourceProductId
      ? `https://item.jd.com/${sourceProductId}.html`
      : null
  }

  function matchUrl(url = '') {
    try {
      const host = new URL(url).hostname
      return host === 'jd.com' || host.endsWith('.jd.com')
    } catch (_) {
      return false
    }
  }

  function detectCurrency() {
    return 'CNY'
  }

  return {
    id: 'jd',
    site: 'jd',
    matchUrl,
    detectCurrency,
    detectBlock,
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
