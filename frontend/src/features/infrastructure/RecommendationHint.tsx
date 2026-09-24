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
    <span><b>{overridden ? 'User override' : recommendation.source}</b> · {Math.round(recommendation.confidence * 100)}% · {recommendation.explanation}{evidence ? ` · ${evidence}` : ''}</span>
    {overridden ? <button type="button" onClick={onReset}>Reset to recommendation</button> : null}
  </div>
}
