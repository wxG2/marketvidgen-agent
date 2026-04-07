import { useState } from 'react'
import { Upload, CheckCircle } from 'lucide-react'
import { getVideoStreamUrl } from '../../api/upload'
import type { VideoUpload } from '../../types'
import { cn } from '../../lib/utils'
import MaterialPickerModal from '../materials/MaterialPickerModal'

interface Props {
  projectId: string
  upload: VideoUpload | null
  onUploaded: (upload: VideoUpload) => void
}

export default function VideoUploader({ projectId, upload, onUploaded }: Props) {
  const [showPicker, setShowPicker] = useState(false)

  return (
    <div className="max-w-2xl mx-auto p-8">
      <div
        onClick={() => setShowPicker(true)}
        className={cn(
          'border-2 border-dashed rounded-2xl p-12 text-center transition-all cursor-pointer',
          'border-gray-300 hover:border-gray-400',
        )}
      >
        {upload ? (
          <div className="flex flex-col items-center gap-4">
            <CheckCircle className="text-green-500" size={48} />
            <p className="text-green-700">已上传: {upload.filename}</p>
            <p className="text-sm text-gray-500">点击打开“添加素材”弹窗，可重新选择或上传视频</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4">
            <Upload className="text-gray-400" size={48} />
            <p className="text-gray-700">点击打开“添加素材”弹窗上传或选择文件</p>
            <p className="text-sm text-gray-500">弹窗支持：素材库、视频库、上传图片和视频</p>
          </div>
        )}
      </div>

      {upload && (
        <div className="mt-6 rounded-xl overflow-hidden border border-gray-200">
          <video
            src={getVideoStreamUrl(upload.id)}
            controls
            className="w-full max-h-[400px]"
          />
        </div>
      )}

      {showPicker && (
        <MaterialPickerModal
          projectId={projectId}
          onClose={() => setShowPicker(false)}
          onMaterialsSelected={() => {}}
          onVideoSelected={(selected) => {
            onUploaded(selected)
            setShowPicker(false)
          }}
        />
      )}
    </div>
  )
}
