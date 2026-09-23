/** 选区浮动动作条（TipTap v3 BubbleMenu）：动作按钮 + 可选指令 + 对齐评分点的评分项选择。 */
import { useCallback, useState } from 'react'
import type { Editor } from '@tiptap/core'
import { BubbleMenu, type BubbleMenuProps } from '@tiptap/react/menus'
import { Button, Input, Select, Space } from 'antd'
import { COPILOT_ACTION_LABEL, type CopilotActionName, type ScoringItemOut } from '../../api'

interface Props {
  editor: Editor
  /** 仅 idle 阶段可再次触发 */
  enabled: boolean
  scoringItems: ScoringItemOut[]
  defaultScoringKey: string
  onAction: (action: CopilotActionName, opts: { instruction: string; scoringKey: string }) => void
}

type ShouldShow = NonNullable<BubbleMenuProps['shouldShow']>

const SELECTION_ACTIONS: CopilotActionName[] = ['rewrite', 'expand', 'compress', 'align_scoring', 'tabulate']
// v3 BubbleMenu 在 options/shouldShow 身份变化时会 dispatch updateOptions，保持引用稳定避免每次渲染都触发
const MENU_OPTIONS: BubbleMenuProps['options'] = { placement: 'top-start', offset: 8 }

export default function CopilotBubble({ editor, enabled, scoringItems, defaultScoringKey, onAction }: Props) {
  const [instruction, setInstruction] = useState('')
  const [scoringKey, setScoringKey] = useState(defaultScoringKey)
  const shouldShow = useCallback<ShouldShow>(({ state }) => enabled && !state.selection.empty, [enabled])

  const fire = (action: CopilotActionName) => onAction(action, { instruction: instruction.trim(), scoringKey })

  return (
    <BubbleMenu
      editor={editor}
      updateDelay={150}
      options={MENU_OPTIONS}
      shouldShow={shouldShow}
      style={{
        background: '#fff',
        border: '1px solid #d9d9d9',
        borderRadius: 8,
        padding: 8,
        boxShadow: '0 4px 12px rgba(0,0,0,.12)',
        maxWidth: 640,
      }}
    >
      <Space direction="vertical" size={6}>
        <Space wrap size={4}>
          {SELECTION_ACTIONS.map((a) => (
            <Button
              key={a}
              size="small"
              type={a === 'align_scoring' ? 'primary' : 'default'}
              ghost={a === 'align_scoring'}
              disabled={a === 'align_scoring' && !scoringKey}
              onClick={() => fire(a)}
            >
              {COPILOT_ACTION_LABEL[a]}
            </Button>
          ))}
        </Space>
        <Space size={4}>
          <Input
            size="small"
            style={{ width: 280 }}
            placeholder="补充指令（可选），回车 = 重写"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onPressEnter={() => fire('rewrite')}
          />
          {scoringItems.length > 0 && (
            <Select
              size="small"
              style={{ width: 240 }}
              value={scoringKey || undefined}
              placeholder="对齐的评分点"
              onChange={(v) => setScoringKey(v)}
              options={scoringItems.map((s) => ({
                value: s.item_key,
                label: `${s.item_key} ${s.item}（${s.score} 分）`,
              }))}
            />
          )}
        </Space>
      </Space>
    </BubbleMenu>
  )
}
