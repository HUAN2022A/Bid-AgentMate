import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Descriptions, Space, Table, Tag, Typography, Upload, message } from 'antd'
import { DownloadOutlined, InboxOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import {
  downloadExtracted,
  getProject,
  listTenderFiles,
  uploadTender,
  STATE_META,
  type TenderFileOut,
} from '../api'

/** 解析中状态 3 秒轮询（Q17：轮询起步，接口预留 SSE 兼容） */
const POLLING_STATES = ['parsing']

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
  const canDownload = project.state !== 'created' && project.state !== 'parsing' && project.state !== 'parse_failed'

  const doUpload = async (file: File) => {
    try {
      await uploadTender(pid, file)
      message.success('已上传，开始解析')
      qc.invalidateQueries({ queryKey: ['project', pid] })
      qc.invalidateQueries({ queryKey: ['tenders', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '上传失败')
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
            {canDownload && (
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

      <Card title="招标文件">
        {canUpload && (
          <Upload.Dragger
            accept=".pdf,.docx"
            maxCount={1}
            showUploadList={false}
            customRequest={({ file }) => doUpload(file as File)}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">
              {project.state === 'parse_failed' ? '解析失败，点击或拖拽重新上传' : '点击或拖拽上传招标文件'}
            </p>
            <p className="ant-upload-hint">支持 .pdf / .docx，上传后自动解析提取全文</p>
          </Upload.Dragger>
        )}
        <Table<TenderFileOut>
          style={{ marginTop: canUpload ? 16 : 0 }}
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={tenders}
          locale={{ emptyText: '尚未上传' }}
          columns={[
            { title: '文件名', dataIndex: 'original_name' },
            { title: '类型', dataIndex: 'file_type', width: 80, render: (v) => <Tag>{v}</Tag> },
            { title: '大小', dataIndex: 'size', width: 110, render: (v) => `${(v / 1024 / 1024).toFixed(2)} MB` },
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

      {project.state === 'outline_confirmed' && (
        <Card title="下一步">
          <Typography.Paragraph type="secondary">
            大纲已确认（v{project.outline_version}）。逐章起草将在阶段 2 后续迭代上线。
          </Typography.Paragraph>
        </Card>
      )}
    </Space>
  )
}
