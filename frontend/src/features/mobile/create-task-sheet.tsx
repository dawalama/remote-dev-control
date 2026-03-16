import { CreateTaskForm } from "@/features/tasks/create-task-form"
import { Sheet } from "./sheet"

export function CreateTaskSheet({ onClose }: { onClose: () => void }) {
  return (
    <Sheet onClose={onClose} title="Create Task">
      <CreateTaskForm onClose={onClose} />
    </Sheet>
  )
}
