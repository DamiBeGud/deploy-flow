import { useState } from 'react'
import type { InfrastructurePlan } from './contracts'

export function PlanFileReview({ plan }: { plan: InfrastructurePlan }) {
  const [selectedPath, setSelectedPath] = useState(plan.files[0]?.path || '')
  const selected = plan.files.find(file => file.path === selectedPath) || plan.files[0]
  const comparison = plan.comparison

  return <section className="file-review" aria-label="Generated file review">
    <div className="file-review-summary">
      <b>Generated files</b>
      <span>{plan.files.length} files · {comparison.added.length} added · {comparison.changed.length} changed · {comparison.removed.length} removed</span>
    </div>
    <div className="file-review-body">
      <div className="file-review-list" role="tablist" aria-label="Generated files">
        {plan.files.map(file => <button key={file.path} type="button" role="tab" aria-selected={file.path === selected?.path} className={file.path === selected?.path ? 'selected' : ''} onClick={() => setSelectedPath(file.path)}>
          <code>{file.path}</code><span className={`file-change file-change-${file.change}`}>{file.change}</span>
        </button>)}
        {comparison.removed.map(path => <div className="removed-file" key={path}><code>{path}</code><span className="file-change file-change-removed">removed</span></div>)}
      </div>
      {selected ? <div className="file-content" role="tabpanel"><div><code>{selected.path}</code><span>SHA-256 {selected.hash.slice(0, 12)}…</span></div><pre>{selected.content}</pre></div> : <p>No generated files.</p>}
    </div>
  </section>
}
