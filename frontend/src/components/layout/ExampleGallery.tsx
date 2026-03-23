import { useEffect, useState } from 'react'
import { ExternalLink, FileImage, FileVideo, Music4, FolderKanban } from 'lucide-react'

import { listExamples } from '../../api/examples'
import type { ExampleCategory, ExampleCategoryResponse, ExampleFile } from '../../types'


function formatSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function AssetPreview({ file }: { file: ExampleFile }) {
  if (file.asset_type === 'image') {
    return <img src={file.url} alt={file.name} className="h-36 w-full rounded-xl object-cover" />
  }

  if (file.asset_type === 'video') {
    return <video src={file.url} controls className="h-36 w-full rounded-xl bg-black object-cover" />
  }

  if (file.asset_type === 'audio') {
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
        <div className="mb-3 flex items-center gap-2 text-emerald-700">
          <Music4 size={18} />
          <span className="text-sm font-medium">音频预览</span>
        </div>
        <audio src={file.url} controls className="w-full" />
      </div>
    )
  }

  return (
    <div className="flex h-36 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-500">
      <FileImage size={24} />
    </div>
  )
}

export default function ExampleGallery() {
  const [categories, setCategories] = useState<ExampleCategory[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listExamples()
      .then((data: ExampleCategoryResponse) => setCategories(data.categories))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="mt-6 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
      <div className="mb-5 flex items-center gap-3">
        <div className="rounded-xl bg-amber-100 p-2 text-amber-700">
          <FolderKanban size={20} />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-gray-900">示例资源</h2>
          <p className="text-sm text-gray-500">这里直接展示 `examples` 目录中的图片、音频和视频。</p>
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-gray-500">正在读取示例文件...</p>
      ) : categories.length === 0 ? (
        <p className="text-sm text-gray-500">还没有示例文件，往 `examples` 目录里放内容后刷新页面即可。</p>
      ) : (
        <div className="space-y-6">
          {categories.map((category) => (
            <section key={category.name}>
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-gray-900">{category.name}</h3>
                <span className="text-xs text-gray-400">{category.files.length} 个文件</span>
              </div>

              {category.files.length === 0 ? (
                <p className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-500">
                  这个分类还没有文件。
                </p>
              ) : (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {category.files.map((file) => (
                    <article key={file.relative_path} className="rounded-2xl border border-gray-200 bg-gray-50 p-3">
                      <AssetPreview file={file} />
                      <div className="mt-3 flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-gray-900">{file.name}</p>
                          <p className="truncate text-xs text-gray-500">{file.relative_path}</p>
                          <p className="mt-1 text-xs text-gray-400">{formatSize(file.size)}</p>
                        </div>
                        <a
                          href={file.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-xs text-gray-700 ring-1 ring-gray-200 transition hover:bg-gray-100"
                        >
                          打开
                          <ExternalLink size={12} />
                        </a>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
