/** 工作台子导航：导入 / 润色 / 检查 / 评分 四页共用。 */
import { Button, Space } from 'antd'
import {
  ArrowLeftOutlined, FileTextOutlined, FundProjectionScreenOutlined, SafetyOutlined, SettingOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

const ITEMS = [
  { key: 'import', label: '项目导入', path: '', icon: <SettingOutlined /> },
  { key: 'polish', label: '标书润色', path: '/wb-polish', icon: <FileTextOutlined /> },
  { key: 'check', label: '标书检查', path: '/wb-check', icon: <SafetyOutlined /> },
  { key: 'scoring', label: '技术评分', path: '/wb-scoring', icon: <FundProjectionScreenOutlined /> },
] as const

export default function WorkbenchNav({ pid, active }: { pid: number; active: string }) {
  const nav = useNavigate()
  return (
    <Space>
      <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => nav('/')}>
        项目列表
      </Button>
      {ITEMS.map((it) => (
        <Button
          key={it.key}
          size="small"
          type={active === it.key ? 'primary' : 'default'}
          icon={it.icon}
          onClick={() => nav(`/projects/${pid}${it.path}`)}
        >
          {it.label}
        </Button>
      ))}
    </Space>
  )
}
