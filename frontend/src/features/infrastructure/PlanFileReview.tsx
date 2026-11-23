import { useState } from 'react'
import type { InfrastructurePlan } from './contracts'

export function PlanFileReview({ plan }: { plan: InfrastructurePlan }) {
  const files = [
    ...plan.files,
    ...plan.comparison.removed.map(path => ({ path, change: 'removed' as const, content: null, hash: null })),
  ]
  const changes = files.filter(file => file.change !== 'unchanged')
  const [changesOnly, setChangesOnly] = useState(changes.length > 0)
  const [selectedPath, setSelectedPath] = useState(changes[0]?.path ?? files[0]?.path)
  const visible = changesOnly ? changes : files
  const selected = visible.find(file => file.path === selectedPath) ?? visible[0]

  return <section className="file-review" aria-label="Generated file review">
    <div className="file-review-summary">
      <b>Generated files</b>
      <span>{plan.comparison.added.length} added · {plan.comparison.changed.length} changed · {plan.comparison.removed.length} removed</span>
      {changes.length > 0 && changes.length < files.length && <label><input type="checkbox" checked={changesOnly} onChange={event => setChangesOnly(event.target.checked)} /> Changes only</label>}
    </div>
    <div className="file-review-body">
      <div className="file-review-list" role="group" aria-label="Generated files">
        {visible.map(file => <button key={file.path} type="button" aria-pressed={file.path === selected?.path} className={file.path === selected?.path ? 'selected' : ''} onClick={() => setSelectedPath(file.path)}>
          <code>{file.path}</code><span className={`file-change file-change-${file.change}`}>{file.change}</span>
        </button>)}
      </div>
      {selected ? <div className="file-content" aria-label="Generated file preview">
        <div><code>{selected.path}</code>{selected.hash && <span>SHA-256 {selected.hash.slice(0, 12)}…</span>}</div>
        {selected.content == null ? <p>This bundle removes the file.</p> : <pre tabIndex={0} aria-label={`Contents of ${selected.path}`}>{selected.content}</pre>}
      </div> : <p>No generated files.</p>}
    </div>
  </section>
}
