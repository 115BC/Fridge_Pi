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

const ROLE_OPTIONS = ['student', 'manager', 'admin']
const ROLE_LABEL = { student: '学生', manager: '宿管', admin: '超级管理员' }
const STATUS_LABEL = { taken_out: '已取出登记', removed: '已清理' }

Page({
  data: {
    role: '', user: null, filters: FILTERS, filter: '', q: '',
    sorts: SORTS, sort: 'urgency',
    items: [], counts: {}, view: 'active', stock: null, stockBusy: false,
    unlockTip: '', unlockColor: '', cap: null,
    section: 'items', isAdmin: false,
    buildings: [], pickerNames: [], building: wx.getStorageSync('mgr_building') || '',
    users: [], uq: '', roleOptions: ROLE_OPTIONS,
    students: [], sq: '',
    pi_nodes: [], unlock_commands: [], events: [], reminders: []
  },
  onShow() {
    if (!app.guard(['manager', 'admin'])) return
    const role = wx.getStorageSync('role')
    this.setData({ role, user: wx.getStorageSync('user'), isAdmin: role === 'admin' })
    this.loadBuildings()
    this.load()
  },
  onPullDownRefresh() {
    const p = this.data.section === 'items' ? this.load()
      : this.data.section === 'students' ? this.loadStudents()
      : this.data.section === 'users' ? this.loadUsers() : this.loadDebug()
    p.then(() => wx.stopPullDownRefresh())
  },
  setSection(e) {
    const s = e.currentTarget.dataset.s
    if (s !== 'items' && s !== 'students' && !this.data.isAdmin)
      return wx.showToast({ title: '仅超级管理员可访问', icon: 'none' })
    this.setData({ section: s })
    if (s === 'students') this.loadStudents()
    if (s === 'users') this.loadUsers()
    if (s === 'debug') this.loadDebug()
  },

  // ---------- 楼宇：切换筛选（宿管可切任意楼）+ 超管维护 ----------
  loadBuildings() {
    return api.get('/api/v1/buildings').then(d => {
      const bs = d.buildings || []
      const pickerNames = ['全部楼宇'].concat(bs.map(b => b.name))
      if (this.data.building && !bs.some(b => b.name === this.data.building)) {
        this.setData({ buildings: bs, pickerNames, building: '' }, () => this.refreshSection())
      } else {
        this.setData({ buildings: bs, pickerNames })
      }
    }).catch(() => {})
  },
  onBuilding(e) {
    const bs = this.data.buildings
    const idx = Number(e.detail.value)
    const name = idx === 0 ? '' : (bs[idx - 1] || {}).name || ''
    this.setData({ building: name }, () => {
      wx.setStorageSync('mgr_building', name)
      this.refreshSection()
    })
  },
  refreshSection() {
    if (this.data.section === 'students') return this.loadStudents()
    return this.load()
  },
  currentFridge() {
    const b = this.data.buildings.find(x => x.name === this.data.building)
    return b ? b.fridge_id : 'fridge-01'
  },
  setSort(e) { this.setData({ sort: e.currentTarget.dataset.k }, () => this.load()) },
  load() {
    const parts = ['status=' + this.data.view, 'sort=' + this.data.sort]
    if (this.data.filter) parts.push('color=' + this.data.filter)
    if (this.data.q) parts.push('q=' + encodeURIComponent(this.data.q))
    if (this.data.building) parts.push('building=' + encodeURIComponent(this.data.building))
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
    const fridge = this.currentFridge()
    return api.get('/api/v1/capacity?fridge_id=' + encodeURIComponent(fridge)).then(c => {
      this.setData({ cap: {
        capacity_l: (c.capacity_ml / 1000).toFixed(0),
        used_l: (c.used_ml / 1000).toFixed(1),
        util: c.util_pct, count: c.item_count, fridge
      } })
    }).catch(() => {})
  },
  editCapacity() {
    const fridge = (this.data.cap || {}).fridge || this.currentFridge()
    wx.showModal({
      title: '修正冰箱总容量', content: `当前冰箱：${fridge}`, editable: true, placeholderText: '升，如 120',
      success: r => {
        if (!r.confirm) return
        const l = parseFloat(r.content)
        if (!(l > 0)) return wx.showToast({ title: '请输入有效容量', icon: 'none' })
        api.post('/api/v1/capacity', { capacity_ml: Math.round(l * 1000), fridge_id: fridge }).then(() => {
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

  // 拍照盘点：选图 -> 压缩 -> base64 走云托管通道识别（callContainer 不支持 multipart 上传）
  stocktake() {
    if (this.data.stockBusy) return
    wx.chooseMedia({
      count: 1, mediaType: ['image'], sourceType: ['album', 'camera'],
      success: res => {
        const src = res.tempFiles[0].tempFilePath
        this.setData({ stockBusy: true })
        wx.showLoading({ title: '识别中' })
        const send = (fp) => wx.getFileSystemManager().readFile({
          filePath: fp, encoding: 'base64',
          success: r => api.post('/api/v1/stocktake_b64',
            { image_b64: r.data, fridge_id: this.currentFridge() }
          ).then(d => this.setData({ stock: d })
          ).catch(e => wx.showToast({ title: '盘点失败:' + (e.data && e.data.detail || e.statusCode), icon: 'none' })
          ).finally(() => { wx.hideLoading(); this.setData({ stockBusy: false }) }),
          fail: () => { wx.hideLoading(); this.setData({ stockBusy: false }); wx.showToast({ title: '读取图片失败', icon: 'none' }) }
        })
        wx.compressImage({ src, quality: 45, compressedWidth: 1280,
          success: cmp => send(cmp.tempFilePath), fail: () => send(src) })
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
  markTaken(e) {
    const it = e.currentTarget.dataset.it
    wx.showModal({
      title: '标记已取出', content: `确认「${it.name}」(${it.owner_name}) 已由本人取走？台账将归档，学生端同步消失。`,
      success: r => r.confirm && this.action(it.id, 'taken')
    })
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
  editQuota(e) {
    const it = e.currentTarget.dataset.it
    wx.showModal({
      title: `「${it.name || it.openid}」在库额度`, editable: true,
      placeholderText: `当前 ${it.quota || 5} 件，输入新额度(1~50)`,
      success: r => {
        if (!r.confirm) return
        const n = parseInt(r.content)
        if (!(n >= 1 && n <= 50)) return wx.showToast({ title: '请输入 1~50', icon: 'none' })
        api.post(`/api/v1/users/${it.id}/quota`, { quota: n }).then(() => {
          wx.showToast({ title: '已调整额度', icon: 'success' })
          this.loadUsers()
        }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
      }
    })
  },
  onRoleChange(e) {
    const id = e.currentTarget.dataset.id
    const role = ROLE_OPTIONS[e.detail.value]
    api.post(`/api/v1/users/${id}/role`, { role }).then(() => {
      wx.showToast({ title: '已设为' + ROLE_LABEL[role], icon: 'success' })
      this.loadUsers()
    }).catch(err => wx.showToast({ title: '设置失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
  },

  // ---------- 学生管理（宿管：按楼筛选、代改姓名/寝室；超管额外可改楼/管楼宇） ----------
  loadStudents() {
    const parts = []
    if (this.data.building) parts.push('building=' + encodeURIComponent(this.data.building))
    if (this.data.sq) parts.push('q=' + encodeURIComponent(this.data.sq))
    return api.get('/api/v1/users' + (parts.length ? '?' + parts.join('&') : '')).then(d => {
      const students = d.users.filter(u => u.role === 'student' || !u.building).map(u => Object.assign(u, {
        building_label: u.building || '未分楼'
      }))
      this.setData({ students })
    }).catch(() => {})
  },
  onStudentSearch(e) { this.setData({ sq: e.detail.value }, () => this.loadStudents()) },
  editStudent(e) {
    const it = e.currentTarget.dataset.it
    wx.showModal({
      title: `「${it.name || it.openid}」的姓名`, editable: true, placeholderText: it.name || '输入姓名',
      success: r1 => {
        if (!r1.confirm) return
        const name = (r1.content || '').trim() || it.name
        wx.showModal({
          title: '寝室号', editable: true, placeholderText: it.room || '如 2-417',
          success: r2 => {
            if (!r2.confirm) return
            const room = (r2.content || '').trim() || it.room
            api.post(`/api/v1/users/${it.id}/profile`, { name, room }).then(() => {
              wx.showToast({ title: '已更新', icon: 'success' })
              this.loadStudents()
            }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
          }
        })
      }
    })
  },
  changeStudentBuilding(e) {
    if (!this.data.isAdmin) return wx.showToast({ title: '改楼宇仅超管可操作', icon: 'none' })
    const it = e.currentTarget.dataset.it
    const names = this.data.buildings.map(b => b.name)
    if (!names.length) return wx.showToast({ title: '还没有楼宇，请先添加', icon: 'none' })
    wx.showActionSheet({
      itemList: names,
      success: r => {
        api.post(`/api/v1/users/${it.id}/building`, { building: names[r.tapIndex] }).then(() => {
          wx.showToast({ title: `已移到${names[r.tapIndex]}`, icon: 'success' })
          this.loadStudents()
        }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
      }
    })
  },
  addBuilding() {
    wx.showModal({
      title: '新增楼宇：名称', editable: true, placeholderText: '如 东楼',
      success: r1 => {
        if (!r1.confirm) return
        const name = (r1.content || '').trim()
        if (!name) return wx.showToast({ title: '名称不能为空', icon: 'none' })
        wx.showModal({
          title: '该楼冰箱编号（树莓派配置里的 fridge_id）', editable: true, placeholderText: '如 fridge-02',
          success: r2 => {
            if (!r2.confirm) return
            const fridge = (r2.content || '').trim()
            if (!fridge) return wx.showToast({ title: '冰箱编号不能为空', icon: 'none' })
            api.post('/api/v1/admin/buildings', { name, fridge_id: fridge }).then(() => {
              wx.showToast({ title: '已添加', icon: 'success' })
              this.loadBuildings(); this.loadStudents()
            }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
          }
        })
      }
    })
  },
  delBuilding(e) {
    const name = e.currentTarget.dataset.name
    wx.showModal({
      title: '删除楼宇', content: `确认删除「${name}」？有学生归属时删不掉。`,
      success: r => r.confirm && api.del('/api/v1/admin/buildings/' + encodeURIComponent(name)).then(() => {
        wx.showToast({ title: '已删除', icon: 'success' })
        this.loadBuildings(); this.refreshSection()
      }).catch(err => wx.showToast({ title: '失败:' + (err.data && err.data.detail || ''), icon: 'none' }))
    })
  },
  setContact() {
    wx.showModal({
      title: '超管联系方式（学生页展示）', editable: true, placeholderText: '如 微信号 xxx，留空则隐藏',
      success: r => {
        if (!r.confirm) return
        api.post('/api/v1/admin/contact', { value: (r.content || '').trim() }).then(() => {
          wx.showToast({ title: '已保存', icon: 'success' })
        }).catch(() => wx.showToast({ title: '保存失败', icon: 'none' }))
      }
    })
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
    const scope = this.data.building ? `【${this.data.building}】` : '【全部楼宇】'
    wx.showModal({
      title: '一键提醒', content: `将向${scope}所有超期+临期物品的同学推送提醒，确认？`,
      success: r => {
        if (!r.confirm) return
        wx.showLoading({ title: '批量推送中' })
        api.post('/api/v1/reminders', { colors: ['red', 'yellow'], building: this.data.building }).then(d => {
          wx.hideLoading()
          wx.showModal({ title: '完成', content: `共 ${d.total} 件，成功推送 ${d.sent} 件（其余为开发模式或用户未订阅）`, showCancel: false })
        }).catch(() => { wx.hideLoading(); wx.showToast({ title: '推送失败', icon: 'none' }) })
      }
    })
  }
})
