/** 交付页：自查报告摘要 + 导出 docx（交付闭环最后一站）。 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Col, Row, Space, Statistic, Tag, Typography, message } from 'antd'
import { AuditOutlined, DownloadOutlined, FileWordOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import {
  downloadFile, getProject, runCheck, runExport, STATE_META,
  type CheckSummaryOut, type ExportSummaryOut,
} from '../api'

export default function DeliveryPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const qc = useQueryClient()
  const { data: project } = useQuery({ queryKey: ['project', pid], queryFn: () => getProject(pid) })

  const [checkResult, setCheckResult] = useState<CheckSummaryOut | null>(null)
  const [exportResult, setExportResult] = useState<ExportSummaryOut | null>(null)
  const [checking, setChecking] = useState(false)
  const [exporting, setExporting] = useState(false)

  const canOperate = ['draft_done', 'checking', 'exported'].includes(project?.state ?? '')

  const doCheck = async () => {
    setChecking(true)
    try {
      const r = await runCheck(pid)
      setCheckResult(r)
      message.success('自查完成')
      qc.invalidateQueries({ queryKey: ['project', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '自查失败')
    } finally {
      setChecking(false)
    }
  }

  const doExport = async () => {
    setExporting(true)
    try {
      const r = await runExport(pid)
      setExportResult(r)
      message.success(`已导出 ${r.chapters} 章 / 约 ${r.total_words.toLocaleString()} 字`)
      qc.invalidateQueries({ queryKey: ['project', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '导出失败')
    } finally {
      setExporting(false)
    }
  }

  if (!project) return <Card loading />
  const meta = STATE_META[project.state] ?? { label: project.state, color: 'default' }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title={
          <Space>
            自查与导出
            <Tag color={meta.color}>{meta.label}</Tag>
          </Space>
        }
      >
        {!canOperate && (
          <Typography.Text type="secondary">
            当前状态 {meta.label}，须全部章节起草完成（draft_done）后才能自查与导出。
          </Typography.Text>
        )}
        <Space size={16}>
          <Button icon={<AuditOutlined />} loading={checking} disabled={!canOperate} onClick={doCheck}>
            执行自查
          </Button>
          <Button type="primary" icon={<FileWordOutlined />} loading={exporting} disabled={!canOperate} onClick={doExport}>
            导出技术文件 docx
          </Button>
        </Space>
      </Card>

      {checkResult && (
        <Card
          title="自查结果"
          extra={
            <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadFile(pid, 'check/report', '自查报告.md')}>
              下载完整报告
            </Button>
          }
        >
          <Row gutter={24}>
            <Col><Statistic title="技术评分点覆盖" value={checkResult.covered} suffix={`/ ${checkResult.tech_items}`} /></Col>
            <Col><Statistic title="★硬指标命中" value={checkResult.star_hit} suffix={`/ ${checkResult.star_reqs}`} /></Col>
            <Col><Statistic title="[待补]缺口" value={checkResult.pending_gaps} /></Col>
            <Col>
              <Statistic
                title="疑似报价混入"
                value={checkResult.price_hits}
                valueStyle={{ color: checkResult.price_hits ? '#faad14' : '#3f8600' }}
              />
            </Col>
          </Row>
        </Card>
      )}

      {exportResult && (
        <Card
          title="导出结果"
          extra={
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              onClick={() => downloadFile(pid, 'export/docx', '技术文件.docx')}
            >
              下载技术文件.docx
            </Button>
          }
        >
          <Row gutter={24}>
            <Col><Statistic title="章节数" value={exportResult.chapters} /></Col>
            <Col><Statistic title="总字数" value={exportResult.total_words} /></Col>
            <Col>
              <Statistic
                title="[待补]高亮"
                value={exportResult.pending_gaps}
                valueStyle={{ color: exportResult.pending_gaps ? '#faad14' : '#3f8600' }}
              />
            </Col>
          </Row>
          <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
            目录已设为打开文档时自动更新页码（若未更新，全选按 F9）。[待补] 处已黄色高亮，补完后可重新导出覆盖。
          </Typography.Paragraph>
        </Card>
      )}
    </Space>
  )
}
