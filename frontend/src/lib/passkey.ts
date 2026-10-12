// Passkeys (WebAuthn) in the browser: the server speaks JSON with base64url fields, the browser wants buffers.
import { post } from "./api";

export const passkeysSupported = (): boolean => typeof window !== "undefined" && !!window.PublicKeyCredential && !!navigator.credentials;

function toBuffer(s: string): ArrayBuffer {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function toBase64Url(buf: ArrayBuffer | null | undefined): string | null {
  if (!buf) return null;
  let bin = "";
  for (const b of new Uint8Array(buf)) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

interface Descriptor {
  id: string;
  [k: string]: unknown;
}

/** What went wrong, in words: the user closing the prompt is not an error worth a stack trace. */
export function passkeyError(e: unknown): string {
  if (e instanceof DOMException) {
    if (e.name === "NotAllowedError" || e.name === "AbortError") return "Operazione annullata o scaduta.";
    if (e.name === "InvalidStateError") return "Questa passkey è già registrata.";
    if (e.name === "SecurityError") return "Le passkey funzionano solo su un indirizzo HTTPS (o localhost).";
  }
  return e instanceof Error ? e.message : String(e);
}

type Options = { state: string; options: Record<string, any> };

/** Ask the browser (or the password manager) to make a passkey from the server's options; returns it as the server wants it. */
async function createCredential(options: Record<string, any>): Promise<Record<string, unknown>> {
  const credential = (await navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: toBuffer(options.challenge),
      user: { ...options.user, id: toBuffer(options.user.id) },
      excludeCredentials: ((options.excludeCredentials ?? []) as Descriptor[]).map((c) => ({ ...c, id: toBuffer(c.id) })),
    } as PublicKeyCredentialCreationOptions,
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error("Operazione annullata.");
  const r = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: toBase64Url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: { clientDataJSON: toBase64Url(r.clientDataJSON), attestationObject: toBase64Url(r.attestationObject), transports: r.getTransports?.() ?? [] },
  };
}

/** Make a passkey (settings); the server remembers it under `name`. */
export async function registerPasskey(name: string): Promise<void> {
  const { state, options } = await post<Options>("/api/account/passkeys/options");
  await post("/api/account/passkeys", { state, name, credential: await createCredential(options) });
}

/** Accept an invite: make the account `username` with a passkey; the server signs it in. */
export async function acceptInvite(token: string, username: string): Promise<void> {
  const path = `/api/invite/${encodeURIComponent(token)}`;
  const { state, options } = await post<Options>(`${path}/options`, { username });
  await post(`${path}/accept`, { state, username, credential: await createCredential(options) });
}

/** Sign in with a passkey: the browser (or the password manager) offers the ones it holds for this site. */
export async function signInWithPasskey(): Promise<void> {
  const { state, options } = await post<Options>("/api/auth/passkey/options");
  const credential = (await navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: toBuffer(options.challenge),
      allowCredentials: ((options.allowCredentials ?? []) as Descriptor[]).map((c) => ({ ...c, id: toBuffer(c.id) })),
    } as PublicKeyCredentialRequestOptions,
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error("Operazione annullata.");
  const r = credential.response as AuthenticatorAssertionResponse;
  await post("/api/auth/passkey/login", {
    state,
    credential: {
      id: credential.id,
      rawId: toBase64Url(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment,
      clientExtensionResults: credential.getClientExtensionResults(),
      response: {
        clientDataJSON: toBase64Url(r.clientDataJSON),
        authenticatorData: toBase64Url(r.authenticatorData),
        signature: toBase64Url(r.signature),
        userHandle: toBase64Url(r.userHandle),
      },
    },
  });
}
