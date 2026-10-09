const config = require('../config.js')

// callContainer 响应体字段随基础库版本有 result/data 两种，统一取出并解析
function bodyOf(res) {
  let b = res.result !== undefined ? res.result : res.data
  if (typeof b === 'string') { try { b = JSON.parse(b) } catch (e) {} }
  return b
}

// 云托管官方通道 wx.cloud.callContainer：免服务器域名配置，体验版/正式版直连
function request(path, method = 'GET', data = {}, needAuth = true) {
  return new Promise((resolve, reject) => {
    const header = { 'content-type': 'application/json', 'X-WX-SERVICE': config.CLOUD_SERVICE }
    const token = wx.getStorageSync('token')
    if (needAuth && token) header['Authorization'] = 'Bearer ' + token
    wx.cloud.callContainer({
      config: { env: config.CLOUD_ENV },
      path, method, header, data,
      success: res => {
        const body = bodyOf(res)
        if (res.statusCode === 401) {
          wx.removeStorageSync('token')
          wx.reLaunch({ url: '/pages/login/login' })
          reject({ statusCode: 401, data: body }); return
        }
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(body)
        else reject({ statusCode: res.statusCode, data: body })
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
