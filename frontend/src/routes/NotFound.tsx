import { Link } from '@tanstack/react-router'
import { Compass } from 'lucide-react'
import { Button, EmptyState } from '../components/ui'

export function NotFound() {
  return (
    <div className="grid h-full place-items-center p-6">
      <EmptyState
        icon={Compass}
        title="This page doesn't exist"
        description="The link may be stale, or the item was deleted."
        action={
          <Link to="/">
            <Button variant="primary">Back to dashboard</Button>
          </Link>
        }
      />
    </div>
  )
}
