import { reactive } from 'vue'

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

export const timelineStore = reactive({
  clips: [] as TLClip[],
  zoomLevel: 100,
  playheadMs: 0,
})

export function setClips(clips: TLClip[]) {
  timelineStore.clips = clips
}

export function addClip(clip: TLClip) {
  timelineStore.clips.push(clip)
}

export function removeClip(id: string) {
  timelineStore.clips = timelineStore.clips.filter((clip) => clip.id !== id)
}

export function updateClip(id: string, data: Partial<TLClip>) {
  timelineStore.clips = timelineStore.clips.map((clip) => (clip.id === id ? { ...clip, ...data } : clip))
}

export function setZoomLevel(zoomLevel: number) {
  timelineStore.zoomLevel = zoomLevel
}

export function setPlayheadMs(playheadMs: number) {
  timelineStore.playheadMs = playheadMs
}
