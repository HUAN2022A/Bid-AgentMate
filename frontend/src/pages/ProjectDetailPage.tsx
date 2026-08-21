import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Descriptions, Popconfirm, Space, Table, Tag, Typography, Upload, message } from 'antd'
import { DownloadOutlined, InboxOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import {
  downloadExtracted,
  getProject,
  listTenderFiles,
  triggerParse,
  uploadTender,
  STATE_META,
  type TenderFileOut,
} from '../api'

/** 解析中状态 3 秒轮询（Q17：轮询起步，接口预留 SSE 兼容） */
const POLLING_STATES = ['parsing']

const ROLE_META: Record<string, { label: string; color: string; hint: string }> = {
  main: { label: '招标文件', color: 'blue', hint: '评分办法/废标/商务（必传）' },
  spec: { label: '技术规范书', color: 'purple', hint: '★技术参数主来源（强烈建议）' },
  attachment: { label: '附件', color: 'default', hint: '澄清/补遗/图纸说明等' },
}

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const nav = useNavigate()
  const qc = useQueryClient()

  const { data: project, isLoading } = useQuery({
    queryKey: ['project', pid],
    queryFn: () => getProject(pid),
    refetchInterval: (query) =>
      POLLING_STATES.includes(query.state.data?.state ?? '') ? 3000 : false,
  })
  const { data: tenders } = useQuery({
    queryKey: ['tenders', pid],
    queryFn: () => listTenderFiles(pid),
    refetchInterval: () => (POLLING_STATES.includes(project?.state ?? '') ? 3000 : false),
  })

  if (isLoading || !project) return <Card loading />

  const meta = STATE_META[project.state] ?? { label: project.state, color: 'default' }
  const canUpload = project.state === 'created' || project.state === 'parse_failed'
  const hasMain = (tenders ?? []).some((t) => t.role === 'main')
  const hasSpec = (tenders ?? []).some((t) => t.role === 'spec')
  const hasFiles = (tenders ?? []).length > 0

  const doUpload = async (file: File, role: string) => {
    try {
      await uploadTender(pid, file, role)
      message.success(`${ROLE_META[role].label}已上传`)
      qc.invalidateQueries({ queryKey: ['tenders', pid] })
      qc.invalidateQueries({ queryKey: ['project', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '上传失败')
    }
  }

  const doParse = async () => {
    try {
      await triggerParse(pid)
      message.success('已开始解析（评分点拆解 + 大纲生成，约需几分钟）')
      qc.invalidateQueries({ queryKey: ['project', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '触发解析失败')
    }
  }

  const statsOf = (t: TenderFileOut): string => {
    try {
      const s = JSON.parse(t.extract_stats)
      const parts = []
      if (s.pages) parts.push(`${s.pages} 页`)
      parts.push(`${s.lines} 行`, `${s.tables} 表格`, `${s.chars} 字符`)
      return parts.join(' / ')
    } catch {
      return ''
    }
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title={
          <Space>
            {project.name}
            <Tag color={meta.color}>{meta.label}</Tag>
          </Space>
        }
        extra={
          <Space>
            {project.state === 'parsing' && <Button icon={<ReloadOutlined spin />} disabled>解析中…</Button>}
            {hasFiles && (
              <Button icon={<DownloadOutlined />} onClick={() => downloadExtracted(pid)}>
                下载提取全文
              </Button>
            )}
          </Space>
        }
      >
        <Descriptions size="small" column={2}>
          <Descriptions.Item label="招标编号">{project.tender_no || '—'}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{new Date(project.created_at).toLocaleString()}</Descriptions.Item>
        </Descriptions>
        {project.parse_error && (
          <Alert
            style={{ marginTop: 12 }}
            type={project.state === 'parse_failed' ? 'error' : 'warning'}
            message={project.parse_error}
            showIcon
          />
        )}
      </Card>

      <Card
        title="招标文件"
        extra={
          canUpload && (
            <Popconfirm
              title="开始解析全部已上传文件？"
              description="评分点拆解 + 大纲生成，约需几分钟"
              onConfirm={doParse}
              okText="开始解析"
              cancelText="再传几份"
            >
              <Button type="primary" icon={<PlayCircleOutlined />} disabled={!hasMain}>
                开始解析
              </Button>
            </Popconfirm>
          )
        }
      >
        {canUpload && !hasMain && (
          <Alert type="info" showIcon style={{ marginBottom: 12 }} message="请先上传招标文件正文，可随后补传技术规范书与附件，全部传完后点右上角「开始解析」" />
        )}
        {canUpload && hasMain && !hasSpec && (
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="尚未上传技术规范书——缺少它 ★技术参数提取将不完整" />
        )}
        {canUpload && (
          <Space size={16} wrap style={{ marginBottom: 16 }}>
            {(['main', 'spec', 'attachment'] as const).map((role) => (
              <Upload.Dragger
                key={role}
                accept=".pdf,.docx"
                maxCount={1}
                showUploadList={false}
                customRequest={({ file }) => doUpload(file as File, role)}
                style={{ width: 260, padding: '8px 0' }}
              >
                <p className="ant-upload-drag-icon" style={{ fontSize: 28 }}><InboxOutlined /></p>
                <p className="ant-upload-text" style={{ fontSize: 14 }}>
                  <Tag color={ROLE_META[role].color}>{ROLE_META[role].label}</Tag>
                </p>
                <p className="ant-upload-hint" style={{ fontSize: 12 }}>{ROLE_META[role].hint}</p>
              </Upload.Dragger>
            ))}
          </Space>
        )}
        <Table<TenderFileOut>
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={tenders}
          locale={{ emptyText: '尚未上传' }}
          columns={[
            { title: '角色', dataIndex: 'role', width: 110, render: (v) => <Tag color={ROLE_META[v]?.color}>{ROLE_META[v]?.label ?? v}</Tag> },
            { title: '文件名', dataIndex: 'original_name' },
            { title: '类型', dataIndex: 'file_type', width: 70, render: (v) => <Tag>{v}</Tag> },
            { title: '大小', dataIndex: 'size', width: 100, render: (v) => `${(v / 1024 / 1024).toFixed(2)} MB` },
            {
              title: '提取统计',
              key: 'stats',
              render: (_, t) => (t.extracted ? statsOf(t) : <Typography.Text type="secondary">—</Typography.Text>),
            },
          ]}
        />
      </Card>

      {project.state === 'outline_pending' && (
        <Card title="下一步">
          <Typography.Paragraph type="secondary">
            招标文件解析完成，评分点已拆解。请核对并确认大纲——这是唯一起草前的人工确认点。
          </Typography.Paragraph>
          <Button type="primary" onClick={() => nav(`/projects/${pid}/outline`)}>
            去确认大纲
          </Button>
        </Card>
      )}

      {(project.state === 'outline_confirmed' || project.state === 'drafting' || project.state === 'draft_done') && (
        <Card title="下一步">
          <Typography.Paragraph type="secondary">
            {project.state === 'outline_confirmed' && `大纲已确认（v${project.outline_version}），可以开始逐章 AI 起草。`}
            {project.state === 'drafting' && '章节起草中，可进入章节页查看进度。'}
            {project.state === 'draft_done' && '全部章节起草完成，请逐章审阅编辑。'}
          </Typography.Paragraph>
          <Space>
            <Button type="primary" onClick={() => nav(`/projects/${pid}/chapters`)}>
              进入章节起草
            </Button>
            {project.state === 'draft_done' && (
              <Button onClick={() => nav(`/projects/${pid}/delivery`)}>
                去自查与导出
              </Button>
            )}
          </Space>
        </Card>
      )}

      {project.state === 'exported' && (
        <Card title="下一步">
          <Typography.Paragraph type="secondary">
            技术文件已导出。可重新自查、补插 [待补] 材料后再次导出覆盖。
          </Typography.Paragraph>
          <Space>
            <Button onClick={() => nav(`/projects/${pid}/chapters`)}>返回章节</Button>
            <Button type="primary" onClick={() => nav(`/projects/${pid}/delivery`)}>
              去自查与导出
            </Button>
          </Space>
        </Card>
      )}
    </Space>
  )
}
