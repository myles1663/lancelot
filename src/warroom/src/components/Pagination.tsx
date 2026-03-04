interface PaginationProps {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
  totalItems?: number
}

export function Pagination({
  currentPage,
  totalPages,
  onPageChange,
  totalItems,
}: PaginationProps) {
  if (totalPages <= 1) return null

  const pages: (number | '...')[] = []
  if (totalPages <= 5) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else {
    pages.push(1)
    if (currentPage > 3) pages.push('...')
    for (
      let i = Math.max(2, currentPage - 1);
      i <= Math.min(totalPages - 1, currentPage + 1);
      i++
    ) {
      pages.push(i)
    }
    if (currentPage < totalPages - 2) pages.push('...')
    pages.push(totalPages)
  }

  const btnBase =
    'px-3 py-1.5 text-xs rounded-md border transition-colors disabled:opacity-40 disabled:cursor-not-allowed'
  const btnInactive =
    'border-border-default text-text-secondary hover:text-text-primary hover:border-border-active'
  const btnActive = 'border-accent-primary bg-accent-primary/10 text-accent-primary'

  return (
    <div className="flex items-center justify-between mt-4">
      <div className="text-xs text-text-muted">
        {totalItems !== undefined && `${totalItems.toLocaleString()} total`}
      </div>
      <div className="flex items-center gap-1">
        <button
          className={`${btnBase} ${btnInactive}`}
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage <= 1}
        >
          Prev
        </button>
        {pages.map((p, i) =>
          p === '...' ? (
            <span key={`e${i}`} className="px-1 text-xs text-text-muted">
              ...
            </span>
          ) : (
            <button
              key={p}
              className={`${btnBase} ${p === currentPage ? btnActive : btnInactive}`}
              onClick={() => onPageChange(p)}
            >
              {p}
            </button>
          ),
        )}
        <button
          className={`${btnBase} ${btnInactive}`}
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage >= totalPages}
        >
          Next
        </button>
      </div>
    </div>
  )
}
