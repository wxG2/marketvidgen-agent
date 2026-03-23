import { create } from 'zustand'
import type { Project } from '../types'

interface ProjectStore {
  project: Project | null
  setProject: (p: Project | null) => void
  currentStep: number
  setCurrentStep: (step: number) => void
}

export const useProjectStore = create<ProjectStore>((set) => ({
  project: null,
  setProject: (project) => set({ project, currentStep: project?.current_step ?? 1 }),
  currentStep: 1,
  setCurrentStep: (currentStep) => set({ currentStep }),
}))
