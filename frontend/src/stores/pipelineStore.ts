import { reactive } from 'vue'
import type { AgentExecution, PipelineRun, PipelineUsageSummary } from '../types'

export const pipelineStore = reactive({
  isAutoMode: true,
  currentRun: null as PipelineRun | null,
  agentExecutions: [] as AgentExecution[],
  usageSummary: null as PipelineUsageSummary | null,
})

export function setAutoMode(isAutoMode: boolean) {
  pipelineStore.isAutoMode = isAutoMode
}

export function setCurrentRun(currentRun: PipelineRun | null) {
  pipelineStore.currentRun = currentRun
}

export function setAgentExecutions(agentExecutions: AgentExecution[]) {
  pipelineStore.agentExecutions = agentExecutions
}

export function setUsageSummary(usageSummary: PipelineUsageSummary | null) {
  pipelineStore.usageSummary = usageSummary
}

export function resetPipeline() {
  pipelineStore.currentRun = null
  pipelineStore.agentExecutions = []
  pipelineStore.usageSummary = null
}
