const config = require('./config.js')

App({
  globalData: { role: '', user: null },
  onLaunch() {
    if (wx.cloud) wx.cloud.init({ env: config.CLOUD_ENV, traceUser: true })
    this.globalData.role = wx.getStorageSync('role') || ''
    this.globalData.user = wx.getStorageSync('user') || null
  },
  // 各页面 onShow 调用：未登录跳登录页；已登录但角色不符则回各自首页
  guard(expectRoles) {
    const token = wx.getStorageSync('token')
    const role = wx.getStorageSync('role')
    if (!token) { wx.reLaunch({ url: '/pages/login/login' }); return false }
    if (expectRoles && expectRoles.length && expectRoles.indexOf(role) < 0) {
      const home = { student: '/pages/student/student', manager: '/pages/manager/manager', admin: '/pages/manager/manager' }[role]
      if (home) wx.reLaunch({ url: home })
      return false
    }
    return true
  },
  logout() {
    wx.clearStorageSync()
    wx.reLaunch({ url: '/pages/login/login' })
  }
})
