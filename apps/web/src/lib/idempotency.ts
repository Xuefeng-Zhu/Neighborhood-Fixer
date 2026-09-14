export type CreationAttempt = { key: string; body: string };
/** Same intended payload keeps its key; an explicit edit begins a new request. */
export function creationAttempt(
  payload: unknown,
  previous?: CreationAttempt,
): CreationAttempt {
  const body = JSON.stringify(payload);
  return previous?.body === body
    ? previous
    : { key: crypto.randomUUID(), body };
}
