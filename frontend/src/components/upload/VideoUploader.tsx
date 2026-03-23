import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, CheckCircle, Loader2 } from 'lucide-react'
import { uploadVideo, getVideoStreamUrl } from '../../api/upload'
import type { VideoUpload } from '../../types'
import { cn } from '../../lib/utils'

interface Props {
  projectId: string
  upload: VideoUpload | null
  onUploaded: (upload: VideoUpload) => void
}

export default function VideoUploader({ projectId, upload, onUploaded }: Props) {
  const [progress, setProgress] = useState(0)
  const [uploading, setUploading] = useState(false)

  const onDrop = useCallback(async (files: File[]) => {
    if (!files[0]) return
    setUploading(true)
    setProgress(0)
    try {
      const result = await uploadVideo(projectId, files[0], setProgress)
      onUploaded(result)
    } finally {
      setUploading(false)
    }
  }, [projectId, onUploaded])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'video/*': ['.mp4', '.mov', '.avi', '.mkv', '.webm'] },
    maxFiles: 1,
    disabled: uploading,
  })

  return (
    <div className="max-w-2xl mx-auto p-8">
      <div
        {...getRootProps()}
        className={cn(
          'border-2 border-dashed rounded-2xl p-12 text-center transition-all cursor-pointer',
          isDragActive ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-gray-400',
          uploading && 'pointer-events-none opacity-60',
        )}
      >
        <input {...getInputProps()} />
        {uploading ? (
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="text-blue-500 animate-spin" size={48} />
            <p className="text-gray-700">上传中... {progress}%</p>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div className="bg-blue-500 h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
            </div>
          </div>
        ) : upload ? (
          <div className="flex flex-col items-center gap-4">
            <CheckCircle className="text-green-500" size={48} />
            <p className="text-green-700">已上传: {upload.filename}</p>
            <p className="text-sm text-gray-500">拖拽新文件可以重新上传</p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4">
            <Upload className="text-gray-400" size={48} />
            <p className="text-gray-700">拖拽视频到此处，或点击选择文件</p>
            <p className="text-sm text-gray-500">支持 MP4, MOV, AVI, MKV, WebM</p>
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
    </div>
  )
}
