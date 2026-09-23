/** ★条款响应入口：选一条技术要求（★优先排序）→ 生成「招标要求 vs 我方响应」对照表 → 预览后插入光标处。 */
import { useMemo, useState } from 'react'
import { Input, Modal, Select, Space, Typography } from 'antd'
import type { TechRequirementOut } from '../../api'

interface Props {
  open: boolean
  requirements: TechRequirementOut[]
  onCancel: () => void
  onSubmit: (requirementKey: string, instruction: string) => void
}

export default function StarResponseModal({ open, requirements, onCancel, onSubmit }: Props) {
  const [key, setKey] = useState<string>()
  const [instruction, setInstruction] = useState('')
  const options = useMemo(
    () =>
      [...requirements]
        .sort((a, b) => Number(b.star) - Number(a.star))
        .map((r) => ({
          value: r.req_key,
          label: `${r.star ? '★ ' : ''}${r.req_key} ${r.requirement_original.slice(0, 60)}`,
        })),
    [requirements],
  )

  return (
    <Modal
      open={open}
      title="插入 ★条款响应"
      onCancel={onCancel}
      okText="生成"
      okButtonProps={{ disabled: !key }}
      onOk={() => key && onSubmit(key, instruction.trim())}
    >
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Select
          showSearch
          style={{ width: '100%' }}
          placeholder={requirements.length ? '选择要响应的技术要求' : '本项目尚无技术需求解析结果'}
          value={key}
          onChange={setKey}
          optionFilterProp="label"
          options={options}
        />
        <Input placeholder="补充指令（可选）" value={instruction} onChange={(e) => setInstruction(e.target.value)} />
        <Typography.Text type="secondary">
          生成「招标要求 vs 我方响应」对照表，预览确认后插入当前光标位置；我方能力事实只引用素材库，缺失处标 [待补]。
        </Typography.Text>
      </Space>
    </Modal>
  )
}
