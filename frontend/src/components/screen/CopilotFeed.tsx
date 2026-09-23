import { COPILOT_ACTION_LABEL } from '../../api'
import type { CopilotActionName, ScreenCopilotRecent } from '../../api'

function fmtClock(s?: string): string {
  if (!s) return '—'
  const t = new Date(s)
  if (Number.isNaN(t.getTime())) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`
}

/** AI 协作动态：无限向上滚动（CSS marquee），hover 暂停。 */
export default function CopilotFeed({ recent }: { recent: ScreenCopilotRecent[] }) {
  const items = Array.isArray(recent) ? recent.filter(Boolean) : []
  if (!items.length) {
    return (
      <div className="sc-feed">
        <div className="sc-feed-empty">暂无 AI 协作记录</div>
      </div>
    )
  }
  // 复制一份实现无缝循环；条目越多滚得越慢
  const dur = Math.max(14, items.length * 3.2)
  const rows = [...items, ...items]
  return (
    <div className="sc-feed">
      <div className="sc-feed-track" style={{ animationDuration: `${dur}s` }}>
        {rows.map((r, i) => {
          const label = COPILOT_ACTION_LABEL[r?.action as CopilotActionName] ?? (r?.action || '—')
          return (
            <div className="sc-feed-item" key={`${i}-${r?.at ?? ''}`}>
              <span className={`sc-feed-tag ${r?.applied ? 'ok' : 'wait'}`}>
                {r?.applied ? '已采纳' : '待采纳'}
              </span>
              <span className="sc-feed-action">{label}</span>
              <span className="sc-feed-meta sc-num">
                {fmtClock(r?.at)} · {r?.model || '—'}
              </span>
              {r?.instruction && (
                <span className="sc-feed-ins" title={r.instruction}>
                  {r.instruction}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
