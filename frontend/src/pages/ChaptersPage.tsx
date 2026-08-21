/** 章节列表页：起草进度总览 + 逐章状态 + 进入编辑器。 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Popconfirm, Progress, Space, Table, Tag, Typography, message } from 'antd'
import { EditOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { draftAllChapters, getProject, listChapters, STATE_META, type ChapterOut } from '../api'

const CH_STATE_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待起草', color: 'default' },
  drafting: { label: '起草中', color: 'processing' },
  draft_done: { label: '起草完成', color: 'cyan' },
  draft_failed: { label: '起草失败', color: 'error' },
  edited: { label: '已编辑', color: 'success' },
}

export default function ChaptersPage() {
  const { id } = useParams<{ id: string }>()
  const pid = Number(id)
  const nav = useNavigate()
  const qc = useQueryClient()

  const { data: project } = useQuery({
    queryKey: ['project', pid],
    queryFn: () => getProject(pid),
    refetchInterval: (q) => (q.state.data?.state === 'drafting' ? 3000 : false),
  })
  const { data: chapters, isLoading } = useQuery({
    queryKey: ['chapters', pid],
    queryFn: () => listChapters(pid),
    refetchInterval: () => (project?.state === 'drafting' ? 3000 : false),
  })

  const canDraft = project?.state === 'outline_confirmed' || project?.state === 'draft_done'
  const done = (chapters ?? []).filter((c) => ['draft_done', 'edited'].includes(c.state)).length
  const failed = (chapters ?? []).filter((c) => c.state === 'draft_failed').length
  const total = (chapters ?? []).length
  const pct = total ? Math.round((done / total) * 100) : 0

  const doDraftAll = async () => {
    try {
      const r = await draftAllChapters(pid)
      message.success(`已派发 ${r.dispatched} 个章节起草任务`)
      qc.invalidateQueries({ queryKey: ['project', pid] })
      qc.invalidateQueries({ queryKey: ['chapters', pid] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '派发失败')
    }
  }

  return (
    <Card
      title={
        <Space>
          章节起草
          {project && <Tag color={STATE_META[project.state]?.color}>{STATE_META[project.state]?.label}</Tag>}
        </Space>
      }
      extra={
        <Space>
          {total > 0 && (
            <Progress
              type="circle"
              size={36}
              percent={pct}
              format={() => `${done}/${total}`}
              status={failed ? 'exception' : undefined}
            />
          )}
          {canDraft && (
            <Popconfirm
              title="对全部待起草章节执行 AI 起草？"
              description="每章约 1-3 分钟，起草中可离开页面"
              onConfirm={doDraftAll}
              okText="开始起草"
              cancelText="取消"
            >
              <Button type="primary" icon={<ThunderboltOutlined />}>
                {failed ? `重起草（含 ${failed} 失败）` : '全部起草'}
              </Button>
            </Popconfirm>
          )}
        </Space>
      }
    >
      <Table<ChapterOut>
        rowKey="id"
        loading={isLoading}
        size="middle"
        pagination={false}
        dataSource={chapters}
        locale={{ emptyText: '尚无章节（确认大纲并点右上角起草）' }}
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
            title: '挂接评分点',
            dataIndex: 'scoring_keys',
            width: 130,
            render: (v) => (v ? v.split(',').map((k: string) => <Tag key={k} color="blue">{k}</Tag>) : '—'),
          },
          {
            title: '状态',
            dataIndex: 'state',
            width: 110,
            render: (v, r) => (
              <Space direction="vertical" size={0}>
                <Tag color={CH_STATE_META[v]?.color}>{CH_STATE_META[v]?.label ?? v}</Tag>
                {v === 'draft_failed' && (
                  <Typography.Text type="danger" style={{ fontSize: 12 }} ellipsis={{ tooltip: r.draft_error }}>
                    {r.draft_error.slice(0, 40)}
                  </Typography.Text>
                )}
              </Space>
            ),
          },
          {
            title: '字数',
            key: 'words',
            width: 120,
            render: (_, r) => (
              <Typography.Text type={r.word_count > r.target_words * 1.2 || (r.word_count > 0 && r.word_count < r.target_words * 0.8) ? 'warning' : undefined}>
                {r.word_count || '—'} / {r.target_words}
              </Typography.Text>
            ),
          },
          {
            title: '操作',
            key: 'op',
            width: 100,
            render: (_, r) => (
              <Button size="small" icon={<EditOutlined />} onClick={() => nav(`/projects/${pid}/chapters/${r.id}`)}>
                编辑
              </Button>
            ),
          },
        ]}
      />
    </Card>
  )
}
