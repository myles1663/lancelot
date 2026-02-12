import { useState, useRef, useEffect, useCallback } from 'react'
import { sendMessage, sendMessageWithFiles } from '@/api'
import { ChatMessage } from './ChatMessage'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  crusaderMode?: boolean
  filesCount?: number
}

export function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [sending, setSending] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(scrollToBottom, [messages, scrollToBottom])

  const timestamp = () => new Date().toLocaleTimeString('en-US', { hour12: false })

  const handleSend = async () => {
    const text = input.trim()
    if (!text && files.length === 0) return

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text || `[${files.length} file(s) attached]`,
      timestamp: timestamp(),
      filesCount: files.length,
    }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setSending(true)

    try {
      const result =
        files.length > 0
          ? await sendMessageWithFiles(text, files)
          : await sendMessage(text)

      const assistantMsg: Message = {
        id: result.request_id,
        role: 'assistant',
        content: result.response,
        timestamp: timestamp(),
        crusaderMode: result.crusader_mode,
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch (err) {
      const errorMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: `Error: ${err instanceof Error ? err.message : 'Unknown error'}`,
        timestamp: timestamp(),
      }
      setMessages((prev) => [...prev, errorMsg])
    } finally {
      setSending(false)
      setFiles([])
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <section className="bg-surface-card border border-border-default rounded-lg flex flex-col min-h-[400px] max-h-[600px]">
      <div className="px-4 py-3 border-b border-border-default">
        <h3 className="text-sm font-medium text-text-secondary uppercase tracking-wider">
          Command Interface
        </h3>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-1">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-text-muted text-sm">
            Issue a command to Lancelot
          </div>
        )}
        {messages.map((msg) => (
          <ChatMessage key={msg.id} {...msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* File chips */}
      {files.length > 0 && (
        <div className="px-4 py-2 border-t border-border-default flex flex-wrap gap-2">
          {files.map((f, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 px-2 py-1 bg-surface-input rounded text-xs text-text-secondary"
            >
              {f.name}
              <button
                onClick={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
                className="text-text-muted hover:text-state-error"
              >
                x
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="p-3 border-t border-border-default flex gap-2">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) setFiles(Array.from(e.target.files))
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          className="p-2 text-text-muted hover:text-text-primary transition-colors"
          title="Attach files"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            <path
              d="M15.2 8.46L9.06 14.6C8.3 15.36 7.28 15.79 6.22 15.79C5.16 15.79 4.14 15.36 3.38 14.6C2.62 13.84 2.19 12.82 2.19 11.76C2.19 10.7 2.62 9.68 3.38 8.92L9.52 2.78C10.02 2.28 10.7 2 11.41 2C12.12 2 12.8 2.28 13.3 2.78C13.8 3.28 14.08 3.96 14.08 4.67C14.08 5.38 13.8 6.06 13.3 6.56L7.15 12.7C6.9 12.95 6.56 13.09 6.21 13.09C5.86 13.09 5.52 12.95 5.27 12.7C5.02 12.45 4.88 12.11 4.88 11.76C4.88 11.41 5.02 11.07 5.27 10.82L10.94 5.16"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Issue command to Lancelot..."
          rows={1}
          className="flex-1 bg-surface-input border border-border-default rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-border-active resize-none"
        />
        <button
          onClick={handleSend}
          disabled={sending || (!input.trim() && files.length === 0)}
          className="px-4 py-2 bg-accent-primary text-white text-sm font-medium rounded-md hover:bg-accent-primary/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {sending ? 'Sending...' : 'Send'}
        </button>
      </div>
    </section>
  )
}
