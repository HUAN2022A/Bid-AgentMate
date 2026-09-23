/** 工作台润色/检查/评分占位页：M2-M4 逐期落地，先保路由不空。 */
import { Card, Empty, Space } from 'antd'
import { useNavigate, useParams } from 'react-router-dom'
import WorkbenchNav from '../components/workbench/WorkbenchNav'

export default function WbPlaceholderPage({ page, title }: { page: string; title: string }) {
  const { id } = useParams<{ id: string }>()
  const nav = useNavigate()
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <WorkbenchNav pid={Number(id)} active={page} />
      <Card title={title}>
        <Empty description="本页在下一期开发中落地（M2 润色 / M3 检查 / M4 评分）">
          <Card.Grid style={{ width: '100%', boxShadow: 'none', padding: 0 }}>
            <a onClick={() => nav(`/projects/${id}/workbench`)}>返回项目导入页</a>
          </Card.Grid>
        </Empty>
      </Card>
    </Space>
  )
}
