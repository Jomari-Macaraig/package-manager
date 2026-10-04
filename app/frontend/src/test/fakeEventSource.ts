type Listener = (e: MessageEvent) => void

export class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []
  static get last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }

  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSED = 2
  readyState = 0
  url: string
  onerror: ((e: Event) => void) | null = null
  listeners = new Map<string, Listener[]>()

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn])
  }

  removeEventListener(type: string, fn: Listener) {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((l) => l !== fn),
    )
  }

  close() {
    this.readyState = FakeEventSource.CLOSED
  }

  /** Test helper: deliver a named event. */
  emit(type: string, data: unknown, id?: number | string) {
    if (this.readyState === FakeEventSource.CLOSED) return
    this.readyState = FakeEventSource.OPEN
    const ev = new MessageEvent(type, {
      data: typeof data === 'string' ? data : JSON.stringify(data),
      lastEventId: id != null ? String(id) : '',
    })
    for (const l of this.listeners.get(type) ?? []) l(ev)
  }

  /** Test helper: simulate a connection error. */
  fail() {
    if (this.readyState === FakeEventSource.CLOSED) return
    const ev = new Event('error')
    this.onerror?.(ev)
    for (const l of this.listeners.get('error') ?? []) l(ev as MessageEvent)
  }

  listenerTypes(): string[] {
    return [...this.listeners.entries()].filter(([, v]) => v.length).map(([k]) => k)
  }
}
