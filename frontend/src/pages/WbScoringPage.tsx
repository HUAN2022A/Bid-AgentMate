/** 标书工作台·技术模拟评分页：逐技术评分点 AI 估分（证据/扣分理由/改进建议）
 * + 用户手工修正（重新估分只覆盖 AI 字段，修正保留）+ 汇总统计。
 * 免责约束：模拟估分 ≠ 正式评标结果，页面常驻提示。
 */
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Button, Card, Col, InputNumber, Popconfirm, Row, Space, Statistic, Table, Tag, Typography, message,
} from 'antd'
import { AimOutlined, CheckOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import {
  ESTIMATE_STATUS_META, listCheckRuns, listScoreEstimates, patchScoreEstimate, runWbCheck,
  type ScoreEstimateOut,
} from '../api'
import WorkbenchNav from '../components/workbench/WorkbenchNav'

export default function WbScoringPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const qc = useQueryClient()
  const [triggering, setTriggering] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [draftScore, setDraftScore] = useState<number | null>(null)

  const runsQ = useQuery({
    queryKey: ['check-runs', pid, 'scoring'],
    queryFn: () => listCheckRuns(pid, 'scoring'),
    refetchInterval: (q) => (q.state.data?.[0]?.state === 'running' ? 2500 : false),
  })
  const latest = runsQ.data?.[0]
  const estimatesQ = useQuery({
    queryKey: ['score-estimates', pid],
    queryFn: () => listScoreEstimates(pid),
    enabled: !!latest && latest.state === 'done',
  })
  const estimates = useMemo(() => estimatesQ.data ?? [], [estimatesQ.data])

  const runEstimate = async () => {
    setTriggering(true)
    try {
      await runWbCheck(pid, { check_type: 'scoring' })
      message.success('模拟估分完成')
    } catch (e) {
      message.error(e instanceof Error ? e.message : '估分失败')
    } finally {
      setTriggering(false)
      qc.invalidateQueries({ queryKey: ['check-runs', pid, 'scoring'] })
      qc.invalidateQueries({ queryKey: ['score-estimates', pid] })
    }
  }

  const save = async (row: ScoreEstimateOut, score: number | null, status?: string) => {
    try {
      await patchScoreEstimate(pid, row.id, { manual_score: score, status })
      message.success(`${row.item_key} 已更新`)
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['score-estimates', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败')
    }
  }

  const totals = useMemo(() => {
    const max = estimates.reduce((a, e) => a + e.max_score, 0)
    // 修正分优先于 AI 估分
    const est = estimates.reduce((a, e) => a + (e.manual_score ?? e.estimated_score), 0)
    const adjusted = estimates.filter((e) => e.status !== 'ai').length
    return { max, est: Math.round(est * 10) / 10, adjusted }
  }, [estimates])

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <WorkbenchNav pid={pid} active="scoring" />
      <Card
        title="技术模拟评分（逐评分点估分，可手工修正）"
        extra={
          <Popconfirm
            title="重新模拟估分"
            description="站在评委角度逐技术评分点估分；只覆盖 AI 字段，你的手工修正保留"
            onConfirm={runEstimate}
            okText="开始估分"
            cancelText="取消"
          >
            <Button
              type="primary" icon={<AimOutlined />}
              loading={triggering || latest?.state === 'running'}
              disabled={latest?.state === 'running'}
            >
              {estimates.length ? '重新估分' : 'AI 估分'}
            </Button>
          </Popconfirm>
        }
      >
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message="模拟估分仅供投标方改进参考，不代表正式评标结果"
        />
        {latest?.state === 'running' && <Alert type="info" showIcon style={{ marginBottom: 12 }} message="正在逐评分点估分…" />}
        {latest?.state === 'failed' && (
          <Alert type="error" showIcon style={{ marginBottom: 12 }} message="估分失败" description={latest.error} />
        )}

        {estimates.length > 0 && (
          <Row gutter={16} style={{ marginBottom: 12 }}>
            <Col span={8}><Statistic title="技术卷满分" value={totals.max} suffix="分" /></Col>
            <Col span={8}>
              <Statistic
                title="预估合计（含手工修正）" value={totals.est} suffix={`/ ${totals.max} 分`}
                valueStyle={{ color: totals.est / Math.max(totals.max, 1) >= 0.8 ? '#3f8600' : '#cf1322' }}
              />
            </Col>
            <Col span={8}><Statistic title="已修正/确认" value={totals.adjusted} suffix={`/ ${estimates.length} 项`} /></Col>
          </Row>
        )}

        <Table<ScoreEstimateOut>
          rowKey="id"
          size="small"
          dataSource={estimates}
          loading={estimatesQ.isLoading}
          pagination={false}
          locale={{ emptyText: latest?.state === 'done' ? '解析结果中没有技术类评分点' : '尚未估分（点右上角 AI 估分）' }}
          columns={[
            { title: '评分点', key: 'k', width: 200, render: (_, r) => (
              <Space direction="vertical" size={0}>
                <Typography.Text strong>{r.item_key} {r.item}</Typography.Text>
                <Tag color={ESTIMATE_STATUS_META[r.status]?.color}>{ESTIMATE_STATUS_META[r.status]?.label ?? r.status}</Tag>
              </Space>
            ) },
            { title: '满分', dataIndex: 'max_score', width: 70 },
            { title: 'AI 估分', dataIndex: 'estimated_score', width: 80,
              render: (v) => <Typography.Text type="secondary">{v}</Typography.Text> },
            { title: '得分（手工修正）', key: 'score', width: 190,
              render: (_, r) => editing === r.id ? (
                <Space>
                  <InputNumber min={0} max={r.max_score} step={0.5} value={draftScore} onChange={(v) => setDraftScore(v)} />
                  <Button size="small" type="primary" onClick={() => save(r, draftScore)}>存</Button>
                  <Button size="small" onClick={() => setEditing(null)}>取消</Button>
                </Space>
              ) : (
                <Space>
                  <Typography.Text strong style={{ fontSize: 16 }}>
                    {r.manual_score ?? r.estimated_score}
                  </Typography.Text>
                  {r.manual_score !== null && <Tag color="warning">修正</Tag>}
                  {r.status !== 'adjusted' && (
                    <Button size="small" type="link" onClick={() => { setEditing(r.id); setDraftScore(r.manual_score ?? r.estimated_score) }}>
                      改
                    </Button>
                  )}
                </Space>
              ) },
            { title: '扣分理由 / 改进建议', key: 'ded', ellipsis: true, render: (_, r) => (
              <Space direction="vertical" size={0} style={{ width: '100%' }}>
                <Typography.Text type="danger" style={{ fontSize: 12 }} ellipsis={{ tooltip: r.deduction_reasons }}>
                  {r.deduction_reasons || '无明显扣分项'}
                </Typography.Text>
                <Typography.Text style={{ fontSize: 12 }} ellipsis={{ tooltip: r.improvement_advice }}>
                  {r.improvement_advice ? `建议：${r.improvement_advice}` : ''}
                </Typography.Text>
              </Space>
            ) },
            { title: '操作', key: 'ops', width: 90, render: (_, r) => (
              <Button size="small" icon={<CheckOutlined />} disabled={r.status === 'confirmed'} onClick={() => save(r, r.manual_score ?? r.estimated_score, 'confirmed')}>
                确认
              </Button>
            ) },
          ]}
          expandable={{
            expandedRowRender: (r) => (
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <div>
                  <Typography.Text type="secondary">评分标准：</Typography.Text>
                  <Typography.Text mark>{r.criteria_original}</Typography.Text>
                </div>
                <div>
                  <Typography.Text type="secondary">投标证据：</Typography.Text>
                  <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{r.evidence || '—'}</Typography.Text>
                </div>
                <div>
                  <Typography.Text type="secondary">扣分理由：</Typography.Text>
                  <Typography.Text>{r.deduction_reasons || '—'}</Typography.Text>
                </div>
                <div>
                  <Typography.Text type="secondary">改进建议：</Typography.Text>
                  <Typography.Text>{r.improvement_advice || '—'}</Typography.Text>
                </div>
                {r.manual_note && <div><Typography.Text type="secondary">修正说明：</Typography.Text>{r.manual_note}</div>}
              </Space>
            ),
          }}
        />
      </Card>
    </Space>
  )
}
