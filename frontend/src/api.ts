export type Receipt = {
  receipt_id: number
  submission_id: string
  status: 'confirmed' | 'pending'
  receipt_date: string | null
  total_cents: number | null
  subtotal_cents: number | null
  payer_id: string | null
  uploader_id: string
  note: string
  created_by: string | null
  updated_by: string | null
  updated_at: string | null
  photo_urls: string[]
  shares: Record<string, number>
  created: boolean
}

export type Settlement = {
  month: string
  strategy: 'group_netting' | 'receipt_links'
  my_member_id: string
  my_display_name: string
  my_balance_cents: number
  balances: { member_id: string; display_name: string; balance_cents: number }[]
  transfers: { direction: 'pay' | 'receive'; counterparty_member_id: string; counterparty_display_name: string; amount_cents: number }[]
}

export type Member = { member_id: string; display_name: string }

export type ApiError = Error & { code?: string; details?: unknown }

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, credentials: 'same-origin' })
  if (response.ok) return response.json() as Promise<T>

  let payload: { error?: { code?: string; message?: string; details?: unknown } } | undefined
  try { payload = await response.json() } catch { /* Use the HTTP error below. */ }
  const error = new Error(payload?.error?.message ?? `请求失败（${response.status}）`) as ApiError
  error.code = payload?.error?.code
  error.details = payload?.error?.details
  throw error
}

export const getMembers = () => api<{ members: Member[] }>('/members')
export const getMe = () => api<{ member: Member }>('/auth/me')
export const login = (member_id: string, password: string) => api<{ member: Member }>('/auth/login', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ member_id, password }),
})
export const logout = () => api<void>('/auth/logout', { method: 'POST' })
export const getReceipt = (id: string) => api<Receipt>(`/receipts/${encodeURIComponent(id)}`)
export const getSettlement = (month: string, strategy: Settlement['strategy']) =>
  api<Settlement>(`/settlements/${encodeURIComponent(month)}?strategy=${strategy}`)

export function postReceipt(input: { payerId: string; note: string; photos: File[]; key: string }) {
  const body = new FormData()
  body.append('payer_id', input.payerId)
  body.append('note', input.note)
  input.photos.forEach((photo) => body.append('photos', photo, photo.name))
  return api<Receipt>('/receipts', {
    method: 'POST',
    headers: { 'Idempotency-Key': input.key },
    body,
  })
}

export function money(cents: number | null | undefined) {
  if (cents == null) return '—'
  return new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD' }).format(cents / 100)
}
