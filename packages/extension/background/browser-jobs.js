import { createJobLogger } from '../shared/logger.js'

const SITE_TABS = new Map()
const SITE_URLS = new Map()
const SITE_QUERIES = new Map()
const SITE_PAGES = new Map()
const SITE_TABS_KEY = 'siteTabs'
const JOB_TIMEOUT_MS = 50000

async function persistTabState() {
  if (typeof chrome === 'undefined' || !chrome.storage?.session) return
  try {
    const state = {}
    for (const [site, tabId] of SITE_TABS.entries()) {
      state[site] = {
        tabId,
        url: SITE_URLS.get(site) || null,
        query: SITE_QUERIES.get(site) || null,
        page: SITE_PAGES.get(site) || null,
      }
    }
    await chrome.storage.session.set({ [SITE_TABS_KEY]: state })
  } catch (_) {
    /* session storage is best effort */
  }
}

async function restoreTabState() {
  if (typeof chrome === 'undefined' || !chrome.storage?.session) return
  try {
    const stored = await chrome.storage.session.get({ [SITE_TABS_KEY]: {} })
    const entries = stored?.[SITE_TABS_KEY] || {}
    for (const [site, value] of Object.entries(entries)) {
      try {
        const tabId = typeof value === 'object' ? value?.tabId : value
        if (!tabId) continue
        const tab = await chrome.tabs.get(tabId)
        const allowedSite = siteForTabKey(site)
        if (!tab.url || !isAllowedSiteUrl(allowedSite, tab.url)) {
          continue
        }
        let restoredUrl = tab.url
        if (
          typeof value === 'object' &&
          value.url &&
          !samePage(tab.url, value.url)
        ) {
          await chrome.tabs.update(tab.id, { url: value.url })
          await waitComplete(tab.id, value.url, Date.now() + 5000)
          restoredUrl = value.url
        }
        SITE_TABS.set(site, tab.id)
        SITE_URLS.set(site, restoredUrl)
        if (typeof value === 'object') {
          if (value.query) SITE_QUERIES.set(site, String(value.query))
          if (value.page) SITE_PAGES.set(site, Number(value.page))
        }
      } catch (_) {
        /* tab was closed while the worker slept */
      }
    }
    await persistTabState()
  } catch (_) {
    /* session storage is best effort */
  }
}

const tabStateReady = restoreTabState()

if (typeof chrome !== 'undefined' && chrome.tabs?.onRemoved?.addListener) {
  chrome.tabs.onRemoved.addListener(async (tabId) => {
    let changed = false
    for (const [site, id] of SITE_TABS.entries()) {
      if (id === tabId) {
        SITE_TABS.delete(site)
        SITE_URLS.delete(site)
        SITE_QUERIES.delete(site)
        SITE_PAGES.delete(site)
        changed = true
      }
    }
    if (changed) await persistTabState()
  })
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function samePage(actualUrl, expectedUrl) {
  try {
    const actual = new URL(actualUrl)
    const expected = new URL(expectedUrl)
    if (actual.hostname !== expected.hostname) return false
    if (actual.pathname !== expected.pathname) return false
    for (const key of [
      '_nkw',
      'keyword',
      '_pgn',
      'page',
      's',
      '_sacat',
      '_udhi',
      '_sop',
    ]) {
      const value = expected.searchParams.get(key)
      if (value && actual.searchParams.get(key) !== value) return false
    }
    return true
  } catch (_) {
    return false
  }
}

function siteForTabKey(tabKey) {
  const value = String(tabKey || '').split(':').pop()
  if (value.startsWith('jd')) return 'jd'
  if (value.startsWith('ebay')) return 'ebay'
  return value
}

function scopeKey(site, args = {}) {
  return args.run_id ? `${args.run_id}:${site}` : site
}

function isAllowedSiteUrl(site, url) {
  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'https:') return false
    const host = parsed.hostname
    if (site === 'jd') {
      return host === 'jd.com' || host.endsWith('.jd.com')
    }
    if (site === 'ebay') {
      return host === 'ebay.com' || host.endsWith('.ebay.com')
    }
    return false
  } catch (_) {
    return false
  }
}

export function buildSearchUrl(site, query, options = {}) {
  const baseQuery = String(query || '').trim()
  if (site === 'jd') {
    return `https://search.jd.com/Search?keyword=${encodeURIComponent(baseQuery)}&enc=utf-8`
  }
  if (site === 'ebay') {
    const exclusions = Array.isArray(options.excludeTerms)
      ? options.excludeTerms.join(' ')
      : String(options.excludeTerms || '').trim()
    const keywordText = exclusions ? `${baseQuery} ${exclusions}` : baseQuery
    let url = `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(keywordText)}`
    if (options.categoryId) {
      url += `&_sacat=${encodeURIComponent(options.categoryId)}`
    }
    if (options.maxPrice != null) {
      url += `&_udhi=${encodeURIComponent(options.maxPrice)}`
    }
    if (options.sort === 'price_asc') {
      url += '&_sop=15'
    } else if (options.sort === 'price_desc') {
      url += '&_sop=16'
    }
    return `${url}&_ipg=60`
  }
  throw new Error(`unsupported site ${site}`)
}

function detailUrl(site, sourceProductId, explicitUrl) {
  if (explicitUrl) return explicitUrl
  if (!sourceProductId) return null
  if (site === 'jd') return `https://item.jd.com/${sourceProductId}.html`
  if (site === 'ebay') return `https://www.ebay.com/itm/${sourceProductId}`
  return null
}

function timeoutError(code) {
  const error = new Error(code)
  error.code = code
  return error
}

async function ensureTab(site, url, log, deadline, tabKey = site) {
  if (!isAllowedSiteUrl(site, url)) {
    throw new Error('invalid_site_url')
  }
  const existingId = SITE_TABS.get(tabKey)
  if (existingId) {
    let tab = null
    try {
      tab = await chrome.tabs.get(existingId)
    } catch (_) {
      SITE_TABS.delete(tabKey)
      SITE_URLS.delete(tabKey)
      await persistTabState()
      log.warn('extension.tab', `旧标签失效，重建 ${tabKey}`)
    }
    if (tab) {
      log.debug('extension.tab', `复用标签 ${tabKey}`, { tabId: tab.id, url })
      await chrome.tabs.update(tab.id, { url, active: false })
      await waitComplete(tab.id, url, deadline)
      SITE_URLS.set(tabKey, url)
      await persistTabState()
      return tab.id
    }
  }
  const tab = await chrome.tabs.create({ url, active: false })
  SITE_TABS.set(tabKey, tab.id)
  log.info('extension.tab', `新建标签 ${tabKey}`, { tabId: tab.id, url })
  await waitComplete(tab.id, url, deadline)
  SITE_URLS.set(tabKey, url)
  await persistTabState()
  return tab.id
}

function waitComplete(tabId, expectedUrl, deadline = Date.now() + 30000) {
  return new Promise((resolve, reject) => {
    let settled = false
    const finish = (fn, value) => {
      if (settled) return
      settled = true
      clearInterval(timer)
      fn(value)
    }
    const check = async () => {
      try {
        const tab = await chrome.tabs.get(tabId)
        if (Date.now() > deadline) {
          finish(reject, timeoutError('tab_load_timeout'))
          return
        }
        if (tab.status === 'complete' && samePage(tab.url, expectedUrl)) {
          finish(resolve, tab)
          return
        }
      } catch (error) {
        finish(reject, error)
      }
    }
    const timer = setInterval(check, 250)
    check()
  })
}

async function sendToTab(
  tabId,
  payload,
  { attempts = 8, retry = true, deadline = Date.now() + 30000 } = {},
) {
  const maxAttempts = retry ? attempts : 1
  let lastError
  for (let i = 0; i < maxAttempts; i += 1) {
    if (Date.now() > deadline) {
      throw timeoutError('job_deadline_exceeded')
    }
    try {
      return await chrome.tabs.sendMessage(tabId, {
        source: 'shopping-agent',
        ...payload,
      })
    } catch (error) {
      lastError = error
      if (i + 1 < maxAttempts) await delay(400)
    }
  }
  throw lastError || new Error('content bridge unavailable')
}

function mapBlock(status) {
  if (!status) return null
  if (status === 'login') return 'login_required'
  return status
}

function withLogs(result, log, jobId) {
  return {
    ...result,
    job_id: jobId ?? result.job_id ?? null,
    document_url: result.document_url ?? null,
    page: result.page ?? null,
    extension_logs: log.entries(),
  }
}

function isStaleDocument(response, expectedUrl) {
  return Boolean(
    response?.document_url &&
      expectedUrl &&
      !samePage(response.document_url, expectedUrl),
  )
}

function emptyListingError(result) {
  const items = result?.items || []
  if (result?.status !== 'ok' || items.length > 0) return null
  if (result.no_more || result.no_results) return null
  return 'empty_listing'
}

async function _runBrowserJob(tool, args = {}) {
  await tabStateReady
  const log = createJobLogger(tool)
  const site = String(args.site || 'jd').toLowerCase()
  const jobId = args.job_id ?? null
  const requestedTimeout = Number(args.job_timeout_s)
  const timeoutMs =
    Number.isFinite(requestedTimeout) && requestedTimeout > 0
      ? Math.min(requestedTimeout * 1000, JOB_TIMEOUT_MS)
      : JOB_TIMEOUT_MS
  const deadline = Date.now() + timeoutMs
  log.info('extension.job', `开始任务 ${tool}`, { site, args })

  if (site !== 'jd' && site !== 'ebay') {
    log.error('extension.job', `不支持站点 ${site}`)
    return withLogs(
      {
        status: 'blocked',
        error: 'site_not_implemented',
        site,
      },
      log,
      jobId,
    )
  }

  try {
    if (tool === 'search') {
      const url = buildSearchUrl(site, args.query || '', args)
      const siteKey = scopeKey(site, args)
      const tabId = await ensureTab(
        site,
        url,
        log,
        deadline,
        siteKey,
      )
      await delay(800)
      const probe = await sendToTab(
        tabId,
        { type: 'detect_block' },
        { deadline },
      )
      const block = mapBlock(probe?.block)
      if (block) {
        log.warn('extension.search', `页面阻断 ${block}`, { url, probe })
        return withLogs(
          {
            status: block,
            site,
            query: args.query,
            url,
            document_url: probe?.document_url || url,
            page: args.page || 1,
            mode: 'live',
          },
          log,
          jobId,
        )
      }
      if (isStaleDocument(probe, url)) {
        log.error('extension.search', '搜索页文档不匹配', { url, probe })
        return withLogs(
          {
            status: 'parse_error',
            error: 'stale_document',
            site,
            url,
            document_url: probe?.document_url,
          },
          log,
          jobId,
        )
      }
      SITE_QUERIES.set(siteKey, String(args.query || ''))
      SITE_PAGES.set(siteKey, Number(args.page || 1))
      await persistTabState()
      log.info('extension.search', '搜索页就绪', { url, tabId })
      return withLogs(
        {
          status: 'ok',
          site,
          query: args.query,
          url,
          document_url: probe?.document_url || url,
          page: args.page || 1,
          tab_id: tabId,
          mode: 'live',
        },
        log,
        jobId,
      )
    }

    if (tool === 'scrape_listing') {
      const siteKey = scopeKey(site, args)
      const tabId = SITE_TABS.get(siteKey)
      if (!tabId) {
        log.warn('extension.scrape', '没有活跃搜索标签')
        return withLogs(
          { status: 'blocked', error: 'no_active_search_tab', site },
          log,
          jobId,
        )
      }
      await delay(500)
      const expectedUrl = SITE_URLS.get(siteKey) || args.url || null
      const response = await sendToTab(
        tabId,
        { type: 'parse_listing' },
        { deadline },
      )
      if (!response?.ok) {
        log.error('extension.scrape', '列表解析失败', response)
        return withLogs(
          { status: 'parse_error', error: response?.error || 'parse_failed' },
          log,
          jobId,
        )
      }
      if (isStaleDocument(response, expectedUrl)) {
        log.error('extension.scrape', '列表页文档不匹配', {
          expectedUrl,
          documentUrl: response.document_url,
        })
        return withLogs(
          { status: 'parse_error', error: 'stale_document' },
          log,
          jobId,
        )
      }
      const result = response.result || {}
      const itemCount = (result.items || []).length
      const emptyError = emptyListingError(result)
      if (emptyError) {
        log.error('extension.scrape', '列表为空', { status: result.status })
        return withLogs(
          {
            status: 'parse_error',
            error: emptyError,
            document_url: response.document_url,
          },
          log,
          jobId,
        )
      }
      SITE_PAGES.set(siteKey, Number(result.page || args.page || 1))
      await persistTabState()
      log.info('extension.scrape', `列表解析完成 items=${itemCount}`, {
        status: result.status,
        itemCount,
      })
      return withLogs(
        {
          ...result,
          document_url: response.document_url || expectedUrl,
          page: result.page ?? args.page ?? 1,
          mode: 'live',
        },
        log,
        jobId,
      )
    }

    if (tool === 'next_page') {
      const siteKey = scopeKey(site, args)
      const tabId = SITE_TABS.get(siteKey)
      if (!tabId) {
        return withLogs(
          { status: 'blocked', error: 'no_active_search_tab', site },
          log,
          jobId,
        )
      }
      const meta = await sendToTab(
        tabId,
        { type: 'has_next_page' },
        { deadline },
      )
      if (!meta?.ok) {
        return withLogs(
          { status: 'parse_error', error: meta?.error || 'next_meta_failed' },
          log,
          jobId,
        )
      }
      if (meta.nextUrl) {
        if (!isAllowedSiteUrl(site, meta.nextUrl)) {
          log.error('extension.next', '下一页 URL 不在允许站点', {
            nextUrl: meta.nextUrl,
          })
          return withLogs(
            { status: 'parse_error', error: 'invalid_next_url' },
            log,
            jobId,
          )
        }
        log.info('extension.next', '跳转下一页', { nextUrl: meta.nextUrl })
        await chrome.tabs.update(tabId, { url: meta.nextUrl })
        await waitComplete(tabId, meta.nextUrl, deadline)
        await delay(800)
        const response = await sendToTab(
          tabId,
          { type: 'parse_listing' },
          { deadline },
        )
        if (!response?.ok) {
          return withLogs(
            { status: 'parse_error', error: response?.error || 'parse_failed' },
            log,
            jobId,
          )
        }
        if (isStaleDocument(response, meta.nextUrl)) {
          return withLogs(
            { status: 'parse_error', error: 'stale_document' },
            log,
            jobId,
          )
        }
        const result = response.result || {}
        const emptyError = emptyListingError(result)
        if (emptyError) {
          return withLogs(
            {
              status: 'parse_error',
              error: emptyError,
              document_url: response.document_url,
            },
            log,
            jobId,
          )
        }
        SITE_URLS.set(siteKey, response.document_url || meta.nextUrl)
        SITE_PAGES.set(
          siteKey,
          Number(result.page || args.page || 1),
        )
        await persistTabState()
        return withLogs(
          {
            ...result,
            document_url: response.document_url || meta.nextUrl,
            page: result.page ?? args.page ?? null,
            mode: 'live',
          },
          log,
          jobId,
        )
      }
      if (meta.hasNext) {
        let clicked
        try {
          clicked = await sendToTab(
            tabId,
            { type: 'click_next_page' },
            { retry: false, deadline },
          )
        } catch (error) {
          log.error('extension.next', '点击下一页失败（不重试）', {
            error: String(error?.message || error),
          })
          return withLogs(
            { status: 'blocked', error: 'click_failed' },
            log,
            jobId,
          )
        }
        if (!clicked?.ok) {
          return withLogs(
            { status: 'blocked', error: clicked?.error || 'click_failed' },
            log,
            jobId,
          )
        }
        if (!clicked.moved) {
          return withLogs(
            {
              status: 'ok',
              items: [],
              no_more: true,
              document_url: clicked.document_url,
              page: args.page ?? null,
              mode: 'live',
            },
            log,
            jobId,
          )
        }
        const result = clicked.result || {}
        const emptyError = emptyListingError(result)
        if (emptyError) {
          return withLogs(
            {
              status: 'parse_error',
              error: emptyError,
              document_url: clicked.document_url,
            },
            log,
            jobId,
          )
        }
        if (clicked.document_url) {
          SITE_URLS.set(siteKey, clicked.document_url)
        }
        SITE_PAGES.set(siteKey, Number(result.page || args.page || 1))
        await persistTabState()
        return withLogs(
          {
            ...result,
            document_url: clicked.document_url,
            page: result.page ?? args.page ?? null,
            mode: 'live',
          },
          log,
          jobId,
        )
      }
      log.info('extension.next', '没有更多页')
      return withLogs(
        {
          status: 'ok',
          items: [],
          no_more: true,
          document_url: SITE_URLS.get(siteKey) || null,
          page: args.page ?? null,
          mode: 'live',
        },
        log,
        jobId,
      )
    }

    if (tool === 'fetch_detail') {
      const url = detailUrl(site, args.source_product_id, args.url)
      if (!url) {
        return withLogs(
          { status: 'parse_error', error: 'missing_detail_url' },
          log,
          jobId,
        )
      }
      if (!isAllowedSiteUrl(site, url)) {
        log.error('extension.detail', '详情 URL 不在允许站点', { url })
        return withLogs(
          { status: 'parse_error', error: 'invalid_detail_url' },
          log,
          jobId,
        )
      }
      // Parallel fetch_detail must not share one tab — otherwise jobs race and
      // parse whichever page last navigated (often empty / mid-load).
      const detailKey = `${site}-detail:${args.source_product_id || args.product_id || url}`
      const tabId = await ensureTab(
        site,
        url,
        log,
        deadline,
        scopeKey(detailKey, args),
      )
      await delay(1200)
      let response = await sendToTab(
        tabId,
        { type: 'parse_detail' },
        { deadline },
      )
      // SPA detail pages often hydrate after "complete"; retry briefly.
      if (
        response?.ok &&
        response?.result?.status === 'parse_error' &&
        response?.result?.error === 'detail_not_found'
      ) {
        for (let i = 0; i < 4 && Date.now() < deadline; i += 1) {
          await delay(700)
          response = await sendToTab(
            tabId,
            { type: 'parse_detail' },
            { deadline },
          )
          if (
            !(
              response?.ok &&
              response?.result?.status === 'parse_error' &&
              response?.result?.error === 'detail_not_found'
            )
          ) {
            break
          }
        }
      }
      if (!response?.ok) {
        log.error('extension.detail', '详情解析失败', response)
        return withLogs(
          { status: 'parse_error', error: response?.error || 'detail_failed' },
          log,
          jobId,
        )
      }
      if (isStaleDocument(response, url)) {
        log.error('extension.detail', '详情页文档不匹配', {
          url,
          documentUrl: response.document_url,
        })
        return withLogs(
          {
            status: 'parse_error',
            error: 'stale_document',
            document_url: response.document_url,
          },
          log,
          jobId,
        )
      }
      log.info('extension.detail', '详情解析完成', {
        title: response.result?.title,
        price: response.result?.price,
      })
      return withLogs(
        {
          ...response.result,
          source_product_id: args.source_product_id,
          url,
          document_url: response.document_url || url,
          mode: 'live',
        },
        log,
        jobId,
      )
    }

    return withLogs(
      { status: 'parse_error', error: `unknown tool ${tool}` },
      log,
      jobId,
    )
  } catch (error) {
    const code = String(error?.code || error?.message || error)
    const errorCode =
      code === 'tab_load_timeout' || code === 'job_deadline_exceeded'
        ? code
        : String(error?.message || error)
    log.error('extension.job', `任务异常 ${tool}`, { error: code })
    return withLogs(
      {
        status: 'blocked',
        error: errorCode,
        mode: 'live',
      },
      log,
      jobId,
    )
  }
}

const jobQueues = new Map()

export async function runBrowserJob(tool, args = {}) {
  const key = `${args.run_id || 'default'}:${args.site || 'jd'}`
  const previous = jobQueues.get(key) || Promise.resolve()
  const run = previous
    .catch(() => {})
    .then(() => _runBrowserJob(tool, args))
  const tail = run.catch(() => {})
  jobQueues.set(key, tail)
  try {
    return await run
  } finally {
    if (jobQueues.get(key) === tail) jobQueues.delete(key)
  }
}
