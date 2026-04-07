import api from './client'
import type { SocialAccount } from '../types'

export const listSocialAccounts = () =>
  api.get<SocialAccount[]>('/api/social-accounts').then((r) => r.data)

export const startDouyinConnect = () =>
  api.post<{ authorization_url: string }>('/api/social-accounts/douyin/connect').then((r) => r.data)

export const refreshSocialAccount = (socialAccountId: string) =>
  api.post<SocialAccount>(`/api/social-accounts/${socialAccountId}/refresh`).then((r) => r.data)

export const setDefaultSocialAccount = (socialAccountId: string) =>
  api.patch<SocialAccount>(`/api/social-accounts/${socialAccountId}/default`).then((r) => r.data)

export const deleteSocialAccount = (socialAccountId: string) =>
  api.delete(`/api/social-accounts/${socialAccountId}`).then((r) => r.data)
