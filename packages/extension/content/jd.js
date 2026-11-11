;(function (root) {
  if (typeof module === 'object' && module.exports) {
    module.exports = require('../adapters/jd/index.js')
    return
  }
  if (!root.__saJd) throw new Error('JD adapter pack was not loaded')
})(typeof globalThis !== 'undefined' ? globalThis : this)
