import { createJobLogger } from '../shared/logger.js'
import { patchSessionSettings } from '../shared/api.js'
import '../adapters/jd/index.js'
import '../adapters/ebay/index.js'
import '../adapters/registry.js'
import './browse-guards.js'

const { getAdapter, getAdapterByUrl } = globalThis.__saAdapters
const { assertBrowseAllowed } = globalThis.__saBrowseGuards

const SITE_TABS = new Map()
const SITE_URLS = new Map()
const SITE_QUERIES = new Map()
const SITE_PAGES = new Map()
const SESSION_SETTINGS = new Map()
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
        const allowedSite = siteForTabKey(site, tab.url)
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

function siteForTabKey(tabKey, url) {
  const value = String(tabKey || '').split(':').pop()
  const adapter = getAdapterByUrl(url)
  return adapter && value.startsWith(adapter.id) ? adapter.id : null
}

function scopeKey(site, args = {}) {
  return args.run_id ? `${args.run_id}:${site}` : site
}

function isAllowedSiteUrl(site, url) {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && getAdapter(site).matchUrl(url)
  } catch (_) {
    return false
  }
}

export function buildSearchUrl(site, query, options = {}) {
  return getAdapter(site).buildSearchUrl(query, options)
}

function detailUrl(site, sourceProductId, explicitUrl) {
  if (explicitUrl) return explicitUrl
  return getAdapter(site).detailUrl(sourceProductId)
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

const PARSE_RETRY_DELAYS_MS = [500, 1000]

function isTransientParseError(error) {
  return [
    'listing_not_ready',
    'empty_listing',
    'parse_failed',
    'detail_not_found',
  ].includes(String(error || ''))
}

async function parseListingWithRetry(tabId, expectedUrl, deadline) {
  let response = null
  let result = null
  for (let attempt = 0; attempt <= PARSE_RETRY_DELAYS_MS.length; attempt += 1) {
    response = await sendToTab(tabId, { type: 'parse_listing' }, { deadline })
    if (!response?.ok) return { response, result: null, stale: false }
    if (isStaleDocument(response, expectedUrl)) {
      return { response, result: null, stale: true }
    }
    result = response.result || {}
    if (!emptyListingError(result) && !isTransientParseError(result.error)) {
      return { response, result, stale: false }
    }
    if (attempt < PARSE_RETRY_DELAYS_MS.length) {
      await delay(PARSE_RETRY_DELAYS_MS[attempt])
    }
  }
  return { response, result, stale: false }
}

async function parseDetailWithRetry(tabId, deadline, attempts = 3) {
  let response = null
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (Date.now() > deadline) throw timeoutError('job_deadline_exceeded')
    response = await sendToTab(tabId, { type: 'parse_detail' }, { deadline })
    if (!response?.ok) return response
    if (!isTransientParseError(response.result?.error)) return response
    if (attempt + 1 < attempts) await delay(600 * (attempt + 1))
  }
  return response
}

function sessionSettings(args) {
  const runId = args.run_id || null
  if (args.settings && typeof args.settings === 'object') {
    const value = { ...args.settings }
    if (runId) SESSION_SETTINGS.set(runId, value)
    return value
  }
  return runId ? SESSION_SETTINGS.get(runId) || null : null
}

async function waitForListingReady(tabId, args, deadline, log) {
  // Check the tab/site before messaging it. This preserves the fail-closed
  // site guard even when the listing wait is enabled.
  const siteRefused = await guardTab(tabId, args, deadline, {
    skipCurrency: true,
  })
  if (siteRefused) return siteRefused
  try {
    const ready = await sendToTab(
      tabId,
      { type: 'wait_listing' },
      { deadline },
    )
    if (ready?.ok === false) {
      log.debug('extension.search', '列表等待不可用，继续检查页面', ready)
    }
  } catch (error) {
    // An older content script may not understand wait_listing. Currency
    // detection below still provides the fail-closed guard.
    log.debug('extension.search', '列表等待失败，继续检查页面', {
      error: error?.message || String(error),
    })
  }
  return null
}

async function guardTab(tabId, args, deadline, { skipCurrency = false } = {}) {
  const settings = sessionSettings(args)
  // Legacy/local callers without a session cannot provide a meaningful
  // currency precondition. Production jobs always include session settings.
  if (!settings) return null

  const tab = await chrome.tabs.get(tabId)
  const siteCheck = assertBrowseAllowed({
    sessionSite: settings.site,
    sessionCurrency: settings.currency || 'SITE_CHECK',
    tabUrl: tab?.url,
    pageCurrency: settings.currency || 'SITE_CHECK',
  })
  if (!siteCheck.ok && siteCheck.error !== 'currency_mismatch') {
    return { status: 'blocked', ...siteCheck }
  }
  if (skipCurrency) {
    // Detail pages are evidence pages. eBay may show a different display
    // currency there; the backend keeps the listing price/currency and flags
    // the detail currency instead of blocking specification extraction.
    return null
  }

  const probe = await sendToTab(
    tabId,
    { type: 'detect_block' },
    { deadline },
  )
  const block = mapBlock(probe?.block)
  if (block) {
    return {
      status: block,
      error: probe?.block || block,
      document_url: probe?.document_url || tab?.url,
    }
  }

  const detected = await sendToTab(
    tabId,
    { type: 'detect_currency' },
    { deadline },
  )
  const pageCurrency = detected?.ok ? detected.currency : null
  if (!pageCurrency) {
    if (!settings.currency) {
      return {
        status: 'blocked',
        error: 'currency_unknown',
        page_currency: null,
        session_currency: null,
      }
    }
    // No currency signal on this page (for example a zero-result page). The
    // product parser still records each item's currency and backend
    // eligibility rejects cross-currency items, so do not deadlock here.
    return null
  }
  if (!settings.currency) {
    if (!args.run_id) {
      return {
        status: 'blocked',
        error: 'currency_mismatch',
        page_currency: pageCurrency,
        session_currency: null,
      }
    }
    try {
      await patchSessionSettings(args.run_id, {
        currency: pageCurrency,
        currency_source: 'detected',
      })
    } catch (_) {
      return {
        status: 'blocked',
        error: 'settings_sync_failed',
        page_currency: pageCurrency,
        session_currency: null,
      }
    }
    settings.currency = pageCurrency
    settings.currency_source = 'detected'
    SESSION_SETTINGS.set(args.run_id, settings)
  }

  const allowed = assertBrowseAllowed({
    sessionSite: settings.site,
    sessionCurrency: settings.currency,
    tabUrl: tab?.url || detected?.document_url,
    pageCurrency,
  })
  return allowed.ok ? null : { status: 'blocked', ...allowed }
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

  let adapter
  try {
    adapter = getAdapter(site)
  } catch (error) {
    log.error('extension.job', `不支持站点 ${site}`)
    return withLogs(
      {
        status: 'blocked',
        error: error.code || 'adapter_missing',
        site,
      },
      log,
      jobId,
    )
  }

  try {
    if (tool === 'search') {
      const url = adapter.buildSearchUrl(args.query || '', args)
      const siteKey = scopeKey(site, args)
      const tabId = await ensureTab(
        site,
        url,
        log,
        deadline,
        siteKey,
      )
      const siteRefused = await waitForListingReady(
        tabId,
        args,
        deadline,
        log,
      )
      if (siteRefused) return withLogs(siteRefused, log, jobId)
      const refused = await guardTab(tabId, args, deadline)
      if (refused) return withLogs(refused, log, jobId)
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
      const expectedUrl = SITE_URLS.get(siteKey) || args.url || null
      const refused = await guardTab(tabId, args, deadline)
      if (refused) return withLogs(refused, log, jobId)
      const parsed = await parseListingWithRetry(tabId, expectedUrl, deadline)
      const response = parsed.response
      if (!response?.ok) {
        log.error('extension.scrape', '列表解析失败', response)
        return withLogs(
          { status: 'parse_error', error: response?.error || 'parse_failed' },
          log,
          jobId,
        )
      }
      if (parsed.stale) {
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
      const result = parsed.result || {}
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
        resultCount: result.result_count ?? null,
        pageItemCount: result.page_item_count ?? null,
        fallbackItemCount: result.fallback_item_count ?? 0,
        firstTitle: result.items?.[0]?.title || null,
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
      const refused = await guardTab(tabId, args, deadline)
      if (refused) return withLogs(refused, log, jobId)
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
        const siteRefused = await waitForListingReady(
          tabId,
          args,
          deadline,
          log,
        )
        if (siteRefused) return withLogs(siteRefused, log, jobId)
        const nextRefused = await guardTab(tabId, args, deadline)
        if (nextRefused) return withLogs(nextRefused, log, jobId)
        const parsed = await parseListingWithRetry(
          tabId,
          meta.nextUrl,
          deadline,
        )
        const response = parsed.response
        if (!response?.ok) {
          return withLogs(
            { status: 'parse_error', error: response?.error || 'parse_failed' },
            log,
            jobId,
          )
        }
        if (parsed.stale) {
          return withLogs(
            { status: 'parse_error', error: 'stale_document' },
            log,
            jobId,
          )
        }
        const result = parsed.result || {}
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
      const refused = await guardTab(tabId, args, deadline, {
        skipCurrency: true,
      })
      if (refused) return withLogs(refused, log, jobId)
      // The content bridge waits for detail hydration before parsing; retry
      // only when eBay still reports a transient "not found" state.
      const response = await parseDetailWithRetry(tabId, deadline)
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
