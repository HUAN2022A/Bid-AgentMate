/** markdown ↔ HTML 双向转换：编辑器加载/保存与 Copilot 选区提取共用一套配置，保证表格等结构 round-trip。 */
import { marked } from 'marked'
import TurndownService from 'turndown'
import { gfm } from 'turndown-plugin-gfm'

export const turndown = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced' })
turndown.use(gfm)

// TipTap 单元格内容包在 <p> 里，GFM 插件会把段落换行原样带进表格行而破坏表格，这里压成单行
turndown.addRule('tiptapTableCell', {
  filter: ['th', 'td'],
  replacement: (content, node) => {
    const index = Array.prototype.indexOf.call(node.parentNode?.childNodes ?? [], node)
    const prefix = index === 0 ? '| ' : ' '
    return prefix + content.replace(/\s*\n\s*/g, ' ').trim() + ' |'
  },
})

export function mdToHtml(md: string): string {
  return marked.parse(md, { async: false }) as string
}

/** TipTap 输出的 <colgroup> 会让 GFM 插件识别不到表头行（要求 tbody 是 table 首子节点），转换前剥掉。
 * 方括号还原：turndown 默认把 [ ] 转义成 \[ \]，会让红线标记 [待补：xxx] 带着反斜杠进导出 docx。 */
export function htmlToMd(html: string): string {
  const md = turndown.turndown(html.replace(/<colgroup>[\s\S]*?<\/colgroup>/g, ''))
  return md.replace(/\\([\[\]])/g, '$1')
}
