import type { ReactNode } from 'react'
import { CircleAlert, CloudCog } from 'lucide-react'

export function Brand({ dark = false }: { dark?: boolean }) {
  return <div className={`brand ${dark ? 'brand-dark' : ''}`}><span className="brand-mark"><CloudCog size={19} /></span><span>DeployFlow</span></div>
}
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span className="field-label">{label}</span>{children}</label>
}
export function ErrorNotice({ message }: { message: string }) {
  return message ? <p className="form-error" role="alert"><CircleAlert size={16} />{message}</p> : null
}

