/** 素材库页：卡片列表（类型/检索过滤）+ 资信文件上传入库 + 手动增改删。 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Upload, message,
} from 'antd'
import { DeleteOutlined, EditOutlined, InboxOutlined, PlusOutlined } from '@ant-design/icons'
import {
  createMaterial, deleteMaterial, ingestMaterial, listMaterials, updateMaterial,
  type MaterialOut,
} from '../api'

const TYPE_META: Record<string, { label: string; color: string }> = {
  case: { label: '案例', color: 'blue' },
  person: { label: '人员', color: 'purple' },
  credential: { label: '资质获奖', color: 'gold' },
  ip: { label: '知识产权', color: 'geekblue' },
  capability: { label: '研发能力', color: 'cyan' },
}

export default function MaterialsPage() {
  const qc = useQueryClient()
  const [typeFilter, setTypeFilter] = useState<string>()
  const [q, setQ] = useState('')
  const [editTarget, setEditTarget] = useState<Partial<MaterialOut> | null>(null)
  const [gaps, setGaps] = useState<string[]>([])
  const [form] = Form.useForm()

  const { data, isLoading } = useQuery({
    queryKey: ['materials', typeFilter, q],
    queryFn: () => listMaterials(typeFilter, q || undefined),
  })

  const doIngest = async (file: File) => {
    try {
      const r = await ingestMaterial(file)
      const s = r.stats
      message.success(
        `入库完成：案例 ${s.cases ?? 0}、人员 ${s.people ?? 0}、获奖 ${s.awards ?? 0}、专利/软著 ${s.patents ?? 0}`
      )
      setGaps(r.gaps)
      qc.invalidateQueries({ queryKey: ['materials'] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '入库失败')
    }
  }

  const onSave = async (values: { type: string; name: string; summary: string; tags: string }) => {
    try {
      if (editTarget?.id) {
        await updateMaterial(editTarget.id, values)
        message.success('已更新')
      } else {
        await createMaterial(values)
        message.success('已新建')
      }
      setEditTarget(null)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['materials'] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : '保存失败')
    }
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title="公司素材库"
        extra={
          <Space>
            <Upload
              accept=".docx"
              maxCount={1}
              showUploadList={false}
              customRequest={({ file }) => doIngest(file as File)}
            >
              <Button icon={<InboxOutlined />}>上传资信文件入库</Button>
            </Upload>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                setEditTarget({})
                form.resetFields()
              }}
            >
              手动新建
            </Button>
          </Space>
        }
      >
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            allowClear
            placeholder="全部类型"
            style={{ width: 140 }}
            value={typeFilter}
            onChange={setTypeFilter}
            options={Object.entries(TYPE_META).map(([v, m]) => ({ value: v, label: m.label }))}
          />
          <Input.Search
            placeholder="搜名称/摘要/标签"
            style={{ width: 280 }}
            allowClear
            onSearch={setQ}
          />
        </Space>
        {gaps.length > 0 && (
          <Alert
            type="warning"
            showIcon
            closable
            style={{ marginBottom: 12 }}
            message={`资格缺口提示（${gaps.length} 条，投标时需补证明材料）`}
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {gaps.slice(0, 10).map((g, i) => <li key={i}>{g}</li>)}
              </ul>
            }
            onClose={() => setGaps([])}
          />
        )}
        <Table<MaterialOut>
          rowKey="id"
          loading={isLoading}
          size="middle"
          dataSource={data}
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: '素材库为空，上传资信文件或手动新建' }}
          columns={[
            {
              title: '类型', dataIndex: 'type', width: 100,
              render: (v) => <Tag color={TYPE_META[v]?.color}>{TYPE_META[v]?.label ?? v}</Tag>,
            },
            { title: '名称', dataIndex: 'name', width: 260, ellipsis: true },
            { title: '摘要', dataIndex: 'summary', ellipsis: true },
            {
              title: '资格缺口',
              key: 'gaps',
              width: 110,
              render: (_, m) => {
                const n = JSON.stringify(m.qual_extra).match(/待补/g)?.length ?? 0
                return n ? <Tag color="orange">{n} 处待补</Tag> : <Tag color="success">齐</Tag>
              },
            },
            { title: '来源', dataIndex: 'source', width: 160, ellipsis: true },
            {
              title: '操作',
              key: 'op',
              width: 130,
              render: (_, m) => (
                <Space>
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => {
                      setEditTarget(m)
                      form.setFieldsValue({ type: m.type, name: m.name, summary: m.summary, tags: m.tags })
                    }}
                  />
                  <Popconfirm
                    title="删除该素材卡？"
                    onConfirm={async () => {
                      await deleteMaterial(m.id)
                      message.success('已删除')
                      qc.invalidateQueries({ queryKey: ['materials'] })
                    }}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={editTarget?.id ? '编辑素材卡' : '新建素材卡'}
        open={editTarget !== null}
        onCancel={() => setEditTarget(null)}
        onOk={() => form.submit()}
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={onSave}>
          <Form.Item name="type" label="类型" rules={[{ required: true, message: '请选择类型' }]}>
            <Select options={Object.entries(TYPE_META).map(([v, m]) => ({ value: v, label: m.label }))} />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="summary" label="摘要（起草注入用）">
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item name="tags" label="标签（逗号分隔，检索用）">
            <Input placeholder="翻车机,摘复钩,电厂" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
