// Where the engine lives. Served by the helper app itself, the dashboard talks to its
// own origin. Built as the hosted website, it talks to the helper on the visitor's own
// machine: sensing needs this machine's WiFi card and microphone, which no server
// elsewhere can reach.

export const HOSTED = import.meta.env.VITE_HOSTED === "1";
export const ENGINE_PORT = 8765;
const ORIGIN = HOSTED ? `http://127.0.0.1:${ENGINE_PORT}` : "";

export const RELEASES = "https://github.com/devadit1515/wifi-motion-radar/releases/latest";
export const DOWNLOAD = `${RELEASES}/download/MotionRadar-windows.zip`;
export const REPO = "https://github.com/devadit1515/wifi-motion-radar";

export function engineUrl(path: string): string {
  return ORIGIN + path;
}

export function engineWs(path: string): string {
  if (HOSTED) return `ws://127.0.0.1:${ENGINE_PORT}${path}`;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}
