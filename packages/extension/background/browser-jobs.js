import { loadConfig } from '../shared/config.js'
import { createJobLogger } from '../shared/logger.js'

const SITE_TABS = new Map()

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function searchUrl(site, query) {
  if (site === 'jd') {
    return `https://search.jd.com/Search?keyword=${encodeURIComponent(query || '')}&enc=utf-8`
  }
  if (site === 'ebay') {
    return `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(query || '')}&_ipg=60`
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

async function ensureTab(site, url, log) {
  const existingId = SITE_TABS.get(site)
  if (existingId) {
    try {
      const tab = await chrome.tabs.get(existingId)
      log.debug('extension.tab', `复用标签 ${site}`, { tabId: tab.id, url })
      await chrome.tabs.update(tab.id, { url, active: false })
      await waitComplete(tab.id)
      return tab.id
    } catch (_) {
      SITE_TABS.delete(site)
      log.warn('extension.tab', `旧标签失效，重建 ${site}`)
    }
  }
  const tab = await chrome.tabs.create({ url, active: false })
  SITE_TABS.set(site, tab.id)
  log.info('extension.tab', `新建标签 ${site}`, { tabId: tab.id, url })
  await waitComplete(tab.id)
  return tab.id
}

function waitComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const started = Date.now()
    const timer = setInterval(async () => {
      try {
        const tab = await chrome.tabs.get(tabId)
        if (tab.status === 'complete') {
          clearInterval(timer)
          resolve(tab)
          return
        }
        if (Date.now() - started > timeoutMs) {
          clearInterval(timer)
          reject(new Error('tab load timeout'))
        }
      } catch (error) {
        clearInterval(timer)
        reject(error)
      }
    }, 250)
  })
}

async function sendToTab(tabId, payload, attempts = 8) {
  let lastError
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await chrome.tabs.sendMessage(tabId, {
        source: 'shopping-agent',
        ...payload,
      })
    } catch (error) {
      lastError = error
      await delay(400)
    }
  }
  throw lastError || new Error('content bridge unavailable')
}

function mapBlock(status) {
  if (!status) return null
  if (status === 'login') return 'login_required'
  return status
}

function withLogs(result, log) {
  return {
    ...result,
    extension_logs: log.entries(),
  }
}

export async function runBrowserJob(tool, args = {}) {
  const log = createJobLogger(tool)
  const site = String(args.site || 'jd').toLowerCase()
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
    )
  }

  try {
    if (tool === 'search') {
      const url = searchUrl(site, args.query || '')
      const tabId = await ensureTab(site, url, log)
      await delay(800)
      const probe = await sendToTab(tabId, { type: 'detect_block' })
      const block = mapBlock(probe?.block)
      if (block) {
        log.warn('extension.search', `页面阻断 ${block}`, { url, probe })
        return withLogs({ status: block, site, query: args.query, url, mode: 'live' }, log)
      }
      log.info('extension.search', '搜索页就绪', { url, tabId })
      return withLogs(
        { status: 'ok', site, query: args.query, url, tab_id: tabId, mode: 'live' },
        log,
      )
    }

    if (tool === 'scrape_listing') {
      const tabId = SITE_TABS.get(site)
      if (!tabId) {
        log.warn('extension.scrape', '没有活跃搜索标签')
        return withLogs({ status: 'blocked', error: 'no_active_search_tab', site }, log)
      }
      await delay(500)
      const response = await sendToTab(tabId, { type: 'parse_listing' })
      if (!response?.ok) {
        log.error('extension.scrape', '列表解析失败', response)
        return withLogs(
          { status: 'parse_error', error: response?.error || 'parse_failed' },
          log,
        )
      }
      const itemCount = (response.result?.items || []).length
      log.info('extension.scrape', `列表解析完成 items=${itemCount}`, {
        status: response.result?.status,
        itemCount,
      })
      return withLogs({ ...response.result, mode: 'live' }, log)
    }

    if (tool === 'next_page') {
      const tabId = SITE_TABS.get(site)
      if (!tabId) {
        return withLogs({ status: 'blocked', error: 'no_active_search_tab', site }, log)
      }
      const meta = await sendToTab(tabId, { type: 'has_next_page' })
      if (!meta?.ok) {
        return withLogs(
          { status: 'parse_error', error: meta?.error || 'next_meta_failed' },
          log,
        )
      }
      if (meta.nextUrl) {
        log.info('extension.next', '跳转下一页', { nextUrl: meta.nextUrl })
        await chrome.tabs.update(tabId, { url: meta.nextUrl })
        await waitComplete(tabId)
        await delay(800)
        const response = await sendToTab(tabId, { type: 'parse_listing' })
        if (!response?.ok) {
          return withLogs(
            { status: 'parse_error', error: response?.error || 'parse_failed' },
            log,
          )
        }
        return withLogs({ ...response.result, mode: 'live' }, log)
      }
      if (meta.hasNext) {
        const clicked = await sendToTab(tabId, { type: 'click_next_page' })
        if (!clicked?.ok) {
          return withLogs(
            { status: 'parse_error', error: clicked?.error || 'click_failed' },
            log,
          )
        }
        if (!clicked.moved) {
          return withLogs({ status: 'ok', items: [], no_more: true, mode: 'live' }, log)
        }
        return withLogs({ ...clicked.result, mode: 'live' }, log)
      }
      log.info('extension.next', '没有更多页')
      return withLogs({ status: 'ok', items: [], no_more: true, mode: 'live' }, log)
    }

    if (tool === 'fetch_detail') {
      const url = detailUrl(site, args.source_product_id, args.url)
      if (!url) {
        return withLogs({ status: 'parse_error', error: 'missing_detail_url' }, log)
      }
      const tabId = await ensureTab(`${site}-detail`, url, log)
      await delay(800)
      const response = await sendToTab(tabId, { type: 'parse_detail' })
      if (!response?.ok) {
        log.error('extension.detail', '详情解析失败', response)
        return withLogs(
          { status: 'parse_error', error: response?.error || 'detail_failed' },
          log,
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
          mode: 'live',
        },
        log,
      )
    }

    return withLogs({ status: 'parse_error', error: `unknown tool ${tool}` }, log)
  } catch (error) {
    log.error('extension.job', `任务异常 ${tool}`, {
      error: String(error?.message || error),
    })
    return withLogs(
      {
        status: 'blocked',
        error: String(error?.message || error),
        mode: 'live',
      },
      log,
    )
  } finally {
    await loadConfig()
  }
}
