import api from './client'
import type { AuthUser, LoginRequest, RegisterRequest } from '../types'

export const register = (payload: RegisterRequest) =>
  api.post<AuthUser>('/api/auth/register', payload).then(r => r.data)

export const login = (payload: LoginRequest) =>
  api.post<AuthUser>('/api/auth/login', payload).then(r => r.data)

export const logout = () =>
  api.post('/api/auth/logout').then(r => r.data)

export const getMe = () =>
  api.get<AuthUser>('/api/auth/me').then(r => r.data)

export const listUsers = () =>
  api.get<AuthUser[]>('/api/admin/users').then(r => r.data)

export const updateUser = (userId: string, payload: { is_active?: boolean; password?: string }) =>
  api.patch<AuthUser>(`/api/admin/users/${userId}`, payload).then(r => r.data)
