/** 覆盖矩阵：评分点 × 章节覆盖热力图（交付页自查可视化）。
 *
 * - 数据自取（queryKey ['coverage', pid]），自查完成后由 DeliveryPage invalidate 联动刷新
 * - 纯 div + CSS Grid 双向滚动网格：首列 sticky left、章节表头 sticky top、左上角双向 sticky
 * - 不引图表库；三态色格（绿 covered / 橙 partial / 灰 none），格级证据与命中关键词走 Tooltip
 * - 空态三分支：无章节 / 无评分点 / 「只看技术类」过滤后为空（设计文档 §5）
 */
import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Checkbox, Empty, Space, Spin, Tag, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import {
  getCoverage,
  type CoverageCellOut,
  type CoverageChapterOut,
  type CoverageItemOut,
  type CoverageStatus,
} from '../../api'

/** 三态配色（取值与 AntD6 green-6 / gold-6 对齐，灰格带浅边框） */
const CELL_STYLE: Record<CoverageStatus, { bg: string; hover: string; border: string }> = {
  covered: { bg: '#52c41a', hover: '#389e0d', border: 'transparent' },
  partial: { bg: '#faad14', hover: '#d48806', border: 'transparent' },
  none: { bg: '#f5f5f5', hover: '#e8e8e8', border: '#f0f0f0' },
}

const STATUS_LABEL: Record<CoverageStatus, string> = {
  covered: '已覆盖',
  partial: '部分覆盖',
  none: '未覆盖',
}

/** 分值去尾零展示（10 → "10"、2.5 → "2.5"） */
const fmtScore = (score: number) => String(score).replace(/\.0$/, '')

/** 格级状态说明行（从格内信息推导判定方式，与后端 evidence 互补） */
function statusLine(cell: CoverageCellOut): string {
  if (cell.status === 'covered') {
    return cell.hit_keywords.length
      ? `已覆盖（硬指标 ${cell.hit_keywords.length}/${cell.hit_keywords.length} 命中）`
      : '已覆盖（无硬指标关键词，按挂接 + 已起草正文判定）'
  }
  if (cell.status === 'partial') {
    return cell.hit_keywords.length
      ? `部分覆盖（命中 ${cell.hit_keywords.length} 项硬指标关键词，未全中）`
      : '部分覆盖（挂接本章但未命中硬指标关键词或正文未起草）'
  }
  return '无关（未挂接本章且未命中）'
}

/** 章节列表头：sticky top，未起草章节灰字 + 小 Tag，悬停看全名与字数 */
function MatrixHeaderCell({ chapter }: { chapter: CoverageChapterOut }) {
  const secondary = !chapter.has_content
  return (
    <Tooltip
      title={`${chapter.chapter_key} ${chapter.title}（约 ${chapter.word_count} 字${chapter.has_content ? '' : '，未起草'}）`}
    >
      <div
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 2,
          background: '#fff',
          borderBottom: '1px solid #f0f0f0',
          padding: '6px 6px 4px',
          textAlign: 'center',
          color: secondary ? '#8c8c8c' : undefined,
          cursor: 'default',
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap' }}>
          {chapter.chapter_key}
          {secondary && (
            <Tag style={{ marginInlineStart: 4, marginInlineEnd: 0, fontSize: 10, lineHeight: '16px' }} color="default">
              未起草
            </Tag>
          )}
        </div>
        <div
          style={{
            fontSize: 11,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
          title={chapter.title}
        >
          {chapter.title}
        </div>
      </div>
    </Tooltip>
  )
}

/** 覆盖格：三态底色 + hover 加深，悬停 Tooltip 展示评分原文摘要/状态/命中关键词/证据 */
function MatrixCell({
  item,
  chapter,
  cell,
}: {
  item: CoverageItemOut
  chapter: CoverageChapterOut
  cell: CoverageCellOut
}) {
  const [hover, setHover] = useState(false)
  const style = CELL_STYLE[cell.status]
  return (
    <Tooltip
      title={
        <div>
          <div>
            [{item.item_key} {item.item} · {fmtScore(item.score)}分] × [{chapter.chapter_key} {chapter.title}]
          </div>
          <div style={{ marginTop: 4 }}>评分原文摘要：{item.criteria_brief}</div>
          <div style={{ marginTop: 4 }}>状态：{statusLine(cell)}</div>
          {cell.hit_keywords.length > 0 && (
            <div style={{ marginTop: 4 }}>
              命中关键词：
              {cell.hit_keywords.map((k) => (
                <Tag key={k} style={{ marginInlineStart: 4 }}>
                  {k}
                </Tag>
              ))}
            </div>
          )}
          {cell.evidence && <div style={{ marginTop: 4 }}>证据：{cell.evidence}</div>}
        </div>
      }
      overlayStyle={{ maxWidth: 360 }}
    >
      <div
        style={{
          height: 28,
          margin: 3,
          borderRadius: 2,
          cursor: 'default',
          background: hover ? style.hover : style.bg,
          border: cell.status === 'none' ? `1px solid ${style.border}` : undefined,
        }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      />
    </Tooltip>
  )
}

/** 覆盖矩阵卡片：行=评分点（按 item_key），列=章节（按 sort_order），cells 与 chapters 下标对齐 */
export default function CoverageMatrix({ pid }: { pid: number }) {
  const qc = useQueryClient()
  const [techOnly, setTechOnly] = useState(false)
  const { data, isLoading } = useQuery({ queryKey: ['coverage', pid], queryFn: () => getCoverage(pid) })

  const items = useMemo(
    () => (data ? (techOnly ? data.items.filter((i) => i.category === '技术') : data.items) : []),
    [data, techOnly],
  )

  const extra = (
    <Space size={12} wrap>
      {(Object.keys(STATUS_LABEL) as CoverageStatus[]).map((s) => (
        <span key={s} style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
          <span
            style={{
              display: 'inline-block',
              width: 10,
              height: 10,
              borderRadius: 2,
              marginInlineEnd: 4,
              verticalAlign: '-1px',
              background: CELL_STYLE[s].bg,
              border: s === 'none' ? `1px solid ${CELL_STYLE[s].border}` : undefined,
            }}
          />
          {STATUS_LABEL[s]} {data ? data.summary[s] : ''}
        </span>
      ))}
      <Checkbox checked={techOnly} onChange={(e) => setTechOnly(e.target.checked)}>
        只看技术类
      </Checkbox>
      <Button
        size="small"
        icon={<ReloadOutlined />}
        onClick={() => qc.invalidateQueries({ queryKey: ['coverage', pid] })}
      />
    </Space>
  )

  let body: ReactNode
  if (isLoading) {
    body = <Spin />
  } else if (!data || data.chapters.length === 0) {
    body = <Empty description="尚无章节，确认大纲并起草后可见覆盖矩阵" />
  } else if (data.items.length === 0) {
    body = <Empty description="尚无评分点，完成招标解析后可见" />
  } else if (items.length === 0) {
    body = <Empty description="该分类下无评分点" />
  } else {
    body = (
      <div style={{ overflow: 'auto', maxHeight: 480, border: '1px solid #f0f0f0', borderRadius: 4 }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `280px repeat(${data.chapters.length}, 72px)`,
            minWidth: 'max-content',
          }}
        >
          {/* 左上角：双向 sticky */}
          <div
            style={{
              position: 'sticky',
              top: 0,
              left: 0,
              zIndex: 3,
              background: '#fff',
              borderBottom: '1px solid #f0f0f0',
              borderRight: '1px solid #f0f0f0',
              padding: '6px 8px',
              fontSize: 12,
              color: '#8c8c8c',
            }}
          >
            评分点 ＼ 章节
          </div>
          {data.chapters.map((ch) => (
            <MatrixHeaderCell key={ch.chapter_key} chapter={ch} />
          ))}
          {items.map((it) => (
            <Fragment key={it.item_key}>
              {/* 行首：sticky left，悬停看评分原文摘要（未挂章时附挂接提示） */}
              <Tooltip
                title={
                  it.criteria_brief +
                  (it.linked_chapters.length === 0 ? '\n（提示：该评分点尚未挂接章节，请到大纲页挂接）' : '')
                }
              >
                <div
                  style={{
                    position: 'sticky',
                    left: 0,
                    zIndex: 1,
                    background: '#fff',
                    borderRight: '1px solid #f0f0f0',
                    borderBottom: '1px solid #f0f0f0',
                    padding: '4px 8px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    cursor: 'default',
                  }}
                >
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      fontSize: 13,
                    }}
                  >
                    {it.item_key} {it.item}
                  </span>
                  <Tag color="blue" style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                    {fmtScore(it.score)}分
                  </Tag>
                  {it.linked_chapters.length === 0 && (
                    <Tag style={{ marginInlineEnd: 0, flexShrink: 0 }}>未挂章</Tag>
                  )}
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      flexShrink: 0,
                      background: CELL_STYLE[it.row_status].bg,
                    }}
                  />
                </div>
              </Tooltip>
              {it.cells.map((cell, idx) => (
                <MatrixCell key={cell.chapter_key} cell={cell} item={it} chapter={data.chapters[idx]!} />
              ))}
            </Fragment>
          ))}
        </div>
      </div>
    )
  }

  return (
    <Card title="覆盖矩阵：评分点 × 章节" extra={extra}>
      {body}
    </Card>
  )
}
