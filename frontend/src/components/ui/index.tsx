/**
 * UI primitives.
 *
 * Hand-vendored on Radix rather than pulled in with the shadcn CLI, which is interactive and
 * rewrites project config. Same components, same look, reproducible from source.
 */
import * as DialogPrimitive from '@radix-ui/react-dialog'
import * as ProgressPrimitive from '@radix-ui/react-progress'
import * as SelectPrimitive from '@radix-ui/react-select'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import * as TabsPrimitive from '@radix-ui/react-tabs'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { Check, ChevronDown, Loader2, X } from 'lucide-react'
import * as React from 'react'
import { cn } from '../../lib/utils'

// ---------------------------------------------------------------- Button

const BUTTON_VARIANTS = {
  primary:
    'bg-[var(--accent)] text-white hover:brightness-110 active:brightness-95 shadow-sm',
  secondary:
    'bg-[var(--bg-raised)] border border-[var(--border)] hover:bg-[var(--bg-sunken)]',
  ghost: 'hover:bg-[var(--bg-sunken)]',
  danger: 'bg-red-600 text-white hover:bg-red-700',
  subtle: 'bg-[var(--bg-sunken)] hover:bg-[var(--border)]',
} as const

const BUTTON_SIZES = {
  sm: 'h-8 px-2.5 text-[13px] gap-1.5 rounded-md',
  md: 'h-9 px-3.5 text-sm gap-2 rounded-lg',
  lg: 'h-11 px-5 text-[15px] gap-2 rounded-lg',
  icon: 'h-9 w-9 rounded-lg justify-center',
  'icon-sm': 'h-7 w-7 rounded-md justify-center',
} as const

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof BUTTON_VARIANTS
  size?: keyof typeof BUTTON_SIZES
  loading?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'secondary', size = 'md', loading, disabled, children, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center font-medium transition-colors select-none',
        'disabled:opacity-50 disabled:pointer-events-none',
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />}
      {children}
    </button>
  )
})

// ---------------------------------------------------------------- Input / Textarea

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          'h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] px-3 text-sm',
          'placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/40',
          'focus:border-[var(--accent)] transition-shadow disabled:opacity-60',
          className,
        )}
        {...props}
      />
    )
  },
)

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      className={cn(
        'w-full rounded-lg border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-2 text-sm',
        'placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/40',
        'focus:border-[var(--accent)] resize-y transition-shadow',
        className,
      )}
      {...props}
    />
  )
})

// ---------------------------------------------------------------- Card

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('surface', className)} {...props} />
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-4 py-3 border-b border-[var(--border)]', className)} {...props} />
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn('font-semibold text-sm tracking-tight', className)} {...props} />
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-4', className)} {...props} />
}

// ---------------------------------------------------------------- Badge

const BADGE_TONES = {
  neutral: 'bg-[var(--bg-sunken)] text-[var(--text-muted)]',
  accent: 'bg-[var(--accent-soft)] text-[var(--accent)]',
  success: 'bg-emerald-500/12 text-emerald-600 dark:text-emerald-400',
  warning: 'bg-amber-500/12 text-amber-600 dark:text-amber-400',
  danger: 'bg-red-500/12 text-red-600 dark:text-red-400',
} as const

export function Badge({
  tone = 'neutral',
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: keyof typeof BADGE_TONES }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium',
        BADGE_TONES[tone],
        className,
      )}
      {...props}
    />
  )
}

// ---------------------------------------------------------------- Dialog

export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogClose = DialogPrimitive.Close

export function DialogContent({
  className,
  children,
  hideClose,
  ...props
}: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & { hideClose?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[2px] data-[state=open]:animate-in" />
      <DialogPrimitive.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2',
          'surface shadow-2xl p-5 max-h-[85vh] overflow-y-auto animate-in-up',
          className,
        )}
        {...props}
      >
        {children}
        {!hideClose && (
          <DialogPrimitive.Close className="absolute right-3.5 top-3.5 rounded-md p-1 opacity-60 hover:opacity-100 hover:bg-[var(--bg-sunken)] transition">
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}

export function DialogTitle({ className, ...props }: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn('text-base font-semibold mb-1', className)} {...props} />
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn('text-sm text-[var(--text-muted)] mb-4', className)}
      {...props}
    />
  )
}

// ---------------------------------------------------------------- Tabs

export const Tabs = TabsPrimitive.Root

export function TabsList({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn('inline-flex items-center gap-0.5 rounded-lg bg-[var(--bg-sunken)] p-0.5', className)}
      {...props}
    />
  )
}

export function TabsTrigger({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors',
        'text-[var(--text-muted)] hover:text-[var(--text)]',
        'data-[state=active]:bg-[var(--bg-raised)] data-[state=active]:text-[var(--text)] data-[state=active]:shadow-sm',
        className,
      )}
      {...props}
    />
  )
}

export const TabsContent = TabsPrimitive.Content

// ---------------------------------------------------------------- Select

export const Select = SelectPrimitive.Root
export const SelectValue = SelectPrimitive.Value

export function SelectTrigger({
  className,
  children,
  ...props
}: React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        'inline-flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-[var(--border)]',
        'bg-[var(--bg-raised)] px-3 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/40',
        'disabled:opacity-60 data-[placeholder]:text-[var(--text-muted)]',
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon>
        <ChevronDown className="h-4 w-4 opacity-50" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
}

export function SelectContent({
  className,
  children,
  ...props
}: React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position="popper"
        sideOffset={4}
        className={cn(
          'z-50 max-h-72 min-w-[10rem] overflow-hidden rounded-lg border border-[var(--border)]',
          'bg-[var(--bg-raised)] shadow-xl',
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  )
}

export function SelectItem({
  className,
  children,
  ...props
}: React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        'relative flex cursor-pointer select-none items-center rounded-md py-1.5 pl-7 pr-2 text-sm',
        'outline-none data-[highlighted]:bg-[var(--bg-sunken)] data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  )
}

// ---------------------------------------------------------------- Switch

export function Switch({ className, ...props }: React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent',
        'transition-colors focus-visible:outline-none data-[state=checked]:bg-[var(--accent)]',
        'data-[state=unchecked]:bg-[var(--border)] disabled:opacity-50',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block h-4 w-4 rounded-full bg-white shadow ring-0 transition-transform data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0" />
    </SwitchPrimitive.Root>
  )
}

// ---------------------------------------------------------------- Progress

export function Progress({
  value,
  className,
  tone = 'accent',
}: {
  value: number
  className?: string
  tone?: 'accent' | 'success' | 'danger'
}) {
  const colour =
    tone === 'success' ? 'bg-emerald-500' : tone === 'danger' ? 'bg-red-500' : 'bg-[var(--accent)]'
  return (
    <ProgressPrimitive.Root
      value={value}
      className={cn('relative h-1.5 w-full overflow-hidden rounded-full bg-[var(--bg-sunken)]', className)}
    >
      <ProgressPrimitive.Indicator
        className={cn('h-full transition-transform duration-300 ease-out', colour)}
        style={{ transform: `translateX(-${100 - Math.max(0, Math.min(100, value))}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}

// ---------------------------------------------------------------- Tooltip

export const TooltipProvider = TooltipPrimitive.Provider

export function Tooltip({ children, label, side = 'top' }: { children: React.ReactNode; label: React.ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  if (!label) return <>{children}</>
  return (
    <TooltipPrimitive.Root delayDuration={400}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className="z-50 rounded-md bg-[var(--color-ink-900)] px-2 py-1 text-[11px] text-white shadow-lg dark:bg-[var(--color-ink-200)] dark:text-[var(--color-ink-950)]"
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}

// ---------------------------------------------------------------- misc

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('h-4 w-4 animate-spin', className)} />
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-[var(--bg-sunken)]', className)} />
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon?: React.ComponentType<{ className?: string }>
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center py-14 px-6 text-center">
      {Icon && (
        <div className="mb-3 rounded-xl bg-[var(--bg-sunken)] p-3">
          <Icon className="h-6 w-6 text-[var(--text-muted)]" />
        </div>
      )}
      <p className="font-medium">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-sm text-[var(--text-muted)]">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-[var(--border)] bg-[var(--bg-sunken)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">
      {children}
    </kbd>
  )
}

export function Field({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string
  hint?: React.ReactNode
  children: React.ReactNode
  htmlFor?: string
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[13px] font-medium">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-[var(--text-muted)]">{hint}</p>}
    </div>
  )
}
