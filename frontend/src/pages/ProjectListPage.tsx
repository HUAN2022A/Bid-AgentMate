import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Form, Input, List, Modal, Radio, Space, Tag, Typography, message } from 'antd'
import { PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import {
  createProject,
  createSampleProject,
  listProjects,
  PROJECT_MODE_META,
  STATE_META,
  type ProjectOut,
} from '../api'

export default function ProjectListPage() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [sampling, setSampling] = useState(false)
  const [form] = Form.useForm()

  const { data, isLoading } = useQuery({ queryKey: ['projects'], queryFn: listProjects })

  const onCreate = async (values: { name: string; tender_no: string; mode: 'draft' | 'workbench' }) => {
    setCreating(true)
    try {
      const p = await createProject(values.name, values.tender_no ?? '', values.mode ?? 'draft')
      message.success('项目已创建')
      setOpen(false)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['projects'] })
      nav(p.mode === 'workbench' ? `/projects/${p.id}/workbench` : `/projects/${p.id}`)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const onSample = async () => {
    setSampling(true)
    try {
      const p = await createSampleProject()
      message.success('样例项目已创建并开始解析')
      qc.invalidateQueries({ queryKey: ['projects'] })
      nav(`/projects/${p.id}/workbench`)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '创建样例项目失败')
    } finally {
      setSampling(false)
    }
  }

  return (
    <Card
      title="我的标书项目"
      extra={
        <Space>
          <Button icon={<ThunderboltOutlined />} loading={sampling} onClick={onSample}>
            一键样例项目
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新建项目
          </Button>
        </Space>
      }
    >
      <List<ProjectOut>
        loading={isLoading}
        dataSource={data}
        locale={{ emptyText: '还没有项目，点右上角新建；或用「一键样例项目」先看完整流程' }}
        renderItem={(p) => {
          const meta = STATE_META[p.state] ?? { label: p.state, color: 'default' }
          const mode = PROJECT_MODE_META[p.mode] ?? { label: p.mode, color: 'default' }
          return (
            <List.Item
              style={{ cursor: 'pointer' }}
              onClick={() => nav(p.mode === 'workbench' ? `/projects/${p.id}/workbench` : `/projects/${p.id}`)}
              extra={
                <Space>
                  <Tag color={mode.color}>{mode.label}</Tag>
                  <Tag color={meta.color}>{meta.label}</Tag>
                </Space>
              }
            >
              <List.Item.Meta
                title={p.name}
                description={
                  <Space split="·">
                    {p.tender_no && <span>招标编号 {p.tender_no}</span>}
                    <span>创建于 {new Date(p.created_at).toLocaleString()}</span>
                  </Space>
                }
              />
              {p.parse_error && (
                <Typography.Text type={p.state === 'parse_failed' || p.state === 'wb_parse_failed' ? 'danger' : 'warning'}>
                  {p.parse_error}
                </Typography.Text>
              )}
            </List.Item>
          )
        }}
      />
      <Modal
        title="新建标书项目"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={onCreate} initialValues={{ mode: 'draft' }}>
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
            <Input placeholder="例：某电厂翻车机机器人系统" autoFocus />
          </Form.Item>
          <Form.Item name="tender_no" label="招标编号">
            <Input placeholder="选填" />
          </Form.Item>
          <Form.Item name="mode" label="项目模式">
            <Radio.Group>
              <Space direction="vertical">
                <Radio value="draft">起草模式 —— 上传招标文件，AI 逐章起草标书</Radio>
                <Radio value="workbench">工作台模式 —— 上传已有投标文件，润色/检查/评分</Radio>
              </Space>
            </Radio.Group>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
