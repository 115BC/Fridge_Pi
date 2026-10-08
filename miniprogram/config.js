// 后端地址：开发时在微信开发者工具勾选「不校验合法域名」，
// 真机/上线需改为已备案的 HTTPS 域名并在小程序后台配置 request 合法域名
module.exports = {
  BASE_URL: 'http://192.168.5.211:8001',   // 树莓派(方案B同机部署)；上线需改 HTTPS 备案域名
  // 订阅消息模板 ID（与后端 config.yaml 的 remind_template_id 一致），登记时申请推送权限
  REMIND_TEMPLATE_ID: ''
}
