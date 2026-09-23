import { Fragment } from 'react'
import type { ScreenCoverageOut } from '../../api'

type CellStatus = 'covered' | 'partial' | 'none'

const CELL_LABEL: Record<CellStatus, string> = {
  covered: '已覆盖',
  partial: '部分覆盖',
  none: '未覆盖',
}

/** cells 兼容三种后端形态：数组 / JSON 字符串数组 / 逗号分隔字符串；越界或脏值按 none。 */
function cellAt(raw: unknown, i: number): CellStatus {
  let arr: unknown = raw
  if (typeof raw === 'string') {
    const s = raw.trim()
    if (s.startsWith('[')) {
      try {
        arr = JSON.parse(s)
      } catch {
        arr = s
      }
    } else {
      arr = s.split(/[,;|]+/)
    }
  }
  if (!Array.isArray(arr)) return 'none'
  const v = arr[i]
  return v === 'covered' || v === 'partial' ? v : 'none'
}

interface RowsProps {
  chapters: ScreenCoverageOut['chapters']
  items: ScreenCoverageOut['items']
  template: string
  copyKey: string
}

/** 一份完整的行网格（评分点 × 章节格子），轮播轨道里渲染两份实现无缝循环。 */
function Rows({ chapters, items, template, copyKey }: RowsProps) {
  return (
    <div className="sc-hm-grid" style={{ gridTemplateColumns: template }}>
      {items.map((it, idx) => (
        <Fragment key={`${copyKey}-${it?.key ?? idx}`}>
          <div className="sc-hm-rowlabel" title={it.item}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {it.item || it.key}
            </span>
            <span className="score sc-num">{Number.isFinite(it.score) ? `${it.score}分` : ''}</span>
          </div>
          {chapters.map((c, ci) => {
            const st = cellAt(it.cells, ci)
            return (
              <div
                key={c.key}
                className={`sc-hm-cell ${st}`}
                title={`${it.item || it.key} × ${c.title}（${c.key}）：${CELL_LABEL[st]}`}
              />
            )
          })}
        </Fragment>
      ))}
    </div>
  )
}

/** 评分覆盖热力图：表头固定，行区自动匀速向上滚动轮播（hover 暂停）——大屏投放无鼠标也能看全。 */
export default function CoverageHeatmap({ coverage }: { coverage: ScreenCoverageOut | undefined }) {
  const chapters = coverage?.chapters ?? []
  const items = coverage?.items ?? []

  if (!chapters.length || !items.length) {
    return <div className="sc-chart-hint">暂无覆盖数据</div>
  }

  const template = `minmax(120px, 168px) repeat(${chapters.length}, 16px)`
  // 行越多滚得越慢（每行约 3.2s），保证可读
  const dur = Math.max(20, items.length * 3.2)

  return (
    <div className="sc-hm-wrap">
      <div className="sc-hm-headrow" style={{ gridTemplateColumns: template }}>
        <div className="sc-hm-head">评分点 / 章节</div>
        {chapters.map((c) => (
          <div className="sc-hm-head" key={c.key} title={c.title}>
            {c.key}
          </div>
        ))}
      </div>
      <div className="sc-hm-body">
        <div className="sc-hm-track" style={{ animationDuration: `${dur}s` }}>
          <Rows chapters={chapters} items={items} template={template} copyKey="a" />
          <Rows chapters={chapters} items={items} template={template} copyKey="b" />
        </div>
      </div>
    </div>
  )
}
