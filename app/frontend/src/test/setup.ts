import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import { FakeEventSource } from './fakeEventSource'

const install = () => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
}

install()
beforeEach(install)
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  install()
})
