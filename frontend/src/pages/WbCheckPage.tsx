/** 标书工作台·检查页：四页签（废标项/错别字 M3 落地；逻辑谬误/技术评分 M4）。
 *
 * 每个页签：手动触发（章节范围可选，默认全书）→ run 行级进度轮询 → findings 表格
 * （八字段，行展开全量）→ 人工确认流（属实/误报/已修复）。
 * 检查发现可能只是待人工确认的风险，不等于问题成立——确认状态列承载该语义。
 */
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Button, Card, Empty, Popconfirm, Progress, Select, Space, Spin, Table, Tabs, Tag, Typography, message,
} from 'antd'
import {
  CheckCircleOutlined, CloseCircleOutlined, DownloadOutlined, SafetyOutlined, ToolOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import {
  CHECK_TYPE_META, downloadFile, FINDING_STATUS_META, fixAllTypos, fixFinding, listCheckRuns,
  listChapters, listFindings, patchFinding, runWbCheck, SEVERITY_META, type FindingOut,
} from '../api'
import WorkbenchNav from '../components/workbench/WorkbenchNav'

type CheckType = 'disqualification' | 'typo' | 'logic'

const TAB_META: Record<CheckType, { hint: string; confirmText: string }> = {
  disqualification: {
    hint: '逐条对照招标文件的废标/否决条款裁决：pass 只进统计，risk/需人工复核 生成检查发现',
    confirmText: '确认违反风险（属实）',
  },
  typo: {
    hint: '全书逐块扫描确定性高的错别字，证据片段逐字取自原文、可在原文定位',
    confirmText: '确认属实',
  },
  logic: {
    hint: '逐章分析逻辑问题（前后矛盾/数据不一致/以偏概全等），证据片段逐字取自原文',
    confirmText: '确认属实',
  },
}

function CheckTab({ pid, checkType }: { pid: number; checkType: CheckType }) {
  const qc = useQueryClient()
  const [scopeKeys, setScopeKeys] = useState<string[]>([])
  const [triggering, setTriggering] = useState(false)
  const meta = TAB_META[checkType]

  const { data: chapters } = useQuery({ queryKey: ['chapters', pid], queryFn: () => listChapters(pid) })
  const runsQ = useQuery({
    queryKey: ['check-runs', pid, checkType],
    queryFn: () => listCheckRuns(pid, checkType),
    refetchInterval: (q) => (q.state.data?.[0]?.state === 'running' ? 2500 : false),
  })
  const latest = runsQ.data?.[0]
  const findingsQ = useQuery({
    queryKey: ['findings', pid, checkType],
    queryFn: () => listFindings(pid, checkType),
    enabled: !!latest && latest.state === 'done',
  })
  const findings = useMemo(
    () => (findingsQ.data ?? []).slice().sort((a, b) => {
      const order = { high: 0, medium: 1, low: 2 } as Record<string, number>
      return (order[a.severity] ?? 9) - (order[b.severity] ?? 9)
    }),
    [findingsQ.data],
  )

  const doRun = async () => {
    setTriggering(true)
    try {
      await runWbCheck(pid, { check_type: checkType, chapter_keys: scopeKeys })
      message.success(`${CHECK_TYPE_META[checkType]}完成`)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '检查失败')
    } finally {
      setTriggering(false)
      qc.invalidateQueries({ queryKey: ['check-runs', pid, checkType] })
      qc.invalidateQueries({ queryKey: ['findings', pid, checkType] })
    }
  }

  const confirm = async (f: FindingOut, status: string) => {
    try {
      await patchFinding(pid, f.id, status)
      qc.invalidateQueries({ queryKey: ['findings', pid, checkType] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '操作失败')
    }
  }

  // 错别字一键修复：定位替换落 fix 版本，finding 自动置已修复
  const [fixing, setFixing] = useState(false)
  const fixOne = async (f: FindingOut) => {
    setFixing(true)
    try {
      await fixFinding(pid, f.id)
      message.success(`${f.chapter_key} 已修复并落新版本`)
      qc.invalidateQueries({ queryKey: ['findings', pid, checkType] })
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '修复失败')
    } finally {
      setFixing(false)
    }
  }
  const fixAll = async () => {
    setFixing(true)
    try {
      const r = await fixAllTypos(pid)
      if (r.fixed) message.success(`已修复 ${r.fixed} 条（跳过 ${r.skipped} 条）`)
      else message.warning(r.errors?.[0] ?? '没有可修复的发现')
      qc.invalidateQueries({ queryKey: ['findings', pid, checkType] })
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '批量修复失败')
    } finally {
      setFixing(false)
    }
  }
  const fixable = findings.filter((f) => f.confirm_status === 'pending' || f.confirm_status === 'confirmed')

  const counts = useMemo(() => {
    const bySeverity: Record<string, number> = {}
    const byStatus: Record<string, number> = {}
    findings.forEach((f) => {
      bySeverity[f.severity] = (bySeverity[f.severity] ?? 0) + 1
      byStatus[f.confirm_status] = (byStatus[f.confirm_status] ?? 0) + 1
    })
    return { bySeverity, byStatus }
  }, [findings])

  const runStats = (latest?.stats ?? {}) as Record<string, unknown>
  const scopeOptions = (chapters ?? []).filter((c) => c.word_count > 0).map((c) => ({
    label: `${c.chapter_key} ${c.title}`,
    value: c.chapter_key,
  }))

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space wrap>
        <Select
          mode="multiple"
          allowClear
          style={{ minWidth: 320 }}
          placeholder="章节范围：默认全书"
          maxTagCount="responsive"
          options={scopeOptions}
          value={scopeKeys}
          onChange={setScopeKeys}
          disabled={triggering || latest?.state === 'running'}
        />
        <Popconfirm
          title={`执行${CHECK_TYPE_META[checkType]}`}
          description={
            scopeKeys.length
              ? `仅扫描选中的 ${scopeKeys.length} 个章节`
              : checkType === 'typo'
                ? '全书扫描：逐块调用 LLM，章节多时可能需要数分钟'
                : '将逐条裁决招标文件的全部废标条款'
          }
          onConfirm={doRun}
          okText="开始"
          cancelText="取消"
        >
          <Button
            type="primary"
            icon={<SafetyOutlined />}
            loading={triggering || latest?.state === 'running'}
            disabled={latest?.state === 'running'}
          >
            执行{CHECK_TYPE_META[checkType]}
          </Button>
        </Popconfirm>
        {checkType === 'typo' && fixable.length > 0 && (
          <Popconfirm
            title={`一键修复 ${fixable.length} 条错别字`}
            description={'逐条在正文中定位替换并落新版本（来源标记「错别字修复」）；正文已变化的条目自动跳过'}
            onConfirm={fixAll}
            okText="全部修复"
            cancelText="取消"
          >
            <Button icon={<ToolOutlined />} loading={fixing} disabled={triggering}>全部修复（{fixable.length}）</Button>
          </Popconfirm>
        )}
        {latest && (
          <Typography.Text type="secondary">
            最近 run #{latest.id} · {latest.state === 'done' ? '完成' : latest.state === 'failed' ? '失败' : '进行中'}
            {latest.state === 'done' && latest.finished_at && ` · ${new Date(latest.finished_at).toLocaleString()}`}
          </Typography.Text>
        )}
      </Space>

      {latest?.state === 'running' && (
        <Alert
          type="info" showIcon
          message={
            latest.progress_total > 0 ? (
              <Space>扫描进度 <Progress percent={Math.round((latest.progress / latest.progress_total) * 100)} size="small" style={{ minWidth: 180 }} />({latest.progress}/{latest.progress_total} 块)</Space>
            ) : '正在调用模型分析…'
          }
        />
      )}
      {latest?.state === 'failed' && <Alert type="error" showIcon message="检查失败" description={latest.error} />}
      {latest?.state === 'done' && latest.error && <Alert type="warning" showIcon message={latest.error} />}

      {latest?.state === 'done' && (
        <>
          <Space split="·" wrap>
            {(Object.keys(counts.bySeverity) as string[]).map((s) => (
              <Tag key={s} color={SEVERITY_META[s]?.color}>
                {SEVERITY_META[s]?.label ?? s} {counts.bySeverity[s]}
              </Tag>
            ))}
            {(Object.keys(counts.byStatus) as string[]).map((s) => (
              <Tag key={s} color={FINDING_STATUS_META[s]?.color}>
                {FINDING_STATUS_META[s]?.label ?? s} {counts.byStatus[s]}
              </Tag>
            ))}
            {checkType === 'disqualification' && 'clauses' in runStats && (
              <Typography.Text type="secondary">
                条款 {String(runStats.clauses)}：通过 {String(runStats.pass ?? 0)} · 风险 {String(runStats.risk ?? 0)} · 需人工 {String(runStats.needs_review ?? 0)}
              </Typography.Text>
            )}
            {typeof runStats.dropped === 'number' && Number(runStats.dropped) > 0 && (
              <Typography.Text type="warning">{String(runStats.dropped_note)}</Typography.Text>
            )}
          </Space>
          <Table<FindingOut>
            rowKey="id"
            size="small"
            dataSource={findings}
            loading={findingsQ.isLoading}
            pagination={{ pageSize: 20 }}
            locale={{ emptyText: '本次检查未发现问题（或尚未执行）' }}
            columns={[
              {
                title: '章节', dataIndex: 'chapter_key', width: 90,
                render: (v) => v || <Typography.Text type="secondary">全局</Typography.Text>,
              },
              {
                title: '严重度', dataIndex: 'severity', width: 80,
                filters: ['high', 'medium', 'low'].map((v) => ({ text: SEVERITY_META[v]?.label ?? v, value: v })),
                onFilter: (v, r) => r.severity === v,
                render: (v) => <Tag color={SEVERITY_META[v]?.color ?? 'default'}>{SEVERITY_META[v]?.label ?? v}</Tag>,
              },
              {
                title: '投标文件证据', dataIndex: 'bid_evidence', ellipsis: true,
                render: (v) => <Typography.Text style={{ fontSize: 12 }}>{v || '—'}</Typography.Text>,
              },
              {
                title: '分析与建议', key: 'ans', ellipsis: true,
                render: (_, r) => (
                  <Space direction="vertical" size={0} style={{ width: '100%' }}>
                    <Typography.Text style={{ fontSize: 12 }} ellipsis={{ tooltip: r.analysis }}>{r.analysis || '—'}</Typography.Text>
                    <Typography.Text type="warning" style={{ fontSize: 12 }} ellipsis={{ tooltip: r.suggestion }}>
                      {r.suggestion ? `建议：${r.suggestion}` : ''}
                    </Typography.Text>
                  </Space>
                ),
              },
              {
                title: '确认', dataIndex: 'confirm_status', width: 90,
                filters: Object.entries(FINDING_STATUS_META).map(([v, m]) => ({ text: m.label, value: v })),
                onFilter: (v, r) => r.confirm_status === v,
                render: (v) => <Tag color={FINDING_STATUS_META[v]?.color ?? 'default'}>{FINDING_STATUS_META[v]?.label ?? v}</Tag>,
              },
              {
                title: '操作', key: 'ops', width: 170,
                render: (_, r) => (
                  <Space size={4}>
                    {checkType === 'typo' && r.confirm_status !== 'fixed' && (
                      <Button size="small" type="primary" ghost icon={<ToolOutlined />} loading={fixing} onClick={() => fixOne(r)}>
                        修复
                      </Button>
                    )}
                    <Button size="small" type="text" icon={<CheckCircleOutlined />} disabled={r.confirm_status === 'confirmed'} onClick={() => confirm(r, 'confirmed')} title={meta.confirmText} />
                    <Button size="small" type="text" icon={<CloseCircleOutlined />} disabled={r.confirm_status === 'dismissed'} onClick={() => confirm(r, 'dismissed')} title="误报，忽略" />
                    {checkType !== 'typo' && (
                      <Button size="small" type="text" icon={<ToolOutlined />} disabled={r.confirm_status === 'fixed'} onClick={() => confirm(r, 'fixed')} title="已修复" />
                    )}
                  </Space>
                ),
              },
            ]}
            expandable={{
              expandedRowRender: (r) => (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  {r.tender_basis && (
                    <div>
                      <Typography.Text type="secondary">招标依据：</Typography.Text>
                      <Typography.Text mark>{r.tender_basis}</Typography.Text>
                    </div>
                  )}
                  <div>
                    <Typography.Text type="secondary">证据原文：</Typography.Text>
                    <Typography.Text>{r.bid_evidence || '—'}</Typography.Text>
                  </div>
                  <div>
                    <Typography.Text type="secondary">分析说明：</Typography.Text>
                    <Typography.Text>{r.analysis || '—'}</Typography.Text>
                  </div>
                  <div>
                    <Typography.Text type="secondary">修改建议：</Typography.Text>
                    <Typography.Text>{r.suggestion || '—'}</Typography.Text>
                  </div>
                  <Typography.Text type="secondary">
                    位置：{r.location || '全局'} · 检查类型：{CHECK_TYPE_META[r.check_type] ?? r.check_type} · 发现于 {new Date(r.created_at).toLocaleString()}
                  </Typography.Text>
                </Space>
              ),
            }}
          />
        </>
      )}
      {latest?.state !== 'done' && latest?.state !== 'running' && !latest && (
        <Empty description={meta.hint} />
      )}
      {latest?.state !== 'done' && latest?.state !== 'running' && latest?.state === 'failed' && <Spin />}
    </Space>
  )
}

export default function WbCheckPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const nav = useNavigate()

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <WorkbenchNav pid={pid} active="check" />
      <Card
        title="标书检查（检查发现 ≠ 问题成立，需人工确认）"
        extra={
          <Button
            size="small" icon={<DownloadOutlined />}
            onClick={() => downloadFile(pid, 'wb/check-report', '标书检查报告.md')}
          >
            下载检查报告
          </Button>
        }
      >
        <Tabs
          defaultActiveKey="disqualification"
          items={[
            {
              key: 'disqualification',
              label: '废标项',
              children: <CheckTab pid={pid} checkType="disqualification" />,
            },
            {
              key: 'typo',
              label: '错别字',
              children: <CheckTab pid={pid} checkType="typo" />,
            },
            {
              key: 'logic',
              label: '逻辑谬误',
              children: <CheckTab pid={pid} checkType="logic" />,
            },
            {
              key: 'scoring',
              label: '技术评分',
              children: (
                <Empty description="技术模拟评分在 M4 落地（逐评分点估分 + 手工修正）">
                  <Button onClick={() => nav(`/projects/${pid}/wb-scoring`)}>前往评分页</Button>
                </Empty>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  )
}
