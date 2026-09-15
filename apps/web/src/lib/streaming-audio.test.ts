import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTemporaryAudioPlayback } from './streaming-audio';

class FakeSourceBuffer extends EventTarget {
  updating = false;
  chunks: Uint8Array[] = [];

  appendBuffer(value: ArrayBuffer) {
    this.updating = true;
    this.chunks.push(new Uint8Array(value));
    queueMicrotask(() => {
      this.updating = false;
      this.dispatchEvent(new Event('updateend'));
    });
  }
}

class FakeMediaSource extends EventTarget {
  static instances: FakeMediaSource[] = [];
  static isTypeSupported = vi.fn(() => true);
  readyState: 'closed' | 'open' | 'ended' = 'closed';
  sourceBuffer = new FakeSourceBuffer();
  ended = false;

  constructor() {
    super();
    FakeMediaSource.instances.push(this);
    queueMicrotask(() => {
      this.readyState = 'open';
      this.dispatchEvent(new Event('sourceopen'));
    });
  }

  addSourceBuffer(type: string) {
    expect(type).toBe('audio/mpeg');
    return this.sourceBuffer;
  }

  endOfStream() {
    this.ended = true;
    this.readyState = 'ended';
  }
}

class FakeAudio {
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  pause = vi.fn();
  play = vi.fn(async () => undefined);

  constructor(public src: string) {}
}

afterEach(() => {
  FakeMediaSource.instances = [];
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('temporary audio streaming', () => {
  it('appends MPEG response chunks incrementally without creating a full blob', async () => {
    vi.stubGlobal('MediaSource', FakeMediaSource);
    vi.stubGlobal('Audio', FakeAudio);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:streaming-audio');
    const response = new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(Uint8Array.from([1, 2]));
          controller.enqueue(Uint8Array.from([3, 4]));
          controller.close();
        },
      }),
      { headers: { 'Content-Type': 'audio/mpeg' } },
    );
    const blob = vi.spyOn(response, 'blob');

    const playback = await createTemporaryAudioPlayback(
      response,
      new AbortController().signal,
    );
    await playback.pump;

    expect(blob).not.toHaveBeenCalled();
    expect(playback.objectUrl).toBe('blob:streaming-audio');
    expect(FakeMediaSource.instances[0].sourceBuffer.chunks).toEqual([
      Uint8Array.from([1, 2]),
      Uint8Array.from([3, 4]),
    ]);
    expect(FakeMediaSource.instances[0].ended).toBe(true);
  });

  it('uses a bounded blob fallback for local WAV fixtures', async () => {
    vi.stubGlobal('MediaSource', undefined);
    vi.stubGlobal('Audio', FakeAudio);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:local-wav');
    const response = new Response(Uint8Array.from([5, 6, 7]), {
      headers: { 'Content-Type': 'audio/wav' },
    });

    const playback = await createTemporaryAudioPlayback(
      response,
      new AbortController().signal,
    );
    await playback.pump;

    expect(playback.objectUrl).toBe('blob:local-wav');
    expect(playback.audio).toBeInstanceOf(FakeAudio);
  });

  it('fails closed instead of buffering deployed MPEG without MediaSource', async () => {
    vi.stubGlobal('MediaSource', undefined);
    const response = new Response(Uint8Array.from([5, 6, 7]), {
      headers: { 'Content-Type': 'audio/mpeg' },
    });
    const blob = vi.spyOn(response, 'blob');
    await expect(
      createTemporaryAudioPlayback(response, new AbortController().signal),
    ).rejects.toThrow('cannot stream');
    expect(blob).not.toHaveBeenCalled();
  });

  it('rejects non-audio responses before reading their bodies', async () => {
    const response = new Response('private error detail', {
      headers: { 'Content-Type': 'text/plain' },
    });
    const blob = vi.spyOn(response, 'blob');
    await expect(
      createTemporaryAudioPlayback(response, new AbortController().signal),
    ).rejects.toThrow('unsupported format');
    expect(blob).not.toHaveBeenCalled();
  });
});
