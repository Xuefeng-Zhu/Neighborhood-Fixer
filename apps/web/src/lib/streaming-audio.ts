const ALLOWED_AUDIO_TYPES = new Set(['audio/mpeg', 'audio/wav']);

export interface TemporaryAudioPlayback {
  audio: HTMLAudioElement;
  objectUrl: string;
  /** Resolves after all streamed bytes have been appended to the media source. */
  pump: Promise<void>;
}

function abortError() {
  return new DOMException(
    'Temporary audio playback was stopped.',
    'AbortError',
  );
}

function contentType(response: Response) {
  return (response.headers.get('Content-Type') || '')
    .split(';', 1)[0]
    .trim()
    .toLowerCase();
}

function waitForEvent(
  target: EventTarget,
  success: string,
  failure: string,
  signal: AbortSignal,
) {
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      target.removeEventListener(success, done);
      target.removeEventListener(failure, failed);
      signal.removeEventListener('abort', aborted);
    };
    const done = () => {
      cleanup();
      resolve();
    };
    const failed = () => {
      cleanup();
      reject(new Error('The temporary audio stream could not be decoded.'));
    };
    const aborted = () => {
      cleanup();
      reject(abortError());
    };
    target.addEventListener(success, done, { once: true });
    target.addEventListener(failure, failed, { once: true });
    signal.addEventListener('abort', aborted, { once: true });
    if (signal.aborted) aborted();
  });
}

async function appendChunk(
  sourceBuffer: SourceBuffer,
  chunk: Uint8Array,
  signal: AbortSignal,
) {
  signal.throwIfAborted();
  const updated = waitForEvent(sourceBuffer, 'updateend', 'error', signal);
  // Copy the view so SourceBuffer receives an ordinary detached-safe buffer.
  sourceBuffer.appendBuffer(new Uint8Array(chunk).buffer);
  await updated;
}

/** Consume the fetch body one chunk at a time; no complete audio blob is built. */
export async function appendStreamingMpeg(
  response: Response,
  mediaSource: MediaSource,
  signal: AbortSignal,
) {
  if (!response.body)
    throw new Error('The temporary audio response did not include a stream.');
  if (mediaSource.readyState !== 'open')
    await waitForEvent(mediaSource, 'sourceopen', 'sourceclose', signal);
  signal.throwIfAborted();
  const sourceBuffer = mediaSource.addSourceBuffer('audio/mpeg');
  const reader = response.body.getReader();
  try {
    for (;;) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      if (value?.byteLength) await appendChunk(sourceBuffer, value, signal);
    }
    if (sourceBuffer.updating)
      await waitForEvent(sourceBuffer, 'updateend', 'error', signal);
    signal.throwIfAborted();
    if (mediaSource.readyState === 'open') mediaSource.endOfStream();
  } finally {
    if (signal.aborted) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

/**
 * Start an incremental MPEG MediaSource pipeline. Local WAV fixtures and
 * browsers without MPEG MediaSource support use a bounded blob fallback.
 */
export async function createTemporaryAudioPlayback(
  response: Response,
  signal: AbortSignal,
): Promise<TemporaryAudioPlayback> {
  const mediaType = contentType(response);
  if (!ALLOWED_AUDIO_TYPES.has(mediaType))
    throw new Error('The temporary audio response used an unsupported format.');
  signal.throwIfAborted();
  const MediaSourceClass = globalThis.MediaSource;
  if (mediaType === 'audio/mpeg') {
    if (
      !response.body ||
      typeof MediaSourceClass !== 'function' ||
      !MediaSourceClass.isTypeSupported('audio/mpeg')
    )
      throw new Error(
        'This browser cannot stream the temporary MPEG demo audio.',
      );
    const mediaSource = new MediaSourceClass();
    const objectUrl = URL.createObjectURL(mediaSource);
    return {
      audio: new Audio(objectUrl),
      objectUrl,
      pump: appendStreamingMpeg(response, mediaSource, signal),
    };
  }

  // Only deterministic local fixtures use WAV; deployed MPEG never buffers.
  const audioBlob = await response.blob();
  signal.throwIfAborted();
  if (!audioBlob.size)
    throw new Error('The temporary audio response was empty.');
  const objectUrl = URL.createObjectURL(audioBlob);
  return {
    audio: new Audio(objectUrl),
    objectUrl,
    pump: Promise.resolve(),
  };
}
