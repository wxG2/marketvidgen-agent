import { reactive } from 'vue'
import type { Project } from '../types'

export const projectStore = reactive({
  project: null as Project | null,
  currentStep: 1,
})

export function setProject(project: Project | null) {
  projectStore.project = project
  projectStore.currentStep = project?.current_step ?? 1
}

export function setCurrentStep(currentStep: number) {
  projectStore.currentStep = currentStep
}
