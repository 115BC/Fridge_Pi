const api = require('../../utils/api.js')
const colorUtil = require('../../utils/color.js')
const config = require('../../config.js')
const app = getApp()

Page({
  data: {
    role: '', user: null,
    items: [],
    unlockTip: '', unlockColor: '',
    qrShow: false, qr: null,
    adminContact: '', notices: []
  },
  onShow() {
    if (!app.guard(['student'])) return
    this.setData({ role: wx.getStorageSync('role'), user: wx.getStorageSync('user') })
    this.loadMine()
    this.loadNotices()
    api.request('/api/v1/buildings', 'GET', {}, false)
      .then(d => this.setData({ adminContact: d.admin_contact || '' }))
      .catch(() => {})
  },
  loadNotices() {
    const LABEL = { sent: '已推送微信', skipped: '未送达(未订阅/额度用尽)', failed: '发送失败' }
    return api.get('/api/v1/notifications/mine').then(d => {
      const notices = d.notifications.map(n => Object.assign(n, {
        status_label: LABEL[n.status] || n.status
      }))
      this.setData({ notices })
    }).catch(() => {})
  },
  onPullDownRefresh() { this.loadMine().then(() => wx.stopPullDownRefresh()) },

  // 修改姓名/寝室号（楼宇改动需超管，见下方提示）
  editProfile() {
    const u = this.data.user || {}
    wx.showModal({
      title: '第1步：修改姓名', editable: true, placeholderText: u.name || '输入姓名',
      success: r1 => {
        if (!r1.confirm) return
        const name = (r1.content || '').trim() || u.name
        wx.showModal({
          title: '第2步：修改寝室号', editable: true, placeholderText: u.room || '如 2-417',
          success: r2 => {
            if (!r2.confirm) return
            const room = (r2.content || '').trim() || u.room
            if (!name || !room) return wx.showToast({ title: '姓名和寝室号不能为空', icon: 'none' })
            api.put('/api/v1/me', { name, room }).then(d => {
              wx.setStorageSync('user', d.user)
              this.setData({ user: d.user })
              wx.showToast({ title: '资料已更新', icon: 'success' })
            }).catch(err => wx.showToast({ title: '保存失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
          }
        })
      }
    })
  },
  copyContact() {
    if (!this.data.adminContact) return
    wx.setClipboardData({ data: this.data.adminContact })
  },
  loadMine() {
    return api.get('/api/v1/items/mine').then(d => {
      const items = d.items.map(it => Object.assign(it, {
        color_label: colorUtil.labelOf(it.color),
        vol_l: (it.vol_est_ml / 1000).toFixed(1)
      }))
      this.setData({ items })
    }).catch(() => {})
  },
  logout() { app.logout() },

  // 冰箱触控屏扫码登录：屏幕选「存入」出码后，来这里扫码确认身份
  scanLogin() {
    this.askSub()
    wx.scanCode({
      success: res => {
        const m = (res.result || '').match(/fridge-login:([0-9a-zA-Z]+)/)
        if (!m) return wx.showToast({ title: '这不是屏幕登录码', icon: 'none' })
        api.post('/api/v1/kiosk/login/confirm', { ticket: m[1] }).then(d => {
          wx.showToast({ title: '已登录冰箱屏幕(' + d.name + ')，可在屏幕上登记', icon: 'none' })
        }).catch(err => wx.showToast({ title: '登录失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
      },
      fail: () => {}
    })
  },

  // 到期提醒订阅（登记在屏幕完成，订阅入口放在这里）
  subscribeRemind() {
    if (!config.REMIND_TEMPLATE_ID) return wx.showToast({ title: '未配置提醒模板', icon: 'none' })
    wx.requestSubscribeMessage({
      tmplIds: [config.REMIND_TEMPLATE_ID],
      success: () => wx.showToast({ title: '已开启到期提醒', icon: 'success' }),
      fail: () => wx.showToast({ title: '未授权提醒', icon: 'none' })
    })
  },

  // 每次真实点击顺带续订一条提醒额度（用户勾过"总是保持以上选择"后静默通过）
  askSub() {
    if (!config.REMIND_TEMPLATE_ID) return
    wx.requestSubscribeMessage({ tmplIds: [config.REMIND_TEMPLATE_ID], complete() {} })
  },

  pollUnlock(itemId, tries = 0) {
    if (tries === 0) this.setData({ unlockTip: '正在通知冰箱开锁…', unlockColor: 'yellow' })
    if (tries > 20) { this.setData({ unlockTip: '开锁超时，请联系宿管', unlockColor: 'red' }); return }
    api.get(`/api/v1/items/${itemId}/unlock`).then(d => {
      if (d.status === 'unlocked') {
        this.setData({ unlockTip: '冰箱已开锁，请取放物品并关门', unlockColor: 'green' })
        setTimeout(() => this.setData({ unlockTip: '' }), 8000)
      } else if (d.status === 'rejected') {
        this.setData({ unlockTip: '开锁被拒绝:' + (d.result_reason || ''), unlockColor: 'red' })
      } else if (d.status === 'expired') {
        this.setData({ unlockTip: '开锁指令已过期，请重新发起', unlockColor: 'red' })
      } else {
        setTimeout(() => this.pollUnlock(itemId, tries + 1), 1500)
      }
    }).catch(() => setTimeout(() => this.pollUnlock(itemId, tries + 1), 1500))
  },

  // 取出开锁：凭标签编码发起（主人本人），复用轮询链路
  unlockItem(e) {
    this.askSub()
    const { code, id } = e.currentTarget.dataset
    api.post(`/api/v1/items/${code}/unlock`, {}).then(d => this.pollUnlock(d.item_id))
      .catch(err => wx.showToast({ title: '开锁失败:' + (err.data && err.data.detail || err.statusCode), icon: 'none' }))
  },

  // 取物码：把二维码显示在手机屏幕上，对准冰箱触控屏摄像头扫描开锁
  showQr(e) {
    this.askSub()
    const id = e.currentTarget.dataset.id
    wx.showLoading({ title: '生成中' })
    api.get(`/api/v1/items/${id}/qr`).then(d => {
      wx.hideLoading()
      this.setData({ qr: {
        code: d.code, name: d.name,
        img: 'data:image/png;base64,' + d.png_base64
      }, qrShow: true })
    }).catch(() => { wx.hideLoading(); wx.showToast({ title: '生成失败', icon: 'none' }) })
  },
  hideQr() { this.setData({ qrShow: false, qr: null }) },
  noop() {},

  // 扫码认领：把触控屏登记/宿管代登的无主物品领到自己名下（一人一主）
  scanClaim() {
    this.askSub()
    wx.scanCode({
      success: res => {
        const code = (res.result || '').trim()
        if (!code) return
        api.post(`/api/v1/items/${encodeURIComponent(code)}/claim`, {}).then(d => {
          wx.showToast({ title: '已认领：' + d.name, icon: 'success' })
          this.loadMine()
        }).catch(err => wx.showToast({ title: '认领失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
      },
      fail: () => {}
    })
  },

  // 自我管理：取出登记 + 改名（到期日仅宿管可改）
  markTaken(e) {
    const { id, name } = e.currentTarget.dataset
    wx.showModal({
      title: '取出登记', content: `确认「${name}」已取出？台账将归档这条记录。`,
      success: r => r.confirm && (this.askSub(), api.post(`/api/v1/items/${id}/action`, { action: 'taken' })).then(() => {
        wx.showToast({ title: '已登记取出', icon: 'success' })
        this.loadMine()
      }).catch(() => wx.showToast({ title: '操作失败', icon: 'none' }))
    })
  },
  renameItem(e) {
    const { id, name } = e.currentTarget.dataset
    wx.showModal({
      title: '修改物品名', editable: true, placeholderText: name,
      success: r => {
        if (!r.confirm) return
        const v = (r.content || '').trim()
        if (!v) return wx.showToast({ title: '名称不能为空', icon: 'none' })
        this.askSub()
        api.post(`/api/v1/items/${id}/action`, { action: 'rename', value: v }).then(() => {
          wx.showToast({ title: '已改名', icon: 'success' })
          this.loadMine()
        }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
      }
    })
  }
})
