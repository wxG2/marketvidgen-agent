import { reactive } from 'vue'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface ToastItem {
  id: string
  type: ToastType
  message: string
}

export const toasts = reactive<ToastItem[]>([])

export function removeToast(id: string) {
  const index = toasts.findIndex((item) => item.id === id)
  if (index >= 0) toasts.splice(index, 1)
}

export function toast(type: ToastType, message: string, duration = 4000) {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  toasts.push({ id, type, message })
  window.setTimeout(() => removeToast(id), duration)
}

export function useToast() {
  return { toasts, toast, removeToast }
}
