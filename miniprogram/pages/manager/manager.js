const api = require('../../utils/api.js')
const colorUtil = require('../../utils/color.js')
const config = require('../../config.js')
const app = getApp()

const FILTERS = [
  { key: '', label: '全部' },
  { key: 'red', label: '超期' },
  { key: 'yellow', label: '临期' },
  { key: 'green', label: '正常' }
]

const SORTS = [
  { key: 'urgency', label: '紧急度' },
  { key: 'expire', label: '到期日' },
  { key: 'created', label: '存放先后' },
  { key: 'volume', label: '占地大小' }
]

const ROLE_OPTIONS = ['student', 'manager']
const ROLE_LABEL = { student: '学生', manager: '宿管' }
const STATUS_LABEL = { taken_out: '已取出登记', removed: '已清理' }

Page({
  data: {
    role: '', user: null, filters: FILTERS, filter: '', q: '',
    sorts: SORTS, sort: 'urgency',
    items: [], counts: {}, view: 'active', stock: null, stockBusy: false,
    unlockTip: '', unlockColor: '', cap: null,
    section: 'items',
    users: [], uq: '', roleOptions: ROLE_OPTIONS,
    pi_nodes: [], unlock_commands: [], events: [], reminders: []
  },
  onShow() {
    if (!app.guard(['manager'])) return
    this.setData({ role: wx.getStorageSync('role'), user: wx.getStorageSync('user') })
    this.load()
  },
  onPullDownRefresh() {
    const p = this.data.section === 'items' ? this.load()
      : this.data.section === 'users' ? this.loadUsers() : this.loadDebug()
    p.then(() => wx.stopPullDownRefresh())
  },
  setSection(e) {
    const s = e.currentTarget.dataset.s
    this.setData({ section: s })
    if (s === 'users') this.loadUsers()
    if (s === 'debug') this.loadDebug()
  },
  setSort(e) { this.setData({ sort: e.currentTarget.dataset.k }, () => this.load()) },
  load() {
    const parts = ['status=' + this.data.view, 'sort=' + this.data.sort]
    if (this.data.filter) parts.push('color=' + this.data.filter)
    if (this.data.q) parts.push('q=' + encodeURIComponent(this.data.q))
    return api.get('/api/v1/items' + '?' + parts.join('&')).then(d => {
      const items = d.items.map(it => Object.assign(it, {
        color_label: colorUtil.labelOf(it.color),
        vol_l: (it.vol_est_ml / 1000).toFixed(1),
        status_label: STATUS_LABEL[it.status] || ''
      }))
      this.setData({ items, counts: d.counts })
      this.loadCapacity()
    }).catch(e => wx.showToast({ title: '加载失败', icon: 'none' }))
  },
  loadCapacity() {
    return api.get('/api/v1/capacity').then(c => {
      this.setData({ cap: {
        capacity_l: (c.capacity_ml / 1000).toFixed(0),
        used_l: (c.used_ml / 1000).toFixed(1),
        util: c.util_pct, count: c.item_count
      } })
    }).catch(() => {})
  },
  editCapacity() {
    wx.showModal({
      title: '修正冰箱总容量', editable: true, placeholderText: '升，如 120',
      success: r => {
        if (!r.confirm) return
        const l = parseFloat(r.content)
        if (!(l > 0)) return wx.showToast({ title: '请输入有效容量', icon: 'none' })
        api.post('/api/v1/capacity', { capacity_ml: Math.round(l * 1000) }).then(() => {
          wx.showToast({ title: '已更新', icon: 'success' })
          this.loadCapacity()
        }).catch(() => wx.showToast({ title: '保存失败', icon: 'none' }))
      }
    })
  },
  editVolume(e) {
    const it = e.currentTarget.dataset.it
    wx.showModal({
      title: `「${it.name}」估算体积`, content: `当前约 ${it.vol_l}L${it.vol_manual ? '（已人工修正）' : '（按类别推断）'}`,
      editable: true, placeholderText: '升，输入 0 恢复按类别估算',
      success: r => {
        if (!r.confirm) return
        const l = parseFloat(r.content)
        if (isNaN(l) || l < 0) return wx.showToast({ title: '请输入有效体积', icon: 'none' })
        api.post(`/api/v1/items/${it.id}/volume`, { volume_ml: Math.round(l * 1000) }).then(() => {
          wx.showToast({ title: '已修正', icon: 'success' })
          this.load()
        }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
      }
    })
  },
  setFilter(e) { this.setData({ filter: e.currentTarget.dataset.k }, () => this.load()) },
  setView(e) { this.setData({ view: e.currentTarget.dataset.v }, () => this.load()) },
  onSearch(e) { this.setData({ q: e.detail.value }, () => this.load()) },
  logout() { app.logout() },

  // 拍照盘点：选一张照片 -> 上传后端批量识别二维码
  stocktake() {
    if (this.data.stockBusy) return
    wx.chooseMedia({
      count: 1, mediaType: ['image'], sourceType: ['album', 'camera'],
      success: res => {
        this.setData({ stockBusy: true })
        wx.showLoading({ title: '识别中' })
        wx.uploadFile({
          url: config.BASE_URL + '/api/v1/stocktake',
          filePath: res.tempFiles[0].tempFilePath,
          name: 'file',
          formData: { fridge_id: 'fridge-01' },
          header: { Authorization: 'Bearer ' + wx.getStorageSync('token') },
          success: r => {
            let d = {}
            try { d = JSON.parse(r.data) } catch (e) {}
            if (r.statusCode === 200) {
              this.setData({ stock: d })
            } else {
              wx.showToast({ title: '盘点失败:' + (d.detail || r.statusCode), icon: 'none' })
            }
          },
          fail: () => wx.showToast({ title: '上传失败', icon: 'none' }),
          complete: () => { wx.hideLoading(); this.setData({ stockBusy: false }) }
        })
      }
    })
  },
  closeStock() { this.setData({ stock: null }) },

  // 取出开锁（宿管可开任意在库物品）
  unlockItem(e) {
    const { code, id } = e.currentTarget.dataset
    api.post(`/api/v1/items/${code}/unlock`, {}).then(d => this.pollUnlock(d.item_id))
      .catch(err => wx.showToast({ title: '开锁失败:' + (err.data && err.data.detail || err.statusCode), icon: 'none' }))
  },
  scanUnlock() {
    wx.scanCode({
      success: res => {
        api.post(`/api/v1/items/${encodeURIComponent(res.result.trim())}/unlock`, {}).then(d => this.pollUnlock(d.item_id))
          .catch(err => wx.showToast({ title: '扫码开锁失败:' + (err.data && err.data.detail || err.statusCode), icon: 'none' }))
      },
      fail: () => {}
    })
  },
  pollUnlock(itemId, tries = 0) {
    if (tries === 0) this.setData({ unlockTip: '正在通知冰箱开锁…', unlockColor: 'yellow' })
    if (tries > 20) { this.setData({ unlockTip: '开锁超时，请检查冰箱端服务', unlockColor: 'red' }); return }
    api.get(`/api/v1/items/${itemId}/unlock`).then(d => {
      if (d.status === 'unlocked') {
        this.setData({ unlockTip: '冰箱已开锁', unlockColor: 'green' })
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

  // 待认领流转
  moveClaim(e) {
    const it = e.currentTarget.dataset.it
    wx.showModal({
      title: '移入待认领区', content: `「${it.name}」(${it.owner_name}) 移入待认领区？`,
      success: r => r.confirm && this.action(it.id, 'pending_claim')
    })
  },
  removeItem(e) {
    const it = e.currentTarget.dataset.it
    wx.showModal({
      title: '依规清理', content: `确认清理「${it.name}」？建议先拍照公示。操作将留痕。`,
      success: r => r.confirm && this.action(it.id, 'removed')
    })
  },
  restoreItem(e) {
    this.action(e.currentTarget.dataset.it.id, 'restore')
  },
  onExpireChange(e) {
    const id = e.currentTarget.dataset.id
    if (!e.detail.value) return
    api.post(`/api/v1/items/${id}/action`, { action: 'expire', value: e.detail.value }).then(() => {
      wx.showToast({ title: '已改到期日', icon: 'success' })
      this.load()
    }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
  },
  action(id, act) {
    api.post(`/api/v1/items/${id}/action`, { action: act }).then(() => {
      wx.showToast({ title: '已处理', icon: 'success' })
      this.load()
    }).catch(() => wx.showToast({ title: '操作失败', icon: 'none' }))
  },

  // 账号管理（原超管能力，已并入宿管）
  loadUsers() {
    const qs = this.data.uq ? '?q=' + encodeURIComponent(this.data.uq) : ''
    return api.get('/api/v1/users' + qs).then(d => {
      const users = d.users.map(u => Object.assign(u, {
        role_label: ROLE_LABEL[u.role] || u.role,
        roleIndex: Math.max(0, ROLE_OPTIONS.indexOf(u.role))
      }))
      this.setData({ users })
    }).catch(() => {})
  },
  onUserSearch(e) { this.setData({ uq: e.detail.value }, () => this.loadUsers()) },
  onRoleChange(e) {
    const id = e.currentTarget.dataset.id
    const role = ROLE_OPTIONS[e.detail.value]
    api.post(`/api/v1/users/${id}/role`, { role }).then(() => {
      wx.showToast({ title: '已设为' + ROLE_LABEL[role], icon: 'success' })
      this.loadUsers()
    }).catch(err => wx.showToast({ title: '设置失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
  },

  // 系统调试（原超管能力）
  loadDebug() {
    return Promise.all([
      api.get('/api/v1/debug/pi').then(d =>
        this.setData({ pi_nodes: d.pi_nodes, unlock_commands: d.unlock_commands })).catch(() => {}),
      api.get('/api/v1/debug/events?limit=80').then(d =>
        this.setData({ events: d.events, reminders: d.reminders })).catch(() => {})
    ])
  },

  remindOne(e) {
    const id = e.currentTarget.dataset.id
    wx.showLoading({ title: '推送中' })
    api.post('/api/v1/reminders', { item_ids: [id] }).then(d => {
      wx.hideLoading()
      const r = d.results[0] || {}
      wx.showModal({ title: '提醒结果', content: r.msg || '已发送', showCancel: false })
    }).catch(() => { wx.hideLoading(); wx.showToast({ title: '推送失败', icon: 'none' }) })
  },
  remindBatch() {
    wx.showModal({
      title: '一键提醒', content: '将向所有超期+临期物品的同学推送提醒，确认？',
      success: r => {
        if (!r.confirm) return
        wx.showLoading({ title: '批量推送中' })
        api.post('/api/v1/reminders', { colors: ['red', 'yellow'] }).then(d => {
          wx.hideLoading()
          wx.showModal({ title: '完成', content: `共 ${d.total} 件，成功推送 ${d.sent} 件（其余为开发模式或用户未订阅）`, showCancel: false })
        }).catch(() => { wx.hideLoading(); wx.showToast({ title: '推送失败', icon: 'none' }) })
      }
    })
  }
})
