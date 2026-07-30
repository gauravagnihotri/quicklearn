import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Toaster } from 'sonner'
import { TooltipProvider } from './components/ui'
import { ApiError } from './lib/api'
import { router } from './router'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Retrying a 4xx just delays showing the user an error they need to act on.
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
        return failureCount < 2
      },
    },
    mutations: { retry: false },
  },
})

const container = document.getElementById('root')
if (!container) throw new Error('Missing #root element')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={400}>
        <RouterProvider router={router} />
        <Toaster
          position="bottom-right"
          closeButton
          toastOptions={{
            classNames: {
              toast:
                '!bg-[var(--bg-raised)] !text-[var(--text)] !border-[var(--border)] !shadow-xl',
              description: '!text-[var(--text-muted)]',
            },
          }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
