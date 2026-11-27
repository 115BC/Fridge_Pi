/* 网站前端配置：业务端口(80/443/9080/9443/16666)走同源（Nginx 反代 /api 与 /photos）；
   其余端口（本地 8088 静态站 / file://）回落到同主机 :8001 后端 */
window.WEB_CONFIG = {
  API_BASE: (location.protocol === 'http:' || location.protocol === 'https:') &&
            (location.port === '' || ['80', '443', '9080', '9443', '16666'].includes(location.port))
    ? ''
    : 'http://' + (location.hostname || '127.0.0.1') + ':8001',
  APP_TITLE: '宿舍冰箱智能管理'
};