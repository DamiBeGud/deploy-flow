import type { FieldRecommendation } from './contracts'

const sameValue = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right)

export function RecommendationHint({ recommendation, effectiveValue, onReset }: {
  recommendation?: FieldRecommendation
  effectiveValue: unknown
  onReset: () => void
}) {
  if (!recommendation) return null
  const overridden = !sameValue(effectiveValue, recommendation.recommended_value)
  const evidence = recommendation.evidence
    ? `${recommendation.evidence.file_path}${recommendation.evidence.line_start ? `:${recommendation.evidence.line_start}` : ''}`
    : null

  return <div className={`recommendation-hint ${overridden ? 'recommendation-overridden' : ''}`}>
    <div><b>{overridden ? 'User override' : recommendation.source}</b>
      {(recommendation.explanation || evidence) && <details><summary>Evidence</summary>
        <span>{recommendation.explanation}{evidence ? ` · ${evidence}` : ''}{recommendation.confidence > 0 ? ` · Confidence ${Math.round(recommendation.confidence * 100)}%` : ''}</span>
      </details>}
    </div>
    {overridden ? <button type="button" onClick={onReset}>Reset to recommendation</button> : null}
    {Boolean(recommendation.alternatives?.length) && <details>
      <summary>Other source commands</summary>
      <ul>{recommendation.alternatives?.map((item, index) => <li key={index}>
        <code>{item.argv.join(' ')}</code> · {item.context} · {item.evidence.file_path}:{item.evidence.line_start} · {item.reason}
      </li>)}</ul>
    </details>}
  </div>
}
