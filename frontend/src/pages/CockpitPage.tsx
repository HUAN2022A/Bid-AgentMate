/** 项目驾驶舱：只读总览页——状态机流程条 + 汇总卡 + 评分分布 + 章节进度。
 * 零新端点：四版块全部复用既有 GET（project/chapters/analysis/export-preview）。
 * 轮询（Q17 范式）：project 在 parsing/drafting/checking 态 5s；chapters 仅 drafting 态 5s；
 * analysis 与 preview 不轮询。查询键与详情页/章节页一致，跨页缓存互通。
 */
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  CheckCircleFilled,
  CloseCircleFilled,
  EditOutlined,
  LoadingOutlined,
  MoreOutlined,
} from '@ant-design/icons'
import { Fragment } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  CHAPTER_STATE_META,
  STATE_META,
  getAnalysis,
  getExportPreview,
  getProject,
  listChapters,
  type ChapterOut,
} from '../api'

/** 流程条主干节点：PROJECT_STATES 去掉 parse_failed（其映射到 parsing 节点标红展示） */
const FLOW_STATES: readonly string[] = [
  'created',
  'parsing',
  'outline_pending',
  'outline_confirmed',
  'drafting',
  'draft_done',
  'checking',
  'exported',
]

/** 进行态：当前节点加旋转图标 */
const SPIN_STATES = new Set(['parsing', 'drafting', 'checking'])

/** 节点跳转目标（当前/已过态可点；created/parsing/parse_failed 回详情页） */
const STATE_ROUTE: Record<string, string> = {
  created: '',
  parsing: '',
  parse_failed: '',
  outline_pending: '/outline',
  outline_confirmed: '/chapters',
  drafting: '/chapters',
  draft_done: '/chapters',
  checking: '/delivery',
  exported: '/delivery',
}

/** [待补] 可统计的状态（export/preview 的业务门槛，drafting 期不发请求） */
const PREVIEW_STATES = new Set(['draft_done', 'checking', 'exported'])

/** 驾驶舱 project 轮询状态集 */
const POLLING_STATES = new Set(['parsing', 'drafting', 'checking'])

/** 评分分段条色板：AntD 预设色循环（纯 CSS 着色，不引图表库） */
const SEGMENT_COLORS = ['#1677ff', '#52c41a', '#faad14', '#722ed1', '#13c2c2', '#eb2f96']

/** 浮点显示去尾零：6.0 → '6'，6.5 → '6.5' */
const fmtNum = (n: number) => String(Math.round(n * 100) / 100)

/** 单个流程节点（图标 + 中文标签），clickable 时点击跳对应功能页 */
function FlowNode({
  state,
  phase,
  onClick,
}: {
  state: string
  phase: 'past' | 'current' | 'future' | 'current-failed'
  onClick: () => void
}) {
  const label = STATE_META[state]?.label ?? state
  // 样式分档：已过 = 灰底绿勾；当前 = 主色描边（parse_failed 为红 error 态）；未来 = 全灰
  const box =
    phase === 'current-failed'
      ? { border: '#ff4d4f', bg: '#fff2f0', color: '#ff4d4f' }
      : phase === 'current'
        ? { border: '#1677ff', bg: '#e6f4ff', color: '#1677ff' }
        : phase === 'past'
          ? { border: '#f0f0f0', bg: '#fafafa', color: 'rgba(0,0,0,0.45)' }
          : { border: '#f0f0f0', bg: '#f5f5f5', color: 'rgba(0,0,0,0.25)' }
  const clickable = phase === 'past' || phase === 'current' || phase === 'current-failed'
  const icon =
    phase === 'past' ? (
      <CheckCircleFilled style={{ fontSize: 20, color: '#52c41a' }} />
    ) : phase === 'current-failed' ? (
      <CloseCircleFilled style={{ fontSize: 20, color: '#ff4d4f' }} />
    ) : phase === 'current' ? (
      SPIN_STATES.has(state) ? (
        <LoadingOutlined style={{ fontSize: 20 }} spin />
      ) : (
        <span
          style={{
            width: 12,
            height: 12,
            borderRadius: '50%',
            background: '#1677ff',
            display: 'inline-block',
          }}
        />
      )
    ) : (
      <MoreOutlined style={{ fontSize: 20 }} />
    )
  return (
    <Tooltip title={clickable ? '点击进入' : undefined}>
      <div
        onClick={clickable ? onClick : undefined}
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 6,
          padding: '12px 8px',
          minWidth: 92,
          borderRadius: 8,
          border: `1px solid ${box.border}`,
          background: box.bg,
          color: box.color,
          cursor: clickable ? 'pointer' : 'default',
          flex: '0 1 auto',
        }}
      >
        {icon}
        <span style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{label}</span>
      </div>
    </Tooltip>
  )
}

/** 流程条：主干 8 节点横排 + CSS 三角箭头；parse_failed 时 parsing 节点标红并叠错误条 */
function FlowBar({ state, onJump }: { state: string; onJump: (s: string) => void }) {
  const parseFailed = state === 'parse_failed'
  const current = parseFailed ? 'parsing' : state
  const idx = Math.max(0, FLOW_STATES.indexOf(current))
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'stretch', justifyContent: 'space-between', flexWrap: 'wrap', rowGap: 8 }}>
        {FLOW_STATES.map((s, i) => (
          <Fragment key={s}>
            {i > 0 && (
              <span
                aria-hidden
                style={{
                  alignSelf: 'center',
                  width: 0,
                  height: 0,
                  borderTop: '6px solid transparent',
                  borderBottom: '6px solid transparent',
                  borderLeft: '8px solid #d9d9d9',
                  margin: '0 2px',
                  flex: '0 0 auto',
                }}
              />
            )}
            <FlowNode
              state={s}
              phase={i < idx ? 'past' : i === idx ? (parseFailed ? 'current-failed' : 'current') : 'future'}
              onClick={() => onJump(s)}
            />
          </Fragment>
        ))}
      </div>
    </div>
  )
}

export default function CockpitPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const nav = useNavigate()

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', pid],
    queryFn: () => getProject(pid),
    // parsing/drafting/checking 5s 轮询，迁出即停；refetchIntervalInBackground 默认 false，后台标签页暂停
    refetchInterval: (q) => (POLLING_STATES.has(q.state.data?.state ?? '') ? 5000 : false),
  })
  const { data: chapters, isLoading: chaptersLoading } = useQuery({
    queryKey: ['chapters', pid],
    queryFn: () => listChapters(pid),
    // 仅起草期间轮询（5s）；状态迁出 drafting 由 project 轮询数据驱动自动停
    refetchInterval: () => (project?.state === 'drafting' ? 5000 : false),
  })
  const { data: analysis } = useQuery({
    queryKey: ['analysis', pid],
    queryFn: () => getAnalysis(pid),
    // 评分点解析完成后不变，不轮询
  })
  const previewEnabled = PREVIEW_STATES.has(project?.state ?? '')
  const { data: preview } = useQuery({
    queryKey: ['export-preview', pid],
    queryFn: () => getExportPreview(pid),
    // [待补] 仅在 preview 可用状态请求（否则接口 409）；状态迁入可用集自动启用
    enabled: previewEnabled,
  })

  if (isLoading || !project) return <Card loading />

  // ---- 汇总：章节列表前端聚合（drafting 期即实时可得，与 preview total_words 口径一致）----
  const list = chapters ?? []
  const totalWords = list.reduce((s, c) => s + c.word_count, 0)
  const targetWords = list.reduce((s, c) => s + c.target_words, 0)
  const doneCount = list.filter((c) => c.state === 'draft_done' || c.state === 'edited').length

  // ---- 评分分布：按 category 聚合 sum(score)，空类目归「未分类」 ----
  const catMap = new Map<string, { name: string; count: number; sum: number }>()
  for (const it of analysis?.scoring_items ?? []) {
    const name = (it.category || '').trim() || '未分类'
    const cur = catMap.get(name) ?? { name, count: 0, sum: 0 }
    cur.count += 1
    cur.sum += it.score
    catMap.set(name, cur)
  }
  const cats = [...catMap.values()].sort((a, b) => b.sum - a.sum)
  const totalScore = cats.reduce((s, c) => s + c.sum, 0)

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {/* 行 1：状态机流程条 */}
      <Card
        title={
          <Space>
            项目状态流程
            <Tag color={STATE_META[project.state]?.color}>{STATE_META[project.state]?.label ?? project.state}</Tag>
          </Space>
        }
        extra={
          <Button size="small" onClick={() => nav(`/projects/${pid}`)}>
            返回详情
          </Button>
        }
      >
        <FlowBar state={project.state} onJump={(s) => nav(`/projects/${pid}${STATE_ROUTE[s] ?? ''}`)} />
        {project.state === 'parse_failed' && project.parse_error && (
          <Alert style={{ marginTop: 12 }} type="error" showIcon message={project.parse_error} />
        )}
      </Card>

      {/* 行 2：汇总卡 + 评分分布 */}
      <Row gutter={16}>
        <Col span={14}>
          <Card title="汇总" style={{ height: '100%' }}>
            <Row gutter={24}>
              <Col>
                <Statistic title="总字数 / 目标" value={totalWords.toLocaleString()} suffix={`/ ${targetWords.toLocaleString()}`} />
              </Col>
              <Col>
                <Statistic title="完成章节" value={doneCount} suffix={`/ ${list.length}`} />
              </Col>
              <Col>
                {previewEnabled ? (
                  <Statistic
                    title="[待补]"
                    value={preview?.pending_gaps ?? '—'}
                    valueStyle={{ color: preview?.pending_gaps ? '#faad14' : '#3f8600' }}
                  />
                ) : (
                  <Tooltip title="全部章节起草完成后可统计">
                    <div>
                      <Statistic title="[待补]" value="—" />
                    </div>
                  </Tooltip>
                )}
              </Col>
            </Row>
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title="评分分布"
            extra={cats.length > 0 ? <Typography.Text type="secondary">总分 {fmtNum(totalScore)}</Typography.Text> : null}
            style={{ height: '100%' }}
          >
            {cats.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="解析完成后可查看评分分布" />
            ) : (
              <>
                {/* 分段条：段宽 ∝ 分值占比，纯 CSS flex */}
                <div style={{ display: 'flex', height: 28, borderRadius: 6, overflow: 'hidden' }}>
                  {cats.map((c, i) => (
                    <Tooltip key={c.name} title={`${c.name} ${fmtNum(c.sum)} 分（${Math.round((c.sum / totalScore) * 100)}%）`}>
                      <div
                        style={{
                          width: `${(c.sum / totalScore) * 100}%`,
                          background: SEGMENT_COLORS[i % SEGMENT_COLORS.length],
                          color: '#fff',
                          fontSize: 12,
                          lineHeight: '28px',
                          padding: '0 8px',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {c.name} {fmtNum(c.sum)}
                      </div>
                    </Tooltip>
                  ))}
                </div>
                {/* 类目明细 */}
                <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {cats.map((c, i) => (
                    <div key={c.name} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: '50%',
                          background: SEGMENT_COLORS[i % SEGMENT_COLORS.length],
                          flexShrink: 0,
                        }}
                      />
                      <span>{c.name}</span>
                      <Typography.Text type="secondary">
                        {fmtNum(c.sum)} 分（{Math.round((c.sum / totalScore) * 100)}%）· {c.count} 项
                      </Typography.Text>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Card>
        </Col>
      </Row>

      {/* 行 3：章节进度列表 */}
      <Card title="章节进度" extra={<Typography.Text type="secondary">完成 {doneCount}/{list.length}</Typography.Text>}>
        <Table<ChapterOut>
          rowKey="id"
          loading={chaptersLoading}
          size="middle"
          pagination={false}
          dataSource={list}
          locale={{ emptyText: '尚无章节（确认大纲后自动生成）' }}
          columns={[
            { title: '编号', dataIndex: 'chapter_key', width: 90 },
            {
              title: '章节',
              dataIndex: 'title',
              render: (v, r) => (
                <Space>
                  {v}
                  {r.needs_review && <Tag color="orange">大纲已变更</Tag>}
                </Space>
              ),
            },
            {
              title: '状态',
              dataIndex: 'state',
              width: 110,
              render: (v, r) => (
                <Space direction="vertical" size={0}>
                  <Tag color={CHAPTER_STATE_META[v]?.color}>{CHAPTER_STATE_META[v]?.label ?? v}</Tag>
                  {v === 'draft_failed' && (
                    <Typography.Text type="danger" style={{ fontSize: 12 }} ellipsis={{ tooltip: r.draft_error }}>
                      {r.draft_error.slice(0, 40)}
                    </Typography.Text>
                  )}
                </Space>
              ),
            },
            {
              title: '字数进度',
              key: 'words',
              width: 240,
              render: (_, r) => {
                const pct =
                  r.target_words > 0 ? Math.min(100, Math.round((r.word_count / r.target_words) * 100)) : 0
                // 偏离告警阈值同章节页（0.8/1.2）；antd Progress 无 warning 态，金色 strokeColor 等价表达
                const deviated =
                  r.word_count > r.target_words * 1.2 || (r.word_count > 0 && r.word_count < r.target_words * 0.8)
                const failed = r.state === 'draft_failed'
                return (
                  <Space size={8}>
                    <Progress
                      percent={pct}
                      showInfo={false}
                      size={[110, 8]}
                      status={failed ? 'exception' : undefined}
                      strokeColor={!failed && deviated ? '#faad14' : undefined}
                    />
                    <Typography.Text type={deviated ? 'warning' : undefined} style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                      {r.word_count || '—'} / {r.target_words}
                    </Typography.Text>
                  </Space>
                )
              },
            },
            {
              title: '操作',
              key: 'op',
              width: 80,
              render: (_, r) => (
                <Button size="small" icon={<EditOutlined />} onClick={() => nav(`/projects/${pid}/chapters/${r.id}`)}>
                  编辑
                </Button>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  )
}
