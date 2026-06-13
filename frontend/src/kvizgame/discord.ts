import { DiscordSDK, DiscordSDKMock, patchUrlMappings } from '@discord/embedded-app-sdk';

declare global {
  interface Window {
    __KVIZGAME_CONFIG__?: { clientId: string; proxyTarget: string };
  }
}

// Detect whether we're running inside a Discord Activity iframe.
const isInDiscord = new URLSearchParams(window.location.search).has('frame_id');

// In production the server injects window.__KVIZGAME_CONFIG__ into index.html.
// In local dev (Vite) fall back to .env vars.
const CLIENT_ID =
  window.__KVIZGAME_CONFIG__?.clientId || (import.meta.env.VITE_DISCORD_CLIENT_ID as string);
const PROXY_TARGET =
  window.__KVIZGAME_CONFIG__?.proxyTarget || (import.meta.env.VITE_DISCORD_PROXY_TARGET as string);

export const sdk = isInDiscord
  ? new DiscordSDK(CLIENT_ID)
  : new DiscordSDKMock(CLIENT_ID, 'mock-guild', 'mock-channel', null);

export interface AuthResult {
  userId: string;
  username: string;
  channelId: string;
}

export async function setup(): Promise<AuthResult> {
  await sdk.ready();

  // In local dev, skip the OAuth flow and use env-var mock identities.
  if (!isInDiscord) {
    return {
      userId: import.meta.env.VITE_MOCK_USER_ID || 'dev-p1',
      username: import.meta.env.VITE_MOCK_USERNAME || 'DevPlayer',
      channelId: import.meta.env.VITE_MOCK_CHANNEL_ID || 'mock-channel',
    };
  }

  // Patch fetch + WebSocket to go through Discord's reverse proxy.
  // The prefix must match the URL mapping configured in the Developer Portal.
  patchUrlMappings([{ prefix: '/api', target: PROXY_TARGET }]);

  const { code } = await sdk.commands.authorize({
    client_id: CLIENT_ID,
    response_type: 'code',
    state: '',
    prompt: 'none',
    scope: ['identify'],
  });

  const { access_token } = await fetch('/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  }).then((r) => r.json());

  const auth = await sdk.commands.authenticate({ access_token });

  return {
    userId: auth.user.id,
    username: auth.user.username,
    channelId: sdk.channelId!,
  };
}
