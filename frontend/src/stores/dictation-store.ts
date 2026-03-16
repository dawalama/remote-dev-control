import { create } from "zustand"

export interface DictationBlock {
  id: string
  text: string
  timestamp: number
}

interface DictationState {
  active: boolean
  blocks: DictationBlock[]
  target: string

  setActive: (active: boolean, target?: string) => void
  addBlock: (text: string) => void
  removeBlock: (id: string) => void
  editBlock: (id: string, text: string) => void
  clearAll: () => void
}

let blockCounter = 0

export const useDictationStore = create<DictationState>((set) => ({
  active: false,
  blocks: [],
  target: "terminal",

  setActive: (active, target) =>
    set((s) => ({
      active,
      target: target || s.target,
      ...(active ? {} : { blocks: [] }),
    })),

  addBlock: (text) => {
    const id = `dict-${++blockCounter}-${Date.now()}`
    set((s) => ({
      blocks: [...s.blocks, { id, text, timestamp: Date.now() }],
    }))
  },

  removeBlock: (id) =>
    set((s) => ({ blocks: s.blocks.filter((b) => b.id !== id) })),

  editBlock: (id, text) =>
    set((s) => ({
      blocks: s.blocks.map((b) => (b.id === id ? { ...b, text } : b)),
    })),

  clearAll: () => set({ blocks: [] }),
}))
