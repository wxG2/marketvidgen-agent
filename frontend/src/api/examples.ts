import api from './client'
import type { ExampleCategoryResponse } from '../types'

export const listExamples = () =>
  api.get<ExampleCategoryResponse>('/api/examples').then((r) => r.data)
