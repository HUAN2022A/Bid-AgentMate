import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, Form, Input, List, Modal, Space, Tag, Typography, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { createProject, listProjects, STATE_META, type ProjectOut } from '../api'

export default function ProjectListPage() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm()

  const { data, isLoading } = useQuery({ queryKey: ['projects'], queryFn: listProjects })

  const onCreate = async (values: { name: string; tender_no: string }) => {
    setCreating(true)
    try {
      const p = await createProject(values.name, values.tender_no ?? '')
      message.success('项目已创建')
      setOpen(false)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['projects'] })
      nav(`/projects/${p.id}`)
    } catch (e) {
      message.error(e instanceof Error ? e.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  return (
    <Card
      title="我的标书项目"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          新建项目
        </Button>
      }
    >
      <List<ProjectOut>
        loading={isLoading}
        dataSource={data}
        locale={{ emptyText: '还没有项目，点右上角新建' }}
        renderItem={(p) => {
          const meta = STATE_META[p.state] ?? { label: p.state, color: 'default' }
          return (
            <List.Item
              style={{ cursor: 'pointer' }}
              onClick={() => nav(`/projects/${p.id}`)}
              extra={<Tag color={meta.color}>{meta.label}</Tag>}
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
                <Typography.Text type={p.state === 'parse_failed' ? 'danger' : 'warning'}>
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
        <Form form={form} layout="vertical" onFinish={onCreate}>
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
            <Input placeholder="例：某电厂翻车机机器人系统" autoFocus />
          </Form.Item>
          <Form.Item name="tender_no" label="招标编号">
            <Input placeholder="选填" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
