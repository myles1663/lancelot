import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { emitWarRoomNotification } from '@/utils/notifications'

interface ChatMessageProps {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  crusaderMode?: boolean
  filesCount?: number
}

export function ChatMessage({ role, content, timestamp, crusaderMode, filesCount }: ChatMessageProps) {
  const isUser = role === 'user'

  async function handleSecureDownload(event: React.MouseEvent<HTMLAnchorElement>, href: string) {
    const url = new URL(href, window.location.origin)
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/files/')) {
      return
    }

    event.preventDefault()

    const response = await fetch(url.toString(), { credentials: 'include' })
    if (!response.ok) {
      throw new Error(`Download failed with status ${response.status}`)
    }

    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') || ''
    const dispositionMatch = disposition.match(/filename="?([^"]+)"?/)
    const filename = dispositionMatch?.[1] || decodeURIComponent(url.pathname.split('/').pop() || 'download')
    const blobUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-3`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-3 ${
          isUser
            ? 'bg-accent-primary/15 border border-accent-primary/30'
            : crusaderMode
              ? 'bg-accent-secondary/10 border border-accent-secondary/30'
              : 'bg-surface-card-elevated border border-border-default'
        }`}
      >
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
            {isUser ? 'Commander' : 'Lancelot'}
          </span>
          {crusaderMode && !isUser && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-accent-secondary/20 text-accent-secondary font-mono">
              CRUSADER
            </span>
          )}
          {filesCount && filesCount > 0 && (
            <span className="text-[9px] px-1 py-0.5 rounded bg-surface-input text-text-muted font-mono">
              {filesCount} file{filesCount > 1 ? 's' : ''}
            </span>
          )}
        </div>
        {isUser ? (
          <p className="text-sm text-text-primary whitespace-pre-wrap">{content}</p>
        ) : (
          <div className="text-sm text-text-primary prose prose-sm prose-invert max-w-none
            prose-headings:text-text-primary prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-1
            prose-p:my-1.5 prose-p:leading-relaxed
            prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5
            prose-strong:text-accent-primary prose-strong:font-semibold
            prose-table:text-xs prose-th:px-2 prose-th:py-1 prose-th:text-left prose-th:border-b prose-th:border-border-default
            prose-td:px-2 prose-td:py-1 prose-td:border-b prose-td:border-border-default/50
            prose-code:text-accent-secondary prose-code:text-xs prose-code:bg-surface-input prose-code:px-1 prose-code:py-0.5 prose-code:rounded
            prose-pre:bg-surface-input prose-pre:rounded-md prose-pre:p-3
            prose-a:text-accent-primary prose-a:underline
            prose-hr:border-border-default prose-hr:my-3
            prose-blockquote:border-l-2 prose-blockquote:border-accent-primary/50 prose-blockquote:pl-3 prose-blockquote:italic
          ">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children, ...props }) => (
                  <a
                    {...props}
                    href={href}
                    onClick={(event) => {
                      if (!href) return
                      void handleSecureDownload(event, href).catch((error) => {
                        const reason = error instanceof Error ? error.message : 'Unknown error'
                        emitWarRoomNotification(`Workspace download failed: ${reason}`, 'high')
                      })
                    }}
                  >
                    {children}
                  </a>
                ),
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
        )}
        <span className="text-[10px] text-text-muted font-mono mt-1 block">{timestamp}</span>
      </div>
    </div>
  )
}
