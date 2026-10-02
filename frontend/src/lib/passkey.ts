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

/** Make a passkey (settings); the server remembers it under `name`. */
export async function registerPasskey(name: string): Promise<void> {
  const { state, options } = await post<{ state: string; options: Record<string, any> }>("/api/account/passkeys/options");
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
  await post("/api/account/passkeys", {
    state,
    name,
    credential: {
      id: credential.id,
      rawId: toBase64Url(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment,
      clientExtensionResults: credential.getClientExtensionResults(),
      response: { clientDataJSON: toBase64Url(r.clientDataJSON), attestationObject: toBase64Url(r.attestationObject), transports: r.getTransports?.() ?? [] },
    },
  });
}

/** Sign in with a passkey: the browser (or the password manager) offers the ones it holds for this site. */
export async function signInWithPasskey(): Promise<void> {
  const { state, options } = await post<{ state: string; options: Record<string, any> }>("/api/auth/passkey/options");
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
