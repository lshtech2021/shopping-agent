;(function () {
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.source !== 'shopping-agent') return false

    const reply = (payload) =>
      sendResponse({ ...payload, document_url: location.href })

    ;(async () => {
      try {
        const adapter = globalThis.__saAdapters?.getAdapterByUrl(location.href)
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
        if (message.type === 'detect_currency') {
          reply({
            ok: true,
            currency: adapter.detectCurrency(document, location.href),
          })
          return
        }
        if (message.type === 'parse_listing') {
          if (adapter.matchesUrl && !adapter.matchesUrl(location.href)) {
            reply({
              ok: true,
              result: {
                status: 'parse_error',
                error: 'not_a_listing_page',
                items: [],
              },
            })
            return
          }
          const ready = adapter.waitForListing
            ? await adapter.waitForListing(document, location.href)
            : true
          reply({
            ok: true,
            result: ready
              ? adapter.parseListing(document, location.href)
              : {
                  status: 'parse_error',
                  error: 'listing_not_ready',
                  items: [],
                },
          })
          return
        }
        if (message.type === 'parse_detail') {
          const ready = adapter.waitForDetail
            ? await adapter.waitForDetail(document, location.href)
            : true
          reply({
            ok: true,
            result: ready
              ? adapter.parseDetail(document, location.href)
              : { status: 'parse_error', error: 'detail_not_found' },
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
