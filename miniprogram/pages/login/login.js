const api = require('../../utils/api.js')
const app = getApp()

Page({
  data: { name: '', room: '', loading: false, needBind: false },
  onLoad() {
    // 已登录直接进对应首页
    if (wx.getStorageSync('token') && wx.getStorageSync('role')) {
      this.goto(wx.getStorageSync('role'))
    }
  },
  onName(e) { this.setData({ name: e.detail.value }) },
  onRoom(e) { this.setData({ room: e.detail.value }) },
  goto(role) {
    const home = { student: '/pages/student/student', manager: '/pages/manager/manager', admin: '/pages/manager/manager' }[role]
    wx.reLaunch({ url: home || '/pages/student/student' })
  },
  // 第一步：纯微信登录；服务器发现未绑定会回 NEED_BIND，转入绑定表单
  doLogin() {
    if (this.data.loading) return
    this.setData({ loading: true })
    wx.login({
      success: res => {
        api.post('/api/v1/wx/login', { code: res.code }, false)
          .then(d => { this.saveAndGo(d) })
          .catch(e => {
            if (e.data && e.data.detail && e.data.detail.indexOf('NEED_BIND') === 0) {
              this.setData({ needBind: true })
            } else {
              wx.showToast({ title: '登录失败:' + (e.data && e.data.detail || e.errMsg || JSON.stringify(e).slice(0, 90)), icon: 'none' })
            }
          })
          .finally(() => this.setData({ loading: false }))
      },
      fail: () => { this.setData({ loading: false }); wx.showToast({ title: 'wx.login 失败', icon: 'none' }) }
    })
  },
  // 第二步：绑定姓名+房间号（code 一次性，需重新取）
  doBind() {
    if (this.data.loading) return
    const name = this.data.name.trim(), room = this.data.room.trim()
    if (!name) { wx.showToast({ title: '请填写姓名', icon: 'none' }); return }
    if (!room) { wx.showToast({ title: '请填写房间号', icon: 'none' }); return }
    this.setData({ loading: true })
    wx.login({
      success: res => {
        api.post('/api/v1/wx/login', { code: res.code, name, room }, false)
          .then(d => { this.saveAndGo(d) })
          .catch(e => { wx.showToast({ title: '绑定失败:' + (e.data && e.data.detail || e.errMsg || JSON.stringify(e).slice(0, 80)), icon: 'none' }) })
          .finally(() => this.setData({ loading: false }))
      },
      fail: () => { this.setData({ loading: false }); wx.showToast({ title: 'wx.login 失败', icon: 'none' }) }
    })
  },
  saveAndGo(d) {
    wx.setStorageSync('token', d.token)
    wx.setStorageSync('role', d.role)
    wx.setStorageSync('user', d.user)
    app.globalData.role = d.role; app.globalData.user = d.user
    wx.showToast({ title: '登录成功', icon: 'success' })
    this.goto(d.role)
  }
})
