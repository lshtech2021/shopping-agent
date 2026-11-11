;(function (root) {
  if (typeof module === 'object' && module.exports) {
    module.exports = require('../adapters/ebay/index.js')
    return
  }
  if (!root.__saEbay) throw new Error('eBay adapter pack was not loaded')
})(typeof globalThis !== 'undefined' ? globalThis : this)
