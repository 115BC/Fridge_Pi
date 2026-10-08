const config = require('../config.js')

function request(path, method = 'GET', data = {}, needAuth = true) {
  return new Promise((resolve, reject) => {
    const header = { 'content-type': 'application/json' }
    const token = wx.getStorageSync('token')
    if (needAuth && token) header['Authorization'] = 'Bearer ' + token
    wx.request({
      url: config.BASE_URL + path, method, data, header,
      success: res => {
        if (res.statusCode === 401) {
          wx.removeStorageSync('token')
          wx.reLaunch({ url: '/pages/login/login' })
          reject(res); return
        }
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data)
        else reject(res)
      },
      fail: reject
    })
  })
}

module.exports = {
  request,
  get: (p, d) => request(p, 'GET', d),
  post: (p, d) => request(p, 'POST', d)
}
