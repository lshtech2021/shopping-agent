import { loadConfig } from '../shared/config.js'

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

async function ensureTab(site, url) {
  const existingId = SITE_TABS.get(site)
  if (existingId) {
    try {
      const tab = await chrome.tabs.get(existingId)
      await chrome.tabs.update(tab.id, { url, active: false })
      await waitComplete(tab.id)
      return tab.id
    } catch (_) {
      SITE_TABS.delete(site)
    }
  }
  const tab = await chrome.tabs.create({ url, active: false })
  SITE_TABS.set(site, tab.id)
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

export async function runBrowserJob(tool, args = {}) {
  const site = String(args.site || 'jd').toLowerCase()
  if (site !== 'jd' && site !== 'ebay') {
    return {
      status: 'blocked',
      error: 'site_not_implemented',
      site,
    }
  }

  try {
    if (tool === 'search') {
      const url = searchUrl(site, args.query || '')
      const tabId = await ensureTab(site, url)
      await delay(800)
      const probe = await sendToTab(tabId, { type: 'detect_block' })
      const block = mapBlock(probe?.block)
      if (block) return { status: block, site, query: args.query, url, mode: 'live' }
      return { status: 'ok', site, query: args.query, url, tab_id: tabId, mode: 'live' }
    }

    if (tool === 'scrape_listing') {
      const tabId = SITE_TABS.get(site)
      if (!tabId) {
        return { status: 'blocked', error: 'no_active_search_tab', site }
      }
      await delay(500)
      const response = await sendToTab(tabId, { type: 'parse_listing' })
      if (!response?.ok) {
        return { status: 'parse_error', error: response?.error || 'parse_failed' }
      }
      return { ...response.result, mode: 'live' }
    }

    if (tool === 'next_page') {
      const tabId = SITE_TABS.get(site)
      if (!tabId) {
        return { status: 'blocked', error: 'no_active_search_tab', site }
      }
      const meta = await sendToTab(tabId, { type: 'has_next_page' })
      if (!meta?.ok) {
        return { status: 'parse_error', error: meta?.error || 'next_meta_failed' }
      }
      if (meta.nextUrl) {
        await chrome.tabs.update(tabId, { url: meta.nextUrl })
        await waitComplete(tabId)
        await delay(800)
        const response = await sendToTab(tabId, { type: 'parse_listing' })
        if (!response?.ok) {
          return { status: 'parse_error', error: response?.error || 'parse_failed' }
        }
        return { ...response.result, mode: 'live' }
      }
      if (meta.hasNext) {
        const clicked = await sendToTab(tabId, { type: 'click_next_page' })
        if (!clicked?.ok) {
          return { status: 'parse_error', error: clicked?.error || 'click_failed' }
        }
        if (!clicked.moved) {
          return { status: 'ok', items: [], no_more: true, mode: 'live' }
        }
        return { ...clicked.result, mode: 'live' }
      }
      return { status: 'ok', items: [], no_more: true, mode: 'live' }
    }

    if (tool === 'fetch_detail') {
      const url = detailUrl(site, args.source_product_id, args.url)
      if (!url) {
        return { status: 'parse_error', error: 'missing_detail_url' }
      }
      const tabId = await ensureTab(`${site}-detail`, url)
      await delay(800)
      const response = await sendToTab(tabId, { type: 'parse_detail' })
      if (!response?.ok) {
        return { status: 'parse_error', error: response?.error || 'detail_failed' }
      }
      return {
        ...response.result,
        source_product_id: args.source_product_id,
        url,
        mode: 'live',
      }
    }

    return { status: 'parse_error', error: `unknown tool ${tool}` }
  } catch (error) {
    return {
      status: 'blocked',
      error: String(error?.message || error),
      mode: 'live',
    }
  } finally {
    await loadConfig()
  }
}
