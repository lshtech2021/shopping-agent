;(function () {
  const jd = globalThis.__saJd
  if (!jd) {
    console.warn('[shopping-agent] jd parser missing')
    return
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || message.source !== 'shopping-agent') return false

    ;(async () => {
      try {
        if (message.type === 'detect_block') {
          sendResponse({
            ok: true,
            block: jd.detectBlock(document, location.href),
          })
          return
        }
        if (message.type === 'parse_listing') {
          sendResponse({ ok: true, result: jd.parseListing(document, location.href) })
          return
        }
        if (message.type === 'parse_detail') {
          sendResponse({ ok: true, result: jd.parseDetail(document, location.href) })
          return
        }
        if (message.type === 'has_next_page') {
          sendResponse({
            ok: true,
            hasNext: jd.hasNextPage(document),
            nextUrl: jd.nextPageUrl(document, location.href),
          })
          return
        }
        if (message.type === 'click_next_page') {
          const moved = await jd.clickNextPage(document)
          sendResponse({
            ok: true,
            moved,
            result: jd.parseListing(document, location.href),
          })
          return
        }
        sendResponse({ ok: false, error: `unknown type ${message.type}` })
      } catch (error) {
        sendResponse({ ok: false, error: String(error?.message || error) })
      }
    })()

    return true
  })
})()
