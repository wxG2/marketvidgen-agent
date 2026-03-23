import { create } from 'zustand'

export type TrackType = 'video' | 'audio' | 'subtitle'

export interface TLClip {
  id: string
  generatedVideoId: string | null
  assetId: string | null
  trackType: TrackType
  trackIndex: number
  positionMs: number
  durationMs: number
  sortOrder: number
  label: string | null
  videoUrl: string | null
  thumbnailUrl: string | null
  filename: string | null
}

interface TimelineStore {
  clips: TLClip[]
  setClips: (clips: TLClip[]) => void
  addClip: (clip: TLClip) => void
  removeClip: (id: string) => void
  updateClip: (id: string, data: Partial<TLClip>) => void
  zoomLevel: number
  setZoomLevel: (z: number) => void
  playheadMs: number
  setPlayheadMs: (ms: number) => void
}

export const useTimelineStore = create<TimelineStore>((set) => ({
  clips: [],
  setClips: (clips) => set({ clips }),
  addClip: (clip) => set((s) => ({ clips: [...s.clips, clip] })),
  removeClip: (id) => set((s) => ({ clips: s.clips.filter((c) => c.id !== id) })),
  updateClip: (id, data) =>
    set((s) => ({
      clips: s.clips.map((c) => (c.id === id ? { ...c, ...data } : c)),
    })),
  zoomLevel: 100,
  setZoomLevel: (zoomLevel) => set({ zoomLevel }),
  playheadMs: 0,
  setPlayheadMs: (playheadMs) => set({ playheadMs }),
}))
