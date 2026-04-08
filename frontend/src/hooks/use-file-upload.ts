import { useCallback } from "react"
import { api } from "@/lib/api"
import { useProjectStore } from "@/stores/project-store"
import { useChannelStore } from "@/stores/channel-store"
import { useUIStore } from "@/stores/ui-store"

/**
 * Hook for uploading files to the project context from chat.
 * Handles the upload API call and posts a notification to the channel.
 */
export function useFileUpload() {
  const currentProject = useProjectStore((s) => s.currentProject)
  const activeChannelId = useChannelStore((s) => s.activeChannelId)
  const postMessage = useChannelStore((s) => s.postMessage)
  const toast = useUIStore((s) => s.toast)

  const upload = useCallback(async (file: File) => {
    if (!activeChannelId) {
      toast("Select a workstream first", "warning")
      return
    }

    const project = currentProject !== "all" ? currentProject : "upload"

    const form = new FormData()
    form.append("file", file)
    form.append("project", project)
    form.append("description", file.name)

    try {
      await api("/context/upload", {
        method: "POST",
        body: form,
      })

      // Post notification to channel
      await postMessage(activeChannelId, `Attached: ${file.name}`, "user", {
        type: "file_attached",
        filename: file.name,
        size: file.size,
        mime: file.type,
      })

      toast(`Uploaded: ${file.name}`, "success")
    } catch {
      toast("File upload failed", "error")
    }
  }, [currentProject, activeChannelId, postMessage, toast])

  const uploadMultiple = useCallback(async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      await upload(file)
    }
  }, [upload])

  return { upload, uploadMultiple }
}
