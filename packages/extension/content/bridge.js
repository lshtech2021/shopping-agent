;(function () {
  function adapterForHost(hostname) {
    if (hostname.includes('jd.com')) return globalThis.__saJd || null
    if (hostname.includes('ebay.com')) return globalThis.__saEbay || null
    return null
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.source !== 'shopping-agent') return false

    const reply = (payload) =>
      sendResponse({ ...payload, document_url: location.href })

    ;(async () => {
      try {
        const adapter = adapterForHost(location.hostname)
        if (!adapter) {
          reply({ ok: false, error: `no adapter for ${location.hostname}` })
          return
        }

        if (message.type === 'detect_block') {
          reply({
            ok: true,
            block: adapter.detectBlock(document, location.href),
          })
          return
        }
        if (message.type === 'parse_listing') {
          reply({
            ok: true,
            result: adapter.parseListing(document, location.href),
          })
          return
        }
        if (message.type === 'parse_detail') {
          reply({
            ok: true,
            result: adapter.parseDetail(document, location.href),
          })
          return
        }
        if (message.type === 'has_next_page') {
          reply({
            ok: true,
            hasNext: adapter.hasNextPage(document, location.href),
            nextUrl: adapter.nextPageUrl(document, location.href),
          })
          return
        }
        if (message.type === 'click_next_page') {
          const moved = await adapter.clickNextPage(document)
          reply({
            ok: true,
            moved,
            result: adapter.parseListing(document, location.href),
          })
          return
        }
        reply({ ok: false, error: `unknown type ${message.type}` })
      } catch (error) {
        reply({ ok: false, error: String(error?.message || error) })
      }
    })()

    return true
  })
})()
