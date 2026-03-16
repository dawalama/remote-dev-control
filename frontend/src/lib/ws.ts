type MessageHandler = (data: unknown) => void

interface WSOptions {
  onOpen?: () => void
  onClose?: () => void
  onError?: (e: Event) => void
  reconnect?: boolean
  reconnectInterval?: number
}

export class ManagedWebSocket {
  private ws: WebSocket | null = null
  private url: string
  private handlers: Map<string, Set<MessageHandler>> = new Map()
  private globalHandlers: Set<MessageHandler> = new Set()
  private options: WSOptions
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private closed = false

  constructor(url: string, options: WSOptions = {}) {
    this.url = url
    this.options = { reconnect: true, reconnectInterval: 3000, ...options }
  }

  connect() {
    this.closed = false
    const token = localStorage.getItem("rdc_token")
    const sep = this.url.includes("?") ? "&" : "?"
    const fullUrl = token ? `${this.url}${sep}token=${token}` : this.url

    // Build absolute WS URL
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
    const base = `${proto}//${window.location.host}`
    const wsUrl = fullUrl.startsWith("ws") ? fullUrl : `${base}${fullUrl}`

    this.ws = new WebSocket(wsUrl)

    this.ws.onopen = () => {
      this.options.onOpen?.()
    }

    this.ws.onmessage = (event) => {
      let data: unknown
      try {
        data = JSON.parse(event.data)
      } catch {
        data = event.data
      }

      // Dispatch to type-specific handlers
      if (data && typeof data === "object" && "type" in data) {
        const type = (data as { type: string }).type
        const typeHandlers = this.handlers.get(type)
        if (typeHandlers) {
          typeHandlers.forEach((h) => h(data))
        }
      }

      // Dispatch to global handlers
      this.globalHandlers.forEach((h) => h(data))
    }

    this.ws.onclose = () => {
      this.options.onClose?.()
      if (!this.closed && this.options.reconnect) {
        this.reconnectTimer = setTimeout(
          () => this.connect(),
          this.options.reconnectInterval,
        )
      }
    }

    this.ws.onerror = (e) => {
      this.options.onError?.(e)
    }
  }

  on(type: string, handler: MessageHandler) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set())
    }
    this.handlers.get(type)!.add(handler)
    return () => this.handlers.get(type)?.delete(handler)
  }

  onMessage(handler: MessageHandler) {
    this.globalHandlers.add(handler)
    return () => this.globalHandlers.delete(handler)
  }

  send(data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof data === "string" ? data : JSON.stringify(data))
    }
  }

  sendRaw(data: ArrayBuffer | Uint8Array) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(data)
    }
  }

  get connected() {
    return this.ws?.readyState === WebSocket.OPEN
  }

  close() {
    this.closed = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
  }
}
