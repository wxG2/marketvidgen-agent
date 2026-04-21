<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { getSelectedVideos, getGeneratedVideoUrl } from '../../api/generation'
import { getTimeline, saveTimeline, uploadTimelineAsset } from '../../api/timeline'
import { addClip, removeClip, setClips, timelineStore, type TLClip, type TrackType } from '../../stores/timelineStore'
import type { GeneratedVideo, TimelineAsset, TimelineClip } from '../../types'
import { toast } from '../../composables/useToast'

const props = defineProps<{
  projectId: string
}>()

const selectedVideos = ref<GeneratedVideo[]>([])
const assets = ref<TimelineAsset[]>([])
const saving = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)

const clips = computed(() => timelineStore.clips)
const tracks: TrackType[] = ['video', 'audio', 'subtitle']

function mapClip(clip: TimelineClip): TLClip {
  return {
    id: clip.id,
    generatedVideoId: clip.generated_video_id,
    assetId: clip.asset_id,
    trackType: clip.track_type,
    trackIndex: clip.track_index,
    positionMs: clip.position_ms,
    durationMs: clip.duration_ms,
    sortOrder: clip.sort_order,
    label: clip.label,
    videoUrl: clip.video_url,
    thumbnailUrl: clip.thumbnail_url,
    filename: clip.filename,
  }
}

async function refresh() {
  const [videos, timeline] = await Promise.all([
    getSelectedVideos(props.projectId).catch(() => []),
    getTimeline(props.projectId).catch(() => null),
  ])
  selectedVideos.value = videos
  assets.value = timeline?.assets || []
  setClips((timeline?.clips || []).map(mapClip))
}

function addVideo(video: GeneratedVideo) {
  addClip({
    id: `local-${Date.now()}-${video.id}`,
    generatedVideoId: video.id,
    assetId: null,
    trackType: 'video',
    trackIndex: 0,
    positionMs: clips.value.filter((clip) => clip.trackType === 'video').reduce((sum, clip) => sum + clip.durationMs, 0),
    durationMs: Math.round((video.duration_seconds || 5) * 1000),
    sortOrder: clips.value.length,
    label: video.material_filename || video.prompt_text?.slice(0, 24) || '生成片段',
    videoUrl: video.video_url,
    thumbnailUrl: video.thumbnail_url,
    filename: video.material_filename,
  })
}

function addAsset(asset: TimelineAsset) {
  addClip({
    id: `local-${Date.now()}-${asset.id}`,
    generatedVideoId: null,
    assetId: asset.id,
    trackType: asset.asset_type,
    trackIndex: 0,
    positionMs: clips.value.filter((clip) => clip.trackType === asset.asset_type).reduce((sum, clip) => sum + clip.durationMs, 0),
    durationMs: asset.duration_ms || 5000,
    sortOrder: clips.value.length,
    label: asset.filename,
    videoUrl: asset.file_url,
    thumbnailUrl: null,
    filename: asset.filename,
  })
}

async function handleAsset(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return

  uploading.value = true
  uploadProgress.value = 0
  try {
    const asset = await uploadTimelineAsset(props.projectId, file, (pct) => {
      uploadProgress.value = pct
    })
    assets.value.push(asset)
    toast('success', '素材已上传到时间线')
  } catch {
    toast('error', '上传时间线素材失败')
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function save() {
  saving.value = true
  try {
    await saveTimeline(props.projectId, clips.value.map((clip, index) => ({
      generated_video_id: clip.generatedVideoId,
      asset_id: clip.assetId,
      track_type: clip.trackType,
      track_index: clip.trackIndex,
      position_ms: clip.positionMs,
      duration_ms: clip.durationMs,
      sort_order: index,
      label: clip.label,
    })))
    toast('success', '时间线已保存')
    await refresh()
  } catch {
    toast('error', '保存时间线失败')
  } finally {
    saving.value = false
  }
}

function clipUrl(clip: TLClip) {
  if (clip.generatedVideoId) return getGeneratedVideoUrl(clip.generatedVideoId)
  return clip.videoUrl || ''
}

onMounted(refresh)
watch(() => props.projectId, refresh)
</script>

<template>
  <section class="grid h-full grid-cols-[300px_1fr] overflow-hidden">
    <aside class="overflow-auto border-r border-[#d8c9ad] bg-[#fff8ec] p-4">
      <div class="mb-5 flex items-center justify-between">
        <h2 class="font-semibold">剪辑素材</h2>
        <button type="button" class="rounded-lg bg-[#7e9d53] px-3 py-2 text-xs text-white disabled:opacity-50" :disabled="saving" @click="save">
          {{ saving ? '保存中' : '保存' }}
        </button>
      </div>

      <label class="mb-5 block rounded-lg border border-dashed border-[#cdbb97] bg-white/75 p-3 text-center text-xs hover:bg-white">
        <input class="hidden" type="file" :disabled="uploading" @change="handleAsset">
        {{ uploading ? `上传中 ${uploadProgress}%` : '上传音频 / 字幕 / 视频素材' }}
      </label>

      <div class="mb-5">
        <div class="mb-2 text-xs font-medium text-[#867351]">已选生成片段</div>
        <div class="space-y-2">
          <button
            v-for="video in selectedVideos"
            :key="video.id"
            type="button"
            class="w-full rounded-lg border border-[#d7c7a8] bg-white p-3 text-left text-xs hover:bg-[#f4ead8]"
            @click="addVideo(video)"
          >
            <div class="truncate font-medium">{{ video.material_filename || video.id }}</div>
            <div class="mt-1 text-[#8a7857]">加入视频轨</div>
          </button>
        </div>
      </div>

      <div>
        <div class="mb-2 text-xs font-medium text-[#867351]">时间线素材</div>
        <div class="space-y-2">
          <button
            v-for="asset in assets"
            :key="asset.id"
            type="button"
            class="w-full rounded-lg border border-[#d7c7a8] bg-white p-3 text-left text-xs hover:bg-[#f4ead8]"
            @click="addAsset(asset)"
          >
            <div class="truncate font-medium">{{ asset.filename }}</div>
            <div class="mt-1 text-[#8a7857]">{{ asset.asset_type }}</div>
          </button>
        </div>
      </div>
    </aside>

    <main class="overflow-auto p-6">
      <div class="mb-5">
        <h2 class="text-xl font-semibold">时间线</h2>
        <p class="mt-1 text-sm text-[#867351]">按轨道排列片段，保存后交给后端编辑服务。</p>
      </div>

      <div class="space-y-5">
        <section v-for="track in tracks" :key="track" class="rounded-lg border border-[#d7c7a8] bg-white/80 p-4">
          <div class="mb-3 flex items-center justify-between">
            <h3 class="text-sm font-semibold">{{ track }} 轨</h3>
            <span class="text-xs text-[#8a7857]">{{ clips.filter((clip) => clip.trackType === track).length }} 段</span>
          </div>

          <div v-if="clips.filter((clip) => clip.trackType === track).length === 0" class="rounded-lg border border-dashed border-[#d7c7a8] p-5 text-sm text-[#867351]">
            暂无片段。
          </div>
          <div v-else class="flex gap-3 overflow-x-auto pb-2">
            <article
              v-for="clip in clips.filter((item) => item.trackType === track)"
              :key="clip.id"
              class="w-56 shrink-0 rounded-lg border border-[#e2d5bf] bg-[#fffaf1] p-3"
            >
              <video v-if="track === 'video' && clipUrl(clip)" class="mb-3 h-28 w-full rounded bg-black object-cover" controls :src="clipUrl(clip)" />
              <div v-else class="mb-3 flex h-28 items-center justify-center rounded bg-[#f2e8d6] text-sm text-[#867351]">{{ track }}</div>
              <div class="truncate text-sm font-medium">{{ clip.label || clip.filename || clip.id }}</div>
              <div class="mt-1 text-xs text-[#8a7857]">{{ Math.round(clip.durationMs / 1000) }} 秒</div>
              <button type="button" class="mt-3 rounded-lg border border-[#d7c7a8] px-3 py-1.5 text-xs hover:bg-[#f4ead8]" @click="removeClip(clip.id)">
                移除
              </button>
            </article>
          </div>
        </section>
      </div>
    </main>
  </section>
</template>
