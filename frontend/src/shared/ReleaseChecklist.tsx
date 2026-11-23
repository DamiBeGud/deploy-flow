import type { ReleaseCheck } from './deploymentStatus'

export function ReleaseChecklist({ label, checks }: { label: string; checks: ReleaseCheck[] }) {
  return <ol className="release-checklist" aria-label={label}>
    {checks.map(check => <li key={check.label} data-tone={check.tone}><span>{check.label}</span><strong>{check.result}</strong></li>)}
  </ol>
}
