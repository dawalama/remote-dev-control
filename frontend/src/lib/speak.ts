/**
 * Speak text for HomeBase Voice replies.
 * Prefers server TTS (/tts/speak); falls back to browser speechSynthesis.
 */

const SPEAK_PREF_KEY = "rdc_voice_speak_replies"
const UPGRADE_DISMISS_KEY = "rdc_voice_upgrade_banner_dismissed"

let currentAudio: HTMLAudioElement | null = null
let lastServerError: string | null = null

export function getSpeakRepliesEnabled(): boolean {
  try {
    const v = localStorage.getItem(SPEAK_PREF_KEY)
    return v !== "0"
  } catch {
    return true
  }
}

export function setSpeakRepliesEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(SPEAK_PREF_KEY, enabled ? "1" : "0")
  } catch {
    /* ignore */
  }
}

export function getLastServerTtsError(): string | null {
  return lastServerError
}

export function isVoiceUpgradeBannerDismissed(): boolean {
  try {
    return localStorage.getItem(UPGRADE_DISMISS_KEY) === "1"
  } catch {
    return false
  }
}

export function dismissVoiceUpgradeBanner(): void {
  try {
    localStorage.setItem(UPGRADE_DISMISS_KEY, "1")
  } catch {
    /* ignore */
  }
}

export function clearVoiceUpgradeBannerDismiss(): void {
  try {
    localStorage.removeItem(UPGRADE_DISMISS_KEY)
  } catch {
    /* ignore */
  }
}

export function stopSpeaking(): void {
  if (currentAudio) {
    currentAudio.pause()
    currentAudio.src = ""
    currentAudio = null
  }
  if (typeof window !== "undefined" && window.speechSynthesis) {
    window.speechSynthesis.cancel()
  }
}

function truncateForSpeech(text: string, max = 800): string {
  const cleaned = text.replace(/\s+/g, " ").trim()
  if (cleaned.length <= max) return cleaned
  return cleaned.slice(0, max - 1).trimEnd() + "…"
}

async function speakViaServer(text: string): Promise<boolean> {
  lastServerError = null
  const token = localStorage.getItem("rdc_token")
  const resp = await fetch("/tts/speak", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ text }),
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json() as { detail?: string }
      if (body.detail) detail = String(body.detail)
    } catch {
      try {
        detail = (await resp.text()).slice(0, 200) || detail
      } catch {
        /* ignore */
      }
    }
    lastServerError = detail
    return false
  }
  const blob = await resp.blob()
  if (!blob.size) {
    lastServerError = "Empty audio from server TTS"
    return false
  }
  const url = URL.createObjectURL(blob)
  await new Promise<void>((resolve, reject) => {
    const audio = new Audio(url)
    currentAudio = audio
    audio.onended = () => {
      URL.revokeObjectURL(url)
      if (currentAudio === audio) currentAudio = null
      resolve()
    }
    audio.onerror = () => {
      URL.revokeObjectURL(url)
      if (currentAudio === audio) currentAudio = null
      reject(new Error("audio play failed"))
    }
    void audio.play().catch(reject)
  })
  return true
}

function speakViaBrowser(text: string): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || !window.speechSynthesis) {
      resolve()
      return
    }
    window.speechSynthesis.cancel()
    const utter = new SpeechSynthesisUtterance(text)
    utter.rate = 1.05
    utter.onend = () => resolve()
    utter.onerror = () => resolve()
    window.speechSynthesis.speak(utter)
  })
}

/**
 * Speak a reply. Returns which path was used.
 */
export async function speakText(
  text: string,
  opts?: { force?: boolean },
): Promise<"server" | "browser" | "skipped" | "error"> {
  if (!opts?.force && !getSpeakRepliesEnabled()) return "skipped"
  const spoken = truncateForSpeech(text)
  if (!spoken) return "skipped"

  stopSpeaking()

  try {
    const ok = await speakViaServer(spoken)
    if (ok) return "server"
  } catch (err) {
    lastServerError = err instanceof Error ? err.message : "Server TTS failed"
  }

  try {
    await speakViaBrowser(spoken)
    return "browser"
  } catch {
    return "error"
  }
}
