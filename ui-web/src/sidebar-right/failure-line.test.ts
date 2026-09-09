import { describe, expect, it } from 'vitest'

import { directoryFailureLine, fileFailureLine, humanBytes } from './failure-line.ts'

describe('humanBytes', () => {
  it('scales to the unit a person would use', () => {
    expect(humanBytes(512)).toBe('512 B')
    expect(humanBytes(4096)).toBe('4 KB')
    expect(humanBytes(2 * 1024 * 1024)).toBe('2 MB')
  })
})

describe('fileFailureLine', () => {
  it('speaks about the file, not the transport', () => {
    expect(fileFailureLine({ code: 'workspace-file/not-found', message: 'ENOENT' })).toBe(
      'That file is gone. It may have been moved or deleted.',
    )
    expect(fileFailureLine({ code: 'workspace-file/not-text', message: 'x' })).toContain(
      'not a text file',
    )
  })

  it('names the cap a page ran into', () => {
    const line = fileFailureLine({
      code: 'workspace-file/too-large',
      details: { limit: 2 * 1024 * 1024 },
      message: 'too large',
    })

    expect(line).toContain('2 MB')
  })

  it('hands an unnamed failure through with its own message', () => {
    // A transport-level failure is not something this panel can improve on.
    expect(fileFailureLine({ code: 'workspace-file/unavailable', message: 'socket closed' })).toBe(
      'Read failed: socket closed',
    )
  })
})

describe('directoryFailureLine', () => {
  it('says the same codes in terms of a directory', () => {
    expect(directoryFailureLine({ code: 'workspace-file/not-found', message: 'x' })).toContain(
      'That directory is gone',
    )
    expect(directoryFailureLine({ code: 'workspace-file/not-directory', message: 'x' })).toBe(
      'That is not a directory.',
    )
  })

  it('falls back to the carrier message like the file reader does', () => {
    expect(directoryFailureLine({ code: 'nope', message: 'boom' })).toBe('Read failed: boom')
  })
})
