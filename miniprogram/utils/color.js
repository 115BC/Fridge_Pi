// 三色 -> 中文标签与样式类名
const COLOR_LABEL = { red: '超期', yellow: '临期', green: '正常' }
function labelOf(c) { return COLOR_LABEL[c] || c }
module.exports = { COLOR_LABEL, labelOf }
