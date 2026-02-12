interface ChatMessageProps {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  crusaderMode?: boolean
  filesCount?: number
}

export function ChatMessage({ role, content, timestamp, crusaderMode, filesCount }: ChatMessageProps) {
  const isUser = role === 'user'

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
        <p className="text-sm text-text-primary whitespace-pre-wrap">{content}</p>
        <span className="text-[10px] text-text-muted font-mono mt-1 block">{timestamp}</span>
      </div>
    </div>
  )
}
