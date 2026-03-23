import { create } from 'zustand'
import type { PipelineRun, AgentExecution, PipelineUsageSummary } from '../types'

interface PipelineStore {
  isAutoMode: boolean
  setAutoMode: (v: boolean) => void
  currentRun: PipelineRun | null
  setCurrentRun: (run: PipelineRun | null) => void
  agentExecutions: AgentExecution[]
  setAgentExecutions: (execs: AgentExecution[]) => void
  usageSummary: PipelineUsageSummary | null
  setUsageSummary: (summary: PipelineUsageSummary | null) => void
  reset: () => void
}

export const usePipelineStore = create<PipelineStore>((set) => ({
  isAutoMode: true,
  setAutoMode: (isAutoMode) => set({ isAutoMode }),
  currentRun: null,
  setCurrentRun: (currentRun) => set({ currentRun }),
  agentExecutions: [],
  setAgentExecutions: (agentExecutions) => set({ agentExecutions }),
  usageSummary: null,
  setUsageSummary: (usageSummary) => set({ usageSummary }),
  reset: () => set({ currentRun: null, agentExecutions: [], usageSummary: null }),
}))
